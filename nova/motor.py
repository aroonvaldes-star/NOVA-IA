"""Motor de inferencia de NOVA.

NOVA trae su propio motor: lanza `llama-server` (llama.cpp oficial) solo
cuando hace falta, lo escucha únicamente en 127.0.0.1 y lo detiene tras un
rato sin uso para liberar RAM y VRAM. En Windows el proceso vive dentro de un
Job Object con KILL_ON_JOB_CLOSE: si NOVA se cierra o falla, el motor muere
con ella y no deja la VRAM ocupada.

Si no hay modelo instalado, se usa el motor simulado, que lo dice con
honestidad en vez de inventar respuestas.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterable

from .config import Config

AlToken = Callable[[str], None]


class Motor:
    nombre = "base"
    modelo = ""
    metricas: dict = {}

    def disponible(self) -> bool:
        return True

    def chat(self, mensajes: list[dict], al_token: AlToken | None = None, temperatura: float = 0.6,
             max_tokens: int = 1024) -> str:
        raise NotImplementedError

    def detener(self) -> None:
        pass

    def precalentar(self, sistema: str = "") -> None:
        pass


class MotorSimulado(Motor):
    """Motor sin modelo. Con `guion` devuelve respuestas fijas (pruebas)."""

    nombre = "simulado"
    modelo = "ninguno"

    def __init__(self, guion: Iterable[str] | None = None):
        self.guion = list(guion or [])
        self.recibidos: list[list[dict]] = []

    def chat(self, mensajes, al_token=None, temperatura=0.6, max_tokens=1024) -> str:
        self.recibidos.append([dict(m) for m in mensajes])
        if self.guion:
            texto = self.guion.pop(0)
        else:
            texto = ("FINAL: Todavía no tengo un modelo de lenguaje instalado, así que no puedo pensar una "
                     "respuesta de verdad. Instala uno con `nova modelo instalar archivo.gguf` y vuelvo a estar "
                     "completa. Mientras tanto sí puedo guardar notas, recordar lo que me digas y buscar en internet.")
        if al_token:
            for trozo in _trozos(texto):
                al_token(trozo)
        return texto


def _trozos(texto: str, n: int = 12) -> Iterable[str]:
    for i in range(0, len(texto), n):
        yield texto[i : i + n]


# --------------------------------------------------------------- Job Object

class _TrabajoWindows:
    """Job Object de Windows con KILL_ON_JOB_CLOSE (vía ctypes, sin dependencias)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        # Los HANDLE son punteros: sin restype/argtypes ctypes los cortaría a 32 bits.
        self.k32.CreateJobObjectW.restype = wintypes.HANDLE
        self.k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.k32.SetInformationJobObject.restype = wintypes.BOOL
        self.k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.k32.AssignProcessToJobObject.restype = wintypes.BOOL
        self.handle = self.k32.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW falló")

        class LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("R", "W", "O", "RB", "WB", "OB")]

        class EXT(ctypes.Structure):
            _fields_ = [
                ("Basic", LIMIT), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = EXT()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject falló")

    def asignar(self, proceso: subprocess.Popen) -> None:
        if not self.k32.AssignProcessToJobObject(self.handle, int(proceso._handle)):  # type: ignore[attr-defined]
            raise OSError(self.ctypes.get_last_error(), "AssignProcessToJobObject falló")


# ------------------------------------------------------------------ llama.cpp

class MotorLlama(Motor):
    nombre = "llama.cpp"

    def __init__(self, config: Config):
        self.config = config
        self.endpoint = config.endpoint.rstrip("/")
        self.binario = buscar_binario(config)
        self.ruta_modelo = buscar_modelo(config)
        self.modelo = self.ruta_modelo.name if self.ruta_modelo else ""
        self.mmproj = buscar_mmproj(config, self.ruta_modelo)
        self.vision = bool(self.mmproj)
        self._proceso: subprocess.Popen | None = None
        self._trabajo = None
        self._ultimo_uso = time.monotonic()
        self._cerrojo = threading.Lock()
        self._vigilante: threading.Thread | None = None
        self.metricas: dict = {}  # tiempos de la última respuesta (los manda llama-server)
        self.ajuste = ""  # cómo acomodó el modelo en la memoria de video

    def disponible(self) -> bool:
        if self._responde():
            return True
        return bool(self.config.manage_server and self.binario and self.ruta_modelo)

    def _responde(self) -> bool:
        try:
            with urllib.request.urlopen(self.endpoint + "/health", timeout=1.5) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def asegurar(self) -> None:
        with self._cerrojo:
            self._ultimo_uso = time.monotonic()
            if self._responde():
                return
            if not (self.config.manage_server and self.binario and self.ruta_modelo):
                raise RuntimeError("No hay motor de IA listo: falta llama-server o un modelo .gguf.")
            base, extras = self._argumentos()
            # Si una versión vieja de llama-server no conoce algún extra, arranca sin ellos.
            for intento, args in enumerate((base + extras, base) if extras else (base,)):
                if self._arrancar(args):
                    break
                if intento == 0 and extras:
                    self.vision = False
            else:
                raise RuntimeError("llama-server se cerró al arrancar; revisa estado/llama-server.log")
            if not self._vigilante or not self._vigilante.is_alive():
                self._vigilante = threading.Thread(target=self._vigilar, daemon=True)
                self._vigilante.start()

    def _argumentos(self) -> tuple[list[str], list[str]]:
        puerto = self.endpoint.rsplit(":", 1)[-1]
        contexto, capas = self._acomodar()
        base = [
            str(self.binario), "-m", str(self.ruta_modelo), "--host", "127.0.0.1", "--port", puerto,
            "-c", str(contexto), "-ngl", str(capas), "--alias", "nova",
            "-np", "1",  # una sola plática a la vez: toda la memoria para ella
        ]
        extras: list[str] = []
        if self.config.kv_cache:
            # Caché de contexto en 8 bits: cabe el doble de conversación en la misma memoria de video.
            extras += ["-fa", "on", "-ctk", self.config.kv_cache, "-ctv", self.config.kv_cache]
        if self.mmproj:
            extras += ["--mmproj", str(self.mmproj), "--image-max-tokens", str(self.config.vision_max_tokens)]
            if not self.config.vision_en_gpu:
                extras.append("--no-mmproj-offload")
        return base, extras

    def _acomodar(self) -> tuple[int, int]:
        """Contexto y capas en GPU que caben en la memoria de video libre.

        Si el modelo no cabe completo, Windows lo pasa en silencio a la memoria normal y
        cada palabra tarda muchísimo. Mejor un contexto más corto, o dejar algunas capas
        en el procesador, que es mucho más rápido que eso.
        """
        contexto, capas = int(self.config.context), int(self.config.gpu_layers)
        libre = vram_libre_mb()
        if libre is None or capas <= 0 or not self.ruta_modelo:
            self.ajuste = ""
            return contexto, capas
        try:
            modelo_mb = self.ruta_modelo.stat().st_size / 2**20
        except OSError:
            return contexto, capas
        por_token = 0.08 if self.config.kv_cache else 0.15  # MB por token de contexto en un modelo 4B
        margen = 700  # búferes de cálculo de CUDA y lo que pide Windows mientras tanto
        while contexto > 4096 and modelo_mb + contexto * por_token + margen > libre:
            contexto //= 2
        sobra = libre - contexto * por_token - margen
        if sobra < modelo_mb:
            total = capas_del_modelo(self.ruta_modelo) or 36
            capas = max(0, min(capas, int(total * sobra / modelo_mb)))
        self.ajuste = (f"{libre:.0f} MB libres de video: contexto {contexto}, "
                       + ("modelo completo en la GPU" if capas >= (capas_del_modelo(self.ruta_modelo) or 36) else f"{capas} capas en la GPU"))
        return contexto, capas

    def _arrancar(self, args: list[str]) -> bool:
        log = open(self.config.rutas.estado / "llama-server.log", "ab")
        log.write(("\n--- " + time.strftime("%Y-%m-%d %H:%M:%S") + " " + " ".join(args[1:]) + "\n"
                   + (f"--- {self.ajuste}\n" if self.ajuste else "")).encode())
        log.flush()
        self._proceso = subprocess.Popen(
            args, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            creationflags=0x08000000 if sys.platform == "win32" else 0,
        )
        if sys.platform == "win32":
            try:
                self._trabajo = _TrabajoWindows()
                self._trabajo.asignar(self._proceso)
            except OSError:
                self._trabajo = None
        limite = time.monotonic() + 180
        while time.monotonic() < limite:
            if self._proceso.poll() is not None:
                return False
            if self._responde():
                return True
            time.sleep(0.5)
        self.detener()
        raise RuntimeError("llama-server no respondió en 3 minutos")

    def _vigilar(self) -> None:
        while self._proceso and self._proceso.poll() is None:
            time.sleep(15)
            espera = float(self.config.server_idle_seconds)
            if espera > 0 and time.monotonic() - self._ultimo_uso > espera:
                self.detener()
                return

    def detener(self) -> None:
        p = self._proceso
        self._proceso = None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()

    def chat(self, mensajes, al_token=None, temperatura=0.6, max_tokens=1024) -> str:
        self.asegurar()
        cuerpo = json.dumps({
            "model": self._nombre_api(), "messages": mensajes, "stream": True,
            "temperature": temperatura, "max_tokens": max_tokens, **self._extras_peticion(),
        }).encode()
        inicio, primero = time.monotonic(), None
        self.metricas = {}
        peticion = urllib.request.Request(self._url_chat(), data=cuerpo,
                                          headers={"Content-Type": "application/json", **self._cabeceras()})
        texto = []
        with urllib.request.urlopen(peticion, timeout=600) as r:
            for linea in r:
                linea = linea.decode("utf-8", "replace").strip()
                if not linea.startswith("data:"):
                    continue
                dato = linea[5:].strip()
                if dato == "[DONE]":
                    break
                try:
                    trozo = json.loads(dato)
                    if trozo.get("timings"):
                        self.metricas = dict(trozo["timings"])
                    delta = (trozo.get("choices") or [{}])[0].get("delta", {}).get("content") or ""
                except (ValueError, KeyError, IndexError, AttributeError):
                    continue
                if delta:
                    primero = primero or time.monotonic()
                    texto.append(delta)
                    if al_token:
                        al_token(delta)
                self._ultimo_uso = time.monotonic()
        self.metricas.update(segundos=round(time.monotonic() - inicio, 2),
                             primera_palabra=round((primero or time.monotonic()) - inicio, 2))
        return "".join(texto)

    def _extras_peticion(self) -> dict:
        # llama-server reutiliza lo ya leído del inicio del prompt (personalidad, herramientas).
        return {"cache_prompt": True}

    def precalentar(self, sistema: str = "") -> None:
        """Carga el modelo en la GPU y deja leído el inicio del prompt, para que la primera respuesta no espere."""
        mensajes = [{"role": "system", "content": sistema or "Eres NOVA."}, {"role": "user", "content": "hola"}]
        self.chat(mensajes, max_tokens=1, temperatura=0)

    def _cabeceras(self) -> dict:
        return {}

    def _url_chat(self) -> str:
        # Acepta la raíz del servidor (llama-server) o una base que ya termina en /v1 (OpenAI, Oracle…).
        return self.endpoint + ("/chat/completions" if self.endpoint.endswith("/v1") else "/v1/chat/completions")

    def _nombre_api(self) -> str:
        return "nova"

    def embeddings(self, textos: list[str]) -> list[list[float]] | None:
        """Embeddings si el servidor los ofrece (llama-server --embedding). Si no, None."""
        try:
            self.asegurar()
            cuerpo = json.dumps({"model": "nova", "input": textos}).encode()
            req = urllib.request.Request(self.endpoint + "/v1/embeddings", data=cuerpo, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return [d["embedding"] for d in json.load(r)["data"]]
        except (urllib.error.URLError, OSError, ValueError, KeyError, RuntimeError):
            return None


class MotorRemoto(MotorLlama):
    """El «cerebro grande»: un llama-server en otro equipo (por ejemplo, Oracle Cloud por Tailscale).

    Solo se usa cuando lo pides («piénsalo bien: …»). No lo arranca ni lo apaga NOVA.
    """

    nombre = "cerebro grande"

    def __init__(self, config: Config):
        self.config = config
        self.endpoint = config.cerebro_endpoint.rstrip("/")
        self.binario = None
        self.ruta_modelo = None
        self.modelo = config.cerebro_modelo or "remoto"
        self.mmproj = None
        self.vision = False
        self.metricas = {}
        self.ajuste = ""
        self._proceso = None
        self._trabajo = None
        self._ultimo_uso = time.monotonic()
        self._cerrojo = threading.Lock()
        self._vigilante = None

    def _cabeceras(self) -> dict:
        return {"Authorization": f"Bearer {self.config.cerebro_clave}"} if self.config.cerebro_clave else {}

    def _nombre_api(self) -> str:
        return self.config.cerebro_api_modelo or "nova-cerebro"

    def _extras_peticion(self) -> dict:
        # Los servicios en la nube rechazan campos que no conocen; solo lo que sabemos que aceptan.
        if "groq.com" in self.endpoint and "gpt-oss" in self._nombre_api():
            return {"reasoning_effort": "low"}  # piensa poco antes de contestar: responde en un par de segundos
        return {}

    def precalentar(self, sistema: str = "") -> None:
        pass  # en la nube ya está cargado; no gastes peticiones

    def _responde(self) -> bool:
        if self.endpoint.endswith("/v1"):
            return True  # servicios en la nube: sin /health; si fallan, la llamada lo dirá
        try:
            req = urllib.request.Request(self.endpoint + "/health", headers=self._cabeceras())
            with urllib.request.urlopen(req, timeout=4) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def disponible(self) -> bool:
        return self._responde()

    def asegurar(self) -> None:
        if not self._responde():
            raise RuntimeError(f"El cerebro grande ({self.endpoint}) no responde. ¿Está encendido el servidor y conectado Tailscale?")

    def detener(self) -> None:
        pass


def vram_libre_mb() -> float | None:
    """Memoria de video libre en la GPU NVIDIA, en MB (None si no hay nvidia-smi)."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return None
    try:
        p = subprocess.run([smi, "--query-gpu=memory.free", "--format=csv,noheader,nounits"], capture_output=True,
                           text=True, timeout=10, creationflags=0x08000000 if sys.platform == "win32" else 0)
        return float(p.stdout.strip().splitlines()[0]) if p.returncode == 0 and p.stdout.strip() else None
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def capas_del_modelo(ruta: Path) -> int | None:
    """Cuántas capas tiene un GGUF (clave «<arquitectura>.block_count» de su cabecera)."""
    try:
        with open(ruta, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            version, _tensores, claves = struct.unpack("<IQQ", f.read(20))
            if version < 2:
                return None
            for _ in range(min(claves, 200)):
                (n,) = struct.unpack("<Q", f.read(8))
                clave = f.read(n).decode("utf-8", "replace")
                (tipo,) = struct.unpack("<I", f.read(4))
                valor = _leer_valor_gguf(f, tipo)
                if clave.endswith(".block_count"):
                    return int(valor)
    except (OSError, struct.error, ValueError):
        return None
    return None


_GGUF_FIJOS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


def _leer_valor_gguf(f, tipo: int):
    if tipo in _GGUF_FIJOS:
        formato = _GGUF_FIJOS[tipo]
        return struct.unpack(formato, f.read(struct.calcsize(formato)))[0]
    if tipo == 8:  # texto
        (n,) = struct.unpack("<Q", f.read(8))
        return f.read(n)
    if tipo == 9:  # lista: tipo, cuántos, valores
        sub, n = struct.unpack("<IQ", f.read(12))
        if sub in _GGUF_FIJOS:
            f.seek(struct.calcsize(_GGUF_FIJOS[sub]) * n, 1)
        else:
            for _ in range(n):
                _leer_valor_gguf(f, sub)
        return None
    raise ValueError(f"tipo GGUF desconocido {tipo}")


def buscar_binario(config: Config) -> Path | None:
    nombre = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    candidatos = []
    if config.server_binary:
        candidatos.append(Path(os.path.expandvars(config.server_binary)))
    candidatos += [config.rutas.programa / "llama" / nombre, config.rutas.datos / "llama" / nombre]
    for c in candidatos:
        if c.is_file():
            return c
    en_path = shutil.which(nombre)
    return Path(en_path) if en_path else None


def buscar_modelo(config: Config) -> Path | None:
    if config.chat_model:
        p = Path(os.path.expandvars(config.chat_model))
        if not p.is_absolute():
            p = config.rutas.modelos / p
        return p if p.is_file() else None
    for carpeta in (config.rutas.modelos, config.rutas.programa / "models"):
        if carpeta.is_dir():
            modelos = sorted(m for m in carpeta.glob("*.gguf") if "mmproj" not in m.name.lower() and "embed" not in m.name.lower())
            if modelos:
                # Con visión instalada, prefiere el modelo que tiene su proyector de imágenes.
                if config.vision:
                    for m in modelos:
                        if buscar_mmproj(config, m):
                            return m
                return modelos[0]
    return None


def buscar_mmproj(config: Config, modelo: Path | None) -> Path | None:
    """El proyector de imágenes (mmproj) que acompaña al modelo, si lo hay: le da vista."""
    if not modelo or not config.vision:
        return None
    if config.vision_mmproj:
        p = Path(os.path.expandvars(config.vision_mmproj))
        if not p.is_absolute():
            p = config.rutas.modelos / p
        return p if p.is_file() else None
    candidatos = sorted(m for m in modelo.parent.glob("*.gguf") if "mmproj" in m.name.lower())
    if not candidatos:
        return None
    clave = _clave_modelo(modelo.name)
    for c in candidatos:
        if clave and clave in _clave_modelo(c.name):
            return c
    # Proyectores con nombre genérico (mmproj-F16.gguf): solo si hay uno y el modelo es de visión.
    if len(candidatos) == 1 and re.search(r"vl|vision|gemma-?3", modelo.name.lower()):
        return candidatos[0]
    return None


def _clave_modelo(nombre: str) -> str:
    """'Qwen3-VL-4B-Instruct-Q4_K_M.gguf' y 'mmproj-Qwen3VL-4B-Instruct-F16.gguf' -> 'qwen3vl4b'."""
    plano = re.sub(r"[^a-z0-9]", "", nombre.lower().replace("mmproj", ""))
    m = re.match(r"([a-z]+\d*(?:vl)?\d+b)", plano)
    return m.group(1) if m else ""


def es_gguf(ruta: Path) -> bool:
    try:
        with open(ruta, "rb") as f:
            return f.read(4) == b"GGUF"
    except OSError:
        return False


def elegir(config: Config) -> Motor:
    if config.motor == "simulado":
        return MotorSimulado()
    llama = MotorLlama(config)
    if config.motor == "llama" or llama.disponible():
        return llama
    return MotorSimulado()
