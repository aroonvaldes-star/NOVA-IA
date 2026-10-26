"""Carpetas de NOVA según el sistema.

Windows (objetivo principal):
    programa       %LOCALAPPDATA%\\Programs\\NOVA
    configuración  %APPDATA%\\NOVA
    datos          %LOCALAPPDATA%\\NOVA
En otros sistemas se usan las carpetas XDG, útiles para desarrollar y probar.
La variable NOVA_HOME fuerza una sola raíz (pruebas y modo portátil).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

ES_WINDOWS = sys.platform == "win32"


@dataclass(frozen=True)
class Rutas:
    programa: Path
    config: Path
    datos: Path

    @property
    def estado(self) -> Path:
        return self.datos / "estado"

    @property
    def modelos(self) -> Path:
        return self.datos / "models"

    @property
    def creaciones(self) -> Path:
        return self.datos / "creaciones"

    def crear(self) -> None:
        for carpeta in (self.config, self.datos, self.estado, self.modelos):
            carpeta.mkdir(parents=True, exist_ok=True)
            proteger(carpeta)


def rutas() -> Rutas:
    raiz = os.environ.get("NOVA_HOME")
    if raiz:
        base = Path(raiz)
        return Rutas(programa=base / "programa", config=base / "config", datos=base / "datos")
    if ES_WINDOWS:
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        roaming = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return Rutas(programa=local / "Programs" / "NOVA", config=roaming / "NOVA", datos=local / "NOVA")
    xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    xdg_datos = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return Rutas(
        programa=Path(__file__).resolve().parent.parent,
        config=xdg_config / "nova",
        datos=xdg_datos / "nova",
    )


def proteger(ruta: Path) -> None:
    """Deja la carpeta o el archivo accesible solo para el usuario actual.

    En Windows se aplica una ACL sin herencia que solo da acceso al usuario
    (icacls con argumentos fijos, nunca desde el agente). En POSIX, 0700/0600.
    """
    try:
        if ES_WINDOWS:
            import subprocess

            usuario = os.environ.get("USERNAME")
            if not usuario:
                return
            subprocess.run(
                ["icacls", str(ruta), "/inheritance:r", "/grant:r", f"{usuario}:(OI)(CI)F" if ruta.is_dir() else f"{usuario}:F"],
                capture_output=True,
                timeout=15,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
                check=False,
            )
        else:
            os.chmod(ruta, 0o700 if ruta.is_dir() else 0o600)
    except OSError:
        pass
