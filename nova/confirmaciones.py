"""Confirmaciones de un solo uso.

Cada acción que necesita permiso recibe un código de 4 cifras que solo se
muestra en pantalla (nunca se dice en voz alta). Caduca a los 10 minutos y se
borra al usarse. Algunas acciones (escribir fuera de tu carpeta o en otra
unidad) no aceptan confirmación por voz.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

CADUCIDAD_S = 600


@dataclass
class Pendiente:
    codigo: str
    descripcion: str
    accion: Callable[[], Any]
    solo_clic: bool = False
    creada: float = field(default_factory=time.monotonic)

    def vigente(self) -> bool:
        return time.monotonic() - self.creada < CADUCIDAD_S


class Confirmaciones:
    def __init__(self) -> None:
        self._pendientes: dict[str, Pendiente] = {}
        self._cerrojo = threading.Lock()

    def pedir(self, descripcion: str, accion: Callable[[], Any], solo_clic: bool = False) -> Pendiente:
        with self._cerrojo:
            self._limpiar()
            while True:
                codigo = f"{secrets.randbelow(10000):04d}"
                if codigo not in self._pendientes:
                    break
            p = Pendiente(codigo, descripcion, accion, solo_clic)
            self._pendientes[codigo] = p
            return p

    def confirmar(self, codigo: str, por_voz: bool = False) -> dict:
        with self._cerrojo:
            self._limpiar()
            p = self._pendientes.get(codigo.strip())
            if not p:
                return {"ok": False, "salida": "Código inválido o caducado."}
            if por_voz and p.solo_clic:
                return {"ok": False, "salida": "Esta acción solo se confirma con clic o teclado."}
            del self._pendientes[p.codigo]
        resultado = p.accion()
        return resultado if isinstance(resultado, dict) else {"ok": True, "salida": str(resultado)}

    def cancelar(self, codigo: str) -> bool:
        with self._cerrojo:
            return self._pendientes.pop(codigo.strip(), None) is not None

    def lista(self) -> list[dict]:
        with self._cerrojo:
            self._limpiar()
            return [{"codigo": p.codigo, "descripcion": p.descripcion, "solo_clic": p.solo_clic} for p in self._pendientes.values()]

    def _limpiar(self) -> None:
        for codigo in [c for c, p in self._pendientes.items() if not p.vigente()]:
            del self._pendientes[codigo]
