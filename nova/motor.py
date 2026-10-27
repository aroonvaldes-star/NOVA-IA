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
import shutil
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

    def disponible(self) -> bool:
        return True

    def chat(self, mensajes: list[dict], al_token: AlToken | None = None, temperatura: float = 0.6,
             max_tokens: int = 1024) -> str:
        raise NotImplementedError

    def detener(self) -> None:
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
        self._proceso: subprocess.Popen | None = None
        self._trabajo = None
        self._ultimo_uso = time.monotonic()
        self._cerrojo = threading.Lock()
        self._vigilante: threading.Thread | None = None

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
            puerto = self.endpoint.rsplit(":", 1)[-1]
            args = [
                str(self.binario), "-m", str(self.ruta_modelo), "--host", "127.0.0.1", "--port", puerto,
                "-c", str(self.config.context), "-ngl", str(self.config.gpu_layers), "--alias", "nova",
            ]
            log = open(self.config.rutas.estado / "llama-server.log", "ab")
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
            limite = time.monotonic() + 120
            while time.monotonic() < limite:
                if self._proceso.poll() is not None:
                    raise RuntimeError("llama-server se cerró al arrancar; revisa estado/llama-server.log")
                if self._responde():
                    break
                time.sleep(0.5)
            else:
                self.detener()
                raise RuntimeError("llama-server no respondió en 2 minutos")
            if not self._vigilante or not self._vigilante.is_alive():
                self._vigilante = threading.Thread(target=self._vigilar, daemon=True)
                self._vigilante.start()

    def _vigilar(self) -> None:
        while self._proceso and self._proceso.poll() is None:
            time.sleep(15)
            if time.monotonic() - self._ultimo_uso > self.config.server_idle_seconds:
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
            "model": "nova", "messages": mensajes, "stream": True,
            "temperature": temperatura, "max_tokens": max_tokens,
        }).encode()
        peticion = urllib.request.Request(self.endpoint + "/v1/chat/completions", data=cuerpo,
                                          headers={"Content-Type": "application/json"})
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
                    delta = json.loads(dato)["choices"][0]["delta"].get("content") or ""
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    texto.append(delta)
                    if al_token:
                        al_token(delta)
                self._ultimo_uso = time.monotonic()
        return "".join(texto)

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
                return modelos[0]
    return None


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
