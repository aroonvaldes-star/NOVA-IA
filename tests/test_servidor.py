import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from nova.servidor import crear_servidor


@pytest.fixture
def servidor(crear_nova):
    nova = crear_nova(["FINAL: hola desde NOVA"])
    srv = crear_servidor(nova, puerto=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield nova, srv, srv.server_address[1]
    srv.shutdown()


def pedir(puerto, ruta, datos=None, cabeceras=None, host=None, esquema="http"):
    import ssl
    ctx = ssl._create_unverified_context() if esquema == "https" else None
    req = urllib.request.Request(f"{esquema}://127.0.0.1:{puerto}{ruta}", data=json.dumps(datos).encode() if datos is not None else None,
                                 headers={"Content-Type": "application/json", **(cabeceras or {})})
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_pagina_con_token_y_csp(servidor):
    nova, srv, puerto = servidor
    codigo, cuerpo, cab = pedir(puerto, "/")
    assert codigo == 200 and srv.token.encode() in cuerpo
    assert "default-src 'self'" in cab["Content-Security-Policy"]


def test_host_ajeno_rechazado(servidor):
    _, _, puerto = servidor
    assert pedir(puerto, "/", host="malo.example")[0] == 403


def test_api_exige_token(servidor):
    nova, srv, puerto = servidor
    assert pedir(puerto, "/api/estado")[0] == 401
    assert pedir(puerto, "/api/mensaje", {"texto": "hola"})[0] == 401
    codigo, cuerpo, _ = pedir(puerto, "/api/estado", cabeceras={"X-Nova-Token": srv.token})
    assert codigo == 200 and json.loads(cuerpo)["nombre"] == "NOVA"


def test_origen_ajeno_rechazado(servidor):
    nova, srv, puerto = servidor
    codigo, _, _ = pedir(puerto, "/api/mensaje", {"texto": "hola"}, {"X-Nova-Token": srv.token, "Origin": "https://malo.example"})
    assert codigo == 401


def test_mensaje_y_eventos(servidor):
    nova, srv, puerto = servidor
    q = nova.bus.suscribir()
    assert pedir(puerto, "/api/mensaje", {"texto": "hola"}, {"X-Nova-Token": srv.token})[0] == 200
    limite = time.time() + 5
    while time.time() < limite:
        e = q.get(timeout=5)
        if e["tipo"] == "final":
            assert e["texto"] == "hola desde NOVA"
            return
    raise AssertionError("sin respuesta")


def test_no_sale_de_la_carpeta_ui(servidor):
    _, _, puerto = servidor
    assert pedir(puerto, "/../nova/config.py")[0] == 404
    assert pedir(puerto, "/vendor/three.module.min.js")[0] == 200


def test_celular_emparejar(crear_nova):
    nova = crear_nova(["FINAL: hola celular"])
    srv = crear_servidor(nova, puerto=0, celular=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        esquema = "https" if srv.https else "http"
        puerto = srv.server_address[1]
        import functools
        pedir_ = functools.partial(pedir, esquema=esquema)
        codigo, cuerpo, _ = pedir_(puerto, "/")
        assert codigo == 200 and b'class="celular"' in cuerpo
        assert pedir_(puerto, "/api/estado")[0] == 401
        assert pedir_(puerto, "/api/yo")[0] == 403  # fuera de lo permitido al celular
        assert pedir_(puerto, "/api/emparejar", {"codigo": "123456"})[0] == 403  # sin código vigente
        cod = nova.emparejador.nuevo_codigo()
        malo = "000000" if cod != "000000" else "111111"
        assert pedir_(puerto, "/api/emparejar", {"codigo": malo})[0] == 403
        codigo, cuerpo, _ = pedir_(puerto, "/api/emparejar", {"codigo": cod, "nombre": "Pixel"})
        llave = json.loads(cuerpo)["dispositivo"]
        assert pedir_(puerto, "/api/emparejar", {"codigo": cod})[0] == 403  # un solo uso
        assert pedir_(puerto, "/api/estado", cabeceras={"X-Nova-Dispositivo": llave})[0] == 200
        assert nova.memoria.dispositivos()[0]["nombre"] == "Pixel"
        assert llave not in json.dumps(nova.memoria.dispositivos())
        # Desde el celular no se confirma lo que exige clic en la PC
        p = nova.confirmaciones.pedir("escribir en E:", lambda: {"ok": True, "salida": "x"}, solo_clic=True)
        codigo, cuerpo, _ = pedir_(puerto, "/api/confirmar", {"codigo": p.codigo}, {"X-Nova-Dispositivo": llave})
        assert json.loads(cuerpo)["ok"] is False
        nova.procesar("desconecta mis celulares")
        assert pedir_(puerto, "/api/estado", cabeceras={"X-Nova-Dispositivo": llave})[0] == 401
    finally:
        srv.shutdown()


def test_emparejar_limita_intentos(crear_nova):
    nova = crear_nova()
    cod = nova.emparejador.nuevo_codigo()
    for _ in range(5):
        assert nova.emparejador.emparejar("x", "malo") is None
    assert nova.emparejador.emparejar(cod, "tarde") is None
