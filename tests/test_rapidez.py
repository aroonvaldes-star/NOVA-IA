"""Velocidad: que el modelo quepa en la GPU, que se reutilice el prompt y que se anote cuánto tarda."""

import http.server
import json
import struct
import threading

from nova import motor as motor_mod
from nova import personalidad
from nova.motor import MotorLlama, MotorRemoto, capas_del_modelo


def _gguf(ruta, capas=36):
    clave = b"qwen3.block_count"
    otra = b"general.name"
    datos = b"GGUF" + struct.pack("<IQQ", 3, 0, 2)
    datos += struct.pack("<Q", len(otra)) + otra + struct.pack("<IQ", 8, 5) + b"Qwen3"
    datos += struct.pack("<Q", len(clave)) + clave + struct.pack("<II", 4, capas)
    ruta.write_bytes(datos)


def _motor(config, monkeypatch, libre, mb_modelo=2500):
    m = config.rutas.modelos
    m.mkdir(parents=True, exist_ok=True)
    ruta = m / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
    _gguf(ruta)
    with open(ruta, "r+b") as f:
        f.truncate(mb_modelo * 2**20)  # archivo disperso: pesa 2.5 GB sin ocupar disco
    monkeypatch.setattr(motor_mod, "vram_libre_mb", lambda: libre)
    return MotorLlama(config)


def test_lee_las_capas_del_gguf(tmp_path):
    _gguf(tmp_path / "m.gguf", capas=28)
    assert capas_del_modelo(tmp_path / "m.gguf") == 28
    (tmp_path / "x.gguf").write_bytes(b"nada")
    assert capas_del_modelo(tmp_path / "x.gguf") is None


def test_si_cabe_va_completo_en_la_gpu(config, monkeypatch):
    m = _motor(config, monkeypatch, libre=4200)
    assert m._acomodar() == (8192, 99)
    assert "completo" in m.ajuste


def test_si_no_cabe_baja_el_contexto_y_luego_las_capas(config, monkeypatch):
    m = _motor(config, monkeypatch, libre=3600)
    assert m._acomodar() == (4096, 99)
    m = _motor(config, monkeypatch, libre=2600)
    contexto, capas = m._acomodar()
    assert contexto == 4096 and 20 <= capas < 36
    assert "capas en la GPU" in m.ajuste


def test_sin_nvidia_no_toca_nada(config, monkeypatch):
    m = _motor(config, monkeypatch, libre=None)
    assert m._acomodar() == (8192, 99)
    base, _ = m._argumentos()
    assert base[base.index("-np") + 1] == "1"


def test_prompt_estable_primero():
    s = personalidad.prompt_sistema("Soy NOVA.", "Hora 10:31, ventana VS Code", "Se llama Aroon", "- recuerdo")
    assert s.index("## Herramientas") < s.index("## Lo que sé de mi usuario") < s.index("## Recuerdos") < s.index("## Mi estado ahora")
    # Lo que cambia en cada turno no altera el inicio: llama-server reutiliza esa parte.
    otro = personalidad.prompt_sistema("Soy NOVA.", "Hora 10:32, ventana Edge", "Se llama Aroon", "")
    assert otro.startswith(personalidad.prompt_sistema("Soy NOVA.", "", "", ""))


class _Falso(http.server.BaseHTTPRequestHandler):
    recibido: list = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Falso.recibido.append(cuerpo)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for t in ["FINAL: ", "hola"]:
            self.wfile.write(f"data: {json.dumps({'choices': [{'delta': {'content': t}}]})}\n\n".encode())
        fin = {"choices": [{"delta": {}, "finish_reason": "stop"}],
               "timings": {"prompt_n": 120, "prompt_ms": 80.0, "predicted_n": 2, "predicted_per_second": 41.5}}
        self.wfile.write(f"data: {json.dumps(fin)}\n\ndata: [DONE]\n\n".encode())


def _servidor():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Falso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_pide_reusar_prompt_y_guarda_los_tiempos(config):
    srv = _servidor()
    try:
        config["endpoint"] = f"http://127.0.0.1:{srv.server_address[1]}"
        config["manage_server"] = False
        m = MotorLlama(config)
        assert m.chat([{"role": "user", "content": "hola"}]) == "FINAL: hola"
        assert _Falso.recibido[-1]["cache_prompt"] is True
        assert m.metricas["prompt_n"] == 120 and m.metricas["predicted_per_second"] == 41.5
        assert "segundos" in m.metricas and "primera_palabra" in m.metricas

        config["cerebro_endpoint"] = config["endpoint"]
        r = MotorRemoto(config)
        r.chat([{"role": "user", "content": "hola"}])
        assert "cache_prompt" not in _Falso.recibido[-1]  # la nube rechaza campos desconocidos
        r.precalentar("x")  # no gasta peticiones en la nube
        assert len(_Falso.recibido) == 2
    finally:
        srv.shutdown()


def test_anota_cuanto_tardo_cada_respuesta(crear_nova, config):
    nova = crear_nova(["FINAL: Listo."])
    nova.motor.metricas = {"segundos": 1.2, "prompt_n": 900, "prompt_ms": 300.0, "predicted_n": 20, "predicted_per_second": 35.0}
    nova.procesar("hola")
    linea = (config.rutas.estado / "tiempos.log").read_text(encoding="utf-8").strip().splitlines()[-1]
    assert "total" in linea and "local: 1.2s" in linea and "leyó 900 tokens" in linea and "a 35/s" in linea
