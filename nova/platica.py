"""Plática de a ratos: NOVA te habla por iniciativa, con límites para no estorbar.

  * Máximo `platica_max_dia` veces al día, con al menos `platica_minutos_entre`.
  * Nunca en juegos o videos a pantalla completa, llamadas (micrófono o cámara
    en uso por otra app), «No molestar» ni en el horario de silencio.
  * Si la ignoras, habla menos; si le sigues la plática, se anima un poco.
  * La iniciativa nunca ejecuta nada: solo propone.
"""

from __future__ import annotations

import datetime as dt
import random
import sys
from dataclasses import dataclass


@dataclass
class Situacion:
    ahora: dt.datetime
    inactivo_min: float = 0.0          # minutos sin teclado ni ratón
    sesion_continua_min: float = 0.0   # minutos seguidos trabajando
    pantalla_completa: bool = False
    en_llamada: bool = False
    no_molestar: bool = False


@dataclass
class EstadoPlatica:
    dia: str = ""
    hoy: int = 0
    ultima: dt.datetime | None = None
    ignoradas: int = 0
    seguidas: int = 0
    pendiente: bool = False

    def nuevo_dia(self, fecha: dt.date) -> None:
        if self.dia != fecha.isoformat():
            self.dia, self.hoy = fecha.isoformat(), 0


def _hora(texto: str) -> dt.time:
    h, m = texto.split(":")
    return dt.time(int(h), int(m))


def en_silencio(ahora: dt.datetime, desde: str, hasta: str) -> bool:
    d, h, t = _hora(desde), _hora(hasta), ahora.time()
    return (d <= t or t < h) if d > h else (d <= t < h)


def limite_diario(config, estado: EstadoPlatica) -> int:
    base = int(config.platica_max_dia)
    ajuste = min(estado.seguidas // 3, 2) - min(estado.ignoradas, base)
    return max(0, base + ajuste)


def puede_hablar(config, estado: EstadoPlatica, s: Situacion) -> tuple[bool, str]:
    if not config.platica:
        return False, "plática desactivada"
    if s.pantalla_completa or s.en_llamada or s.no_molestar:
        return False, "estás ocupado"
    if en_silencio(s.ahora, config.silencio_desde, config.silencio_hasta):
        return False, "horario de silencio"
    estado.nuevo_dia(s.ahora.date())
    if estado.pendiente:
        return False, "hay una plática sin respuesta"
    if estado.hoy >= limite_diario(config, estado):
        return False, "ya platicamos suficiente hoy"
    if estado.ultima and (s.ahora - estado.ultima).total_seconds() < config.platica_minutos_entre * 60:
        return False, "fue hace poco"
    return True, ""


def elegir_tema(memoria, s: Situacion, nombre: str | None = None, azar: random.Random | None = None) -> str | None:
    """Frase de inicio sin usar el modelo (el modelo puede pulirla después)."""
    azar = azar or random.Random()
    tu = f", {nombre}" if nombre else ""
    if s.sesion_continua_min >= 120:
        return f"Llevas más de dos horas seguidas{tu}. ¿Un descanso de cinco minutos?"
    opciones = []
    if s.inactivo_min >= 30:
        proyectos = memoria.recuerdos("proyecto", 3)
        if proyectos:
            opciones.append(f"¿Qué onda{tu}? La última vez andábamos con esto: {proyectos[0].texto.lower()}. ¿Le seguimos?")
        else:
            opciones.append(f"¡Qué onda{tu}! ¿En qué te ayudo hoy?")
    for m in memoria.recuerdos("momento", 3):
        opciones.append(f"Oye{tu}, me habías contado: «{m.texto}». ¿Cómo te fue?")
    pendientes = memoria.tareas()
    if pendientes:
        opciones.append(f"Tienes {len(pendientes)} tarea(s) pendiente(s); la primera es «{pendientes[0]['texto']}». ¿Te ayudo con ella?")
    return azar.choice(opciones) if opciones else None


def registrar(estado: EstadoPlatica, ahora: dt.datetime) -> None:
    estado.nuevo_dia(ahora.date())
    estado.hoy += 1
    estado.ultima = ahora
    estado.pendiente = True


def respuesta(estado: EstadoPlatica, siguio: bool) -> None:
    estado.pendiente = False
    if siguio:
        estado.seguidas += 1
        estado.ignoradas = max(0, estado.ignoradas - 1)
    else:
        estado.ignoradas += 1


def no_molestar_windows() -> bool:
    """True si Windows indica pantalla completa, presentación o «No molestar» (SHQueryUserNotificationState)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        estado = ctypes.c_int()
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(estado))
        # 2 ocupado, 3 pantalla completa D3D, 4 presentación, 6 app en modo silencioso
        return estado.value in (2, 3, 4, 6)
    except (OSError, AttributeError):
        return False
