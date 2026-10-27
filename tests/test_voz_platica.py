import datetime as dt

import pytest

from nova import platica
from nova.voz import orden_rapida, para_hablar, quitar_activacion


@pytest.mark.parametrize("texto,accion", [
    ("Oye NOVA, mira la pantalla", "pantalla"), ("nova, nueva tarea: llamar a Luis mañana", "tarea"),
    ("apunta: comprar SSD", "nota"), ("¿Qué tengo hoy?", "resumen"), ("busca en internet llama.cpp", "buscar_web"),
    ("¿Cuánto espacio tengo?", "unidades"), ("NOVA, desconéctate", "internet"), ("para", "parar"),
    ("¿Qué sabes de mí?", "yo"), ("olvida lo de futbol", "olvidar"), ("esto no lo recuerdes", "privado"),
    ("confirmar 4821", "confirmar"), ("conecta mi celular", "celular"), ("desconecta mis celulares", "celular"),
])
def test_ordenes_rapidas(texto, accion):
    assert orden_rapida(texto).accion == accion


@pytest.mark.parametrize("texto", ["¿cómo se configura Vulkan?", "eso no va así", "explícame este error", "hola"])
def test_pasa_al_modelo(texto):
    assert orden_rapida(texto) is None


def test_quitar_activacion():
    assert quitar_activacion("Oye NOVA, ¿qué hora es?") == "¿qué hora es?"
    assert quitar_activacion("novata") == "novata"


def test_para_hablar():
    t = para_hablar("Mira:\n```py\nprint(1)\n```\nVe a https://x.com **ya**")
    assert "print" not in t and "https" not in t and "*" not in t


class Cfg(dict):
    __getattr__ = dict.__getitem__


@pytest.fixture
def cfg():
    return Cfg(platica=True, platica_max_dia=3, platica_minutos_entre=90, silencio_desde="23:00", silencio_hasta="08:00")


def test_platica_limites(cfg):
    e = platica.EstadoPlatica()
    ahora = dt.datetime(2026, 10, 26, 12, 0)
    assert platica.puede_hablar(cfg, e, platica.Situacion(ahora))[0]
    assert not platica.puede_hablar(cfg, e, platica.Situacion(ahora, pantalla_completa=True))[0]
    assert not platica.puede_hablar(cfg, e, platica.Situacion(ahora, en_llamada=True))[0]
    assert not platica.puede_hablar(cfg, e, platica.Situacion(dt.datetime(2026, 10, 26, 23, 30)))[0]
    assert not platica.puede_hablar(cfg, e, platica.Situacion(dt.datetime(2026, 10, 26, 7, 0)))[0]
    platica.registrar(e, ahora)
    assert not platica.puede_hablar(cfg, e, platica.Situacion(ahora + dt.timedelta(hours=3)))[0]  # sin respuesta
    platica.respuesta(e, siguio=True)
    assert not platica.puede_hablar(cfg, e, platica.Situacion(ahora + dt.timedelta(minutes=30)))[0]
    assert platica.puede_hablar(cfg, e, platica.Situacion(ahora + dt.timedelta(minutes=91)))[0]


def test_platica_maximo_diario_y_se_ajusta(cfg):
    e = platica.EstadoPlatica()
    t = dt.datetime(2026, 10, 26, 9, 0)
    for _ in range(3):
        platica.registrar(e, t)
        platica.respuesta(e, siguio=True)
        t += dt.timedelta(hours=2)
    # Le siguió la plática 3 veces: se anima y le queda una más.
    assert platica.limite_diario(cfg, e) == 4
    platica.registrar(e, t)
    platica.respuesta(e, siguio=False)
    assert not platica.puede_hablar(cfg, e, platica.Situacion(t + dt.timedelta(hours=2)))[0]
    e2 = platica.EstadoPlatica(ignoradas=2)
    assert platica.limite_diario(cfg, e2) == 1


def test_descanso():
    class M:
        def recuerdos(self, *a):
            return []

        def tareas(self):
            return []
    s = platica.Situacion(dt.datetime(2026, 10, 26, 12), sesion_continua_min=130)
    assert "descanso" in platica.elegir_tema(M(), s, "Aroon")
