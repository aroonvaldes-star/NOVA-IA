"""OmniRoute: el enrutador de cerebros grandes que corre en tu propia PC.

NOVA le habla como a cualquier servicio compatible con OpenAI en http://127.0.0.1:20128/v1
con el modelo «auto»: OmniRoute escoge un proveedor gratis con cuota (Groq, Gemini, OpenRouter...)
y cambia solo a otro cuando uno se agota. Lo instala omniroute.ps1 en %LOCALAPPDATA%\\NOVA\\omniroute
con su propio Node en %LOCALAPPDATA%\\NOVA\\node, sin administrador.

NOVA solo lo arranca si su config apunta a él y no está corriendo ya. Siempre escucha en 127.0.0.1:
nadie de tu red puede usar tus cuotas.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import urllib.parse
from pathlib import Path

PUERTO = 20128
ENDPOINT = f"http://127.0.0.1:{PUERTO}/v1"
SIN_VENTANA = 0x08000000 if sys.platform == "win32" else 0


def es_omniroute(endpoint: str) -> bool:
    partes = urllib.parse.urlsplit(endpoint or "")
    return partes.hostname in {"127.0.0.1", "localhost", "::1"} and partes.port == PUERTO


def carpetas(datos: Path) -> tuple[Path, Path]:
    """(Node, OmniRoute) dentro de la carpeta de datos de NOVA. Sus datos y claves van en omniroute-datos."""
    return datos / "node", datos / "omniroute"


def programa(datos: Path) -> list[str] | None:
    """La orden para lanzar OmniRoute, o None si no está instalado."""
    node_dir, omni = carpetas(datos)
    node = node_dir / ("node.exe" if sys.platform == "win32" else "bin/node")
    # npm --prefix en Windows deja los paquetes en <prefix>\node_modules; en Linux en <prefix>/lib/node_modules.
    for base in (omni / "node_modules", omni / "lib" / "node_modules"):
        script = base / "omniroute" / "bin" / "omniroute.mjs"
        if script.exists():
            return [str(node) if node.exists() else "node", str(script)]
    return None


def escuchando(timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PUERTO), timeout=timeout):
            return True
    except OSError:
        return False


def entorno(datos: Path) -> dict:
    """Variables con las que corre: solo en 127.0.0.1, sin avisos de actualización ni ventana del navegador."""
    node_dir, _ = carpetas(datos)
    env = dict(os.environ)
    env["PATH"] = str(node_dir) + os.pathsep + env.get("PATH", "")
    env["DATA_DIR"] = str(datos / "omniroute-datos")
    env["OMNIROUTE_SERVER_HOST"] = "127.0.0.1"
    env["PORT"] = str(PUERTO)
    env["OMNIROUTE_NO_UPDATE_NOTIFIER"] = "1"
    env.pop("HOSTNAME", None)
    return env


def arrancar(config) -> str:
    """Lo prende en segundo plano si NOVA lo usa y no está corriendo. Devuelve qué pasó, en palabras."""
    if not es_omniroute(config.cerebro_endpoint):
        return ""
    if escuchando():
        return "OmniRoute ya estaba corriendo."
    datos = config.rutas.datos
    orden = programa(datos)
    if not orden:
        return "OmniRoute no está instalado; corre omniroute.bat en la carpeta de NOVA."
    config.rutas.estado.mkdir(parents=True, exist_ok=True)
    registro = open(config.rutas.estado / "omniroute.log", "ab")
    try:
        subprocess.Popen(
            orden + ["serve", "--no-open", "--no-tray"],
            cwd=str(carpetas(datos)[1]),
            env=entorno(datos),
            stdin=subprocess.DEVNULL,
            stdout=registro,
            stderr=subprocess.STDOUT,
            creationflags=SIN_VENTANA,
        )
    except OSError as exc:
        return f"No pude arrancar OmniRoute: {exc}"
    finally:
        registro.close()
    return "Arranqué OmniRoute."
