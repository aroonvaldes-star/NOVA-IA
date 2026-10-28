import json

from nova import omniroute
from nova.voz import orden_rapida


def test_reconoce_omniroute():
    assert omniroute.es_omniroute("http://127.0.0.1:20128/v1")
    assert omniroute.es_omniroute("http://localhost:20128/v1/")
    assert not omniroute.es_omniroute("https://api.groq.com/openai/v1")
    assert not omniroute.es_omniroute("http://192.168.1.5:20128/v1")  # nunca en otra máquina
    assert not omniroute.es_omniroute("")


def test_entorno_solo_en_esta_pc(tmp_path, monkeypatch):
    monkeypatch.setenv("HOSTNAME", "0.0.0.0")
    env = omniroute.entorno(tmp_path)
    assert env["OMNIROUTE_SERVER_HOST"] == "127.0.0.1" and env["PORT"] == "20128"
    assert "HOSTNAME" not in env
    assert env["DATA_DIR"].endswith("omniroute-datos")


def test_programa_instalado(tmp_path):
    assert omniroute.programa(tmp_path) is None
    script = tmp_path / "omniroute" / "node_modules" / "omniroute" / "bin" / "omniroute.mjs"
    script.parent.mkdir(parents=True)
    script.write_text("")
    assert omniroute.programa(tmp_path)[-1] == str(script)


def test_no_arranca_si_no_es_omniroute(config):
    config["cerebro_endpoint"] = "https://api.groq.com/openai/v1"
    assert omniroute.arrancar(config) == ""


def test_avisa_si_falta_instalar(config):
    config["cerebro_endpoint"] = "http://127.0.0.1:20128/v1"
    if omniroute.escuchando():
        return
    assert "no está instalado" in omniroute.arrancar(config)


def test_orden_quien_contesto():
    for t in ("¿quién contestó?", "nova, qué modelo respondió eso", "muestra la auditoría", "auditoría"):
        assert orden_rapida(t).accion == "auditoria", t
    assert orden_rapida("quién contestó la carta de Juan en 1990 y por qué") is None


def test_auditoria_en_palabras(crear_nova, config):
    nova = crear_nova()
    assert "Todavía no" in nova.procesar("¿quién contestó?")
    (config.rutas.estado / "auditoria.jsonl").write_text("\n".join(json.dumps(f) for f in [
        {"fecha": "2026-10-28T10:00:00", "motor": "nube", "modelo": "OmniRoute (auto)", "modelo_real": "groq/openai/gpt-oss-120b",
         "endpoint": "127.0.0.1:20128", "segundos": 1.23, "accion": "", "error": ""},
        {"fecha": "2026-10-28T10:01:00", "motor": "nube", "modelo": "OmniRoute (auto)", "modelo_real": "",
         "endpoint": "127.0.0.1:20128", "segundos": None, "accion": "", "error": "HTTP 503"},
        {"fecha": "2026-10-28T10:02:00", "motor": "local", "modelo": "Qwen3 4B", "modelo_real": "", "endpoint": "127.0.0.1:8080",
         "segundos": 3.0, "accion": '{"herramienta": "calcular"}', "error": ""},
    ]) + "\n", encoding="utf-8")
    texto = nova.procesar("quién contestó")
    assert "10:00 groq/openai/gpt-oss-120b vía OmniRoute: 1.2 s" in texto
    assert "falló: HTTP 503" in texto
    assert "Qwen3 4B (local): 3.0 s, usó" in texto
