"""`nova diagnostico`: revisa el equipo pieza por pieza y deja un reporte.

Pensado para la primera prueba en Windows: cada línea dice si algo está bien
(✔), si es un aviso (!) o si falla (✘), con una pista de cómo arreglarlo. El
reporte se guarda en la carpeta de estado para poder compartirlo.
"""

from __future__ import annotations

import datetime as dt
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import time

from . import __version__
from .config import Config, cargar

BIEN, AVISO, MAL = "✔", "!", "✘"


class Reporte:
    def __init__(self) -> None:
        self.lineas: list[str] = []
        self.fallas = 0

    def __call__(self, marca: str, texto: str, pista: str = "") -> None:
        linea = f" {marca} {texto}" + (f"\n     → {pista}" if pista else "")
        self.lineas.append(linea)
        if marca == MAL:
            self.fallas += 1
        print(linea, flush=True)

    def titulo(self, texto: str) -> None:
        self.lineas.append(f"\n{texto}")
        print(f"\n{texto}", flush=True)


def _correr(args: list[str], timeout: int = 20) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
                              creationflags=0x08000000 if sys.platform == "win32" else 0)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _puerto_libre(puerto: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", puerto))
            return True
        except OSError:
            return False


def diagnosticar(config: Config | None = None, rapido: bool = False) -> Reporte:
    config = config or cargar()
    config.rutas.crear()
    r = Reporte()
    r.titulo(f"NOVA {__version__} · diagnóstico {dt.datetime.now():%Y-%m-%d %H:%M}")

    r.titulo("Sistema")
    r(BIEN, f"{platform.system()} {platform.release()} ({platform.version()}), {platform.machine()}")
    if sys.version_info >= (3, 11):
        r(BIEN, f"Python {platform.python_version()}")
    else:
        r(MAL, f"Python {platform.python_version()}", "NOVA necesita Python 3.11 o más nuevo.")
    try:
        sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        r(BIEN, "SQLite con búsqueda de texto completo (FTS5)")
    except sqlite3.OperationalError:
        r(MAL, "Tu SQLite no trae FTS5", "Instala Python desde python.org (trae FTS5).")
    r(BIEN, f"Datos en {config.rutas.datos}")

    r.titulo("Tarjeta gráfica")
    smi = shutil.which("nvidia-smi")
    if smi:
        p = _correr([smi, "--query-gpu=name,memory.total,memory.used,driver_version", "--format=csv,noheader"])
        if p and p.returncode == 0 and p.stdout.strip():
            for linea in p.stdout.strip().splitlines():
                r(BIEN, "NVIDIA: " + linea.strip())
        else:
            r(AVISO, "nvidia-smi no respondió", "Actualiza el controlador de NVIDIA.")
    else:
        r(AVISO, "No encontré nvidia-smi", "Sin controlador de NVIDIA el motor usará Vulkan o el procesador (más lento).")

    r.titulo("Motor de IA (llama.cpp)")
    from .motor import MotorLlama, buscar_binario, buscar_modelo, es_gguf

    binario = buscar_binario(config)
    if binario:
        p = _correr([str(binario), "--version"])
        version = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-1] if p else "no respondió"
        r(BIEN if p and p.returncode == 0 else MAL, f"llama-server: {binario}", "" if p and p.returncode == 0 else
          f"No arranca ({version}). Si es la versión CUDA, falta el paquete cudart o el controlador de NVIDIA.")
        if p and p.returncode == 0:
            r(BIEN, "Versión: " + version)
    else:
        r(MAL, "No encontré llama-server", f"Ejecuta instalar.bat o copia llama.cpp a {config.rutas.datos / 'llama'}.")
    modelo = buscar_modelo(config)
    if modelo and es_gguf(modelo):
        r(BIEN, f"Modelo: {modelo.name} ({modelo.stat().st_size / 1e9:.2f} GB)")
    elif modelo:
        r(MAL, f"{modelo.name} no es un GGUF válido", "Vuelve a descargarlo; probablemente quedó incompleto.")
    else:
        r(MAL, "No hay modelo instalado", "Ejecuta instalar.bat o usa: nova modelo instalar archivo.gguf")
    from .motor import buscar_mmproj

    mmproj = buscar_mmproj(config, modelo) if modelo else None
    if mmproj:
        r(BIEN, f"Vista: {mmproj.name} (puede ver imágenes y tu pantalla)")
    else:
        r(AVISO, "Sin modelo con vista", "NOVA leerá el texto de tu pantalla con el OCR de Windows. Para que vea, ejecuta instalar.bat.")
    if not _puerto_libre(int(config.endpoint.rsplit(":", 1)[-1])):
        r(AVISO, f"El puerto del motor ({config.endpoint}) está ocupado", "Si no es NOVA, cambia «endpoint» en config.json.")

    if binario and modelo and not rapido:
        motor = MotorLlama(config)
        try:
            t0 = time.monotonic()
            motor.asegurar()
            r(BIEN, f"El motor arrancó en {time.monotonic() - t0:.1f} s")
            t0 = time.monotonic()
            texto = motor.chat([{"role": "system", "content": "Responde en español, en una sola línea, empezando con FINAL:"},
                                {"role": "user", "content": "Preséntate en una frase."}], max_tokens=80, temperatura=0.2)
            dur = time.monotonic() - t0
            vel = max(len(texto) / 4, 1) / max(dur, 0.01)
            r(BIEN if vel >= 8 else AVISO, f"Primera respuesta en {dur:.1f} s (unos {vel:.0f} tokens/s)",
              "" if vel >= 8 else "Va lento: revisa que use la GPU (versión CUDA) o baja «context» en config.json.")
            r(BIEN if texto.strip().upper().startswith("FINAL") else AVISO, "Formato: " + texto.strip()[:120].replace("\n", " "),
              "" if texto.strip().upper().startswith("FINAL") else "El modelo no siguió el formato; NOVA lo tolera, pero un modelo instruct sigue mejor las herramientas.")
            from .nucleo import ACCION, sin_pensamientos
            from .personalidad import HERRAMIENTAS_DOC

            salida = sin_pensamientos(motor.chat([{"role": "system", "content": HERRAMIENTAS_DOC},
                                                  {"role": "user", "content": "¿Cuánto es 1234 por 5678? Usa la herramienta calcular."}],
                                                 max_tokens=120, temperatura=0.1))
            r(BIEN if ACCION.match(salida) else AVISO, "Uso de herramientas: " + salida.strip()[:120].replace("\n", " "),
              "" if ACCION.match(salida) else "No pidió la herramienta; prueba otro modelo instruct (ver README).")
            if smi:
                p = _correr([smi, "--query-gpu=memory.used,memory.total", "--format=csv,noheader"])
                if p and p.returncode == 0:
                    r(BIEN, "Memoria de video con el modelo cargado: " + p.stdout.strip())
        except Exception as exc:  # noqa: BLE001 - el diagnóstico reporta todo
            r(MAL, f"El motor falló: {exc}", f"Revisa {config.rutas.estado / 'llama-server.log'}")
        finally:
            motor.detener()

    r.titulo("Internet")
    from .internet import Internet

    net = Internet(config)
    if not config.internet:
        r(AVISO, "Internet desactivado en la configuración")
    elif net.hay_conexion():
        r(BIEN, "Hay conexión")
        try:
            ddg = net._duckduckgo("llama.cpp", 3)
            r(BIEN if ddg else AVISO, f"DuckDuckGo: {len(ddg)} resultado(s)", "" if ddg else "Se usará Wikipedia de respaldo; también puedes configurar SearXNG.")
        except Exception as exc:  # noqa: BLE001
            r(AVISO, f"DuckDuckGo falló: {exc}", "Se usará Wikipedia de respaldo.")
        try:
            wiki = net._wikipedia("Inteligencia artificial", 2)
            r(BIEN if wiki else AVISO, f"Wikipedia: {len(wiki)} resultado(s)")
        except Exception as exc:  # noqa: BLE001
            r(AVISO, f"Wikipedia falló: {exc}")
    else:
        r(AVISO, "Sin conexión a internet", "NOVA funciona igual; solo no podrá buscar.")

    r.titulo("Voz")
    from .voz import Voz

    voz = Voz(config)
    cap = voz.capacidades()
    r(BIEN if cap["tts"] else AVISO, f"Hablar (Piper): {voz.tts_bin or 'no instalado'} · voz: {voz.tts_voz.name if voz.tts_voz else 'falta'}",
      "" if cap["tts"] else "Ejecuta instalar.bat para instalar la voz.")
    r(BIEN if cap["stt"] else AVISO, f"Oír (whisper.cpp): {voz.stt_bin or 'no instalado'} · modelo: {voz.stt_modelo.name if voz.stt_modelo else 'falta'}",
      "" if cap["stt"] else "Ejecuta instalar.bat para instalar el oído.")
    if cap["tts"] and not rapido:
        try:
            t0 = time.monotonic()
            audio = voz.sintetizar("Hola, soy NOVA. Ya puedo hablar contigo.")
            r(BIEN, f"Síntesis de voz: {len(audio) // 1024} KB en {time.monotonic() - t0:.1f} s")
            (config.rutas.estado / "prueba-voz.wav").write_bytes(audio)
            if cap["stt"]:
                t0 = time.monotonic()
                oido = voz.transcribir(audio)
                ok = "nova" in oido.lower() or "hola" in oido.lower()
                r(BIEN if ok else AVISO, f"Me escuché a mí misma en {time.monotonic() - t0:.1f} s: «{oido}»")
        except Exception as exc:  # noqa: BLE001
            r(MAL, f"La voz falló: {exc}")

    r.titulo("Pantalla y archivos")
    from . import pantalla

    if pantalla.ES_WINDOWS:
        try:
            cap = pantalla.capturar(320, esconder_nova=False)
            r(BIEN, f"Captura de pantalla: {cap.ancho}x{cap.alto} · ventana en uso: {cap.ventana or 'ninguna'}")
            if not rapido and not mmproj:
                texto = pantalla.leer_texto(pantalla.capturar(1280, esconder_nova=False))
                r(BIEN if texto else AVISO, f"OCR de Windows: {len(texto)} caracteres leídos",
                  "" if texto else "Instala el paquete de idioma español con «Reconocimiento óptico de caracteres» en Configuración → Hora e idioma.")
        except Exception as exc:  # noqa: BLE001
            r(MAL, f"No pude capturar la pantalla: {exc}")
    else:
        r(AVISO, "Ver la pantalla solo funciona en Windows")
    from .memoria import Memoria
    from .nucleo import _variantes_carpeta
    import json as _json
    import os as _os

    mem = Memoria(config.rutas.datos / "nova.db")
    try:
        propias = _json.loads(mem.ajuste("carpetas", "[]") or "[]")
        conocidas = [c for c in [*config.carpetas, *propias] if any(_os.path.isdir(v) for v in _variantes_carpeta(c))]
        r(BIEN, f"Carpetas que conozco: {', '.join(conocidas) or 'ninguna'} · {mem.estado()['documentos']} archivos leídos",
          "" if propias else "Dile «conoce la carpeta N:\\TU_CARPETA» para que conozca tus proyectos.")
    finally:
        mem.cerrar()

    r.titulo("Interfaz y celular")
    r(BIEN if _puerto_libre(config.web_port) else AVISO, f"Puerto del orbe {config.web_port}",
      "" if _puerto_libre(config.web_port) else "Ocupado: puede ser NOVA ya abierta.")
    try:
        import cryptography  # noqa: F401
        r(BIEN, "Conexión cifrada con el celular disponible")
    except ImportError:
        r(AVISO, "Falta «cryptography»: la conexión con el celular no irá cifrada", "pip install cryptography")
    from .celular import ip_local
    r(BIEN, f"IP de esta PC en tu red: {ip_local()}")

    r.titulo("Resultado")
    r(BIEN if r.fallas == 0 else MAL, "Todo listo." if r.fallas == 0 else f"{r.fallas} cosa(s) por arreglar (ver ✘ arriba).")
    destino = config.rutas.estado / "diagnostico.txt"
    destino.write_text("\n".join(r.lineas), encoding="utf-8")
    print(f"\nReporte guardado en {destino}")
    return r
