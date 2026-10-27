"""Pruebas de las correcciones de la revisión de la 0.1.1."""
import socket

import pytest

from nova import politica as p
from nova.confirmaciones import FALLOS_MAX, Confirmaciones
from nova.internet import URLNoPermitida, _revisar_par


@pytest.mark.parametrize("ruta", [
    r"C:\Users\yo\..\..\Windows\System32\drivers\etc\hosts",
    r"C:\Users\yo\..\otro\Documentos\a.txt",
    r"C:\PROGRA~1\app\a.txt",
    r"C:\Users\yo\DOCUME~1\a.txt",
])
def test_ruta_windows_sin_puntos_ni_nombres_cortos(ruta):
    assert not p.revisar_ruta_texto(ruta, windows=True, usuario="yo").ok


def test_ruta_windows_normal_sigue_permitida():
    assert p.revisar_ruta_texto(r"D:\Proyectos\nova~notas.txt", windows=True, usuario="yo").ok


@pytest.mark.parametrize("nombre", ["x.reg", "x.url", "x.psm1", "x.cpl", "x.jar", "desktop.ini", "x.chm"])
def test_no_escribe_archivos_que_windows_ejecuta(tmp_path, nombre):
    assert not p.revisar_ruta(str(tmp_path / nombre), escribir=True).ok


def test_confirmaciones_se_anulan_tras_fallos():
    c = Confirmaciones()
    hechas = []
    pend = c.pedir("algo", lambda: hechas.append(1))
    malos = [f"{i:04d}" for i in range(10000) if f"{i:04d}" != pend.codigo][:FALLOS_MAX]
    for codigo in malos:
        c.confirmar(codigo)
    assert c.confirmar(pend.codigo)["ok"] is False
    assert not hechas and not c.lista()


def test_acierto_reinicia_los_fallos():
    c = Confirmaciones()
    for _ in range(FALLOS_MAX - 1):
        c.confirmar("xxxx")
    assert c.confirmar(c.pedir("a", lambda: "ok").codigo)["ok"]
    pend = c.pedir("b", lambda: "ok")
    c.confirmar("xxxx")
    assert c.confirmar(pend.codigo)["ok"]


def test_rebinding_ip_interna_se_rechaza():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    cli = socket.create_connection(srv.getsockname())
    try:
        with pytest.raises(URLNoPermitida):
            _revisar_par(cli)
    finally:
        cli.close()
        srv.close()
