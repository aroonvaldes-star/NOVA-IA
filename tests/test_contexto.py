"""Contexto de NOVA: conversación que sigue al reabrir, tus carpetas, la pantalla y la vista del motor."""

import json
import struct
import time
import zipfile
import zlib

from nova import pantalla
from nova.herramientas import Resultado
from nova.motor import MotorLlama, buscar_mmproj, buscar_modelo
from nova.nucleo import Nova, _consulta, _mensaje_resultado


def test_retoma_la_conversacion_al_reabrir(crear_nova, config, internet_falso):
    from nova.motor import MotorSimulado

    nova = crear_nova(["FINAL: Hola Aroon."])
    nova.procesar("Hola, soy Aroon")
    nova.cerrar()
    nueva = Nova(config, motor=MotorSimulado(["FINAL: Sí, me saludaste."]), memoria=nova.memoria, internet=internet_falso)
    assert {"role": "user", "content": "Hola, soy Aroon"} in nueva.historial
    nueva.procesar("¿Te acuerdas?")
    assert "Hola, soy Aroon" in json.dumps(nueva.motor.recibidos[0], ensure_ascii=False)
    assert [m["texto"] for m in nueva.conversacion()][:2] == ["Hola, soy Aroon", "Hola Aroon."]


def test_conoce_una_carpeta_y_la_usa_como_contexto(crear_nova, tmp_path):
    carpeta = tmp_path / "NOVORA_WORKSPACE"
    (carpeta / "NOVORA-LINK" / "node_modules").mkdir(parents=True)
    (carpeta / "NOVORA-LINK" / "README.md").write_text("NOVORA-LINK enlaza el teléfono con la base de datos Firestore.", encoding="utf-8")
    (carpeta / "NOVORA-LINK" / "node_modules" / "basura.js").write_text("Firestore basura", encoding="utf-8")
    with zipfile.ZipFile(carpeta / "plan.docx", "w") as z:
        z.writestr("word/document.xml", "<w:document><w:p><w:r><w:t>Plan de lanzamiento &amp; fechas</w:t></w:r></w:p></w:document>")

    nova = crear_nova(["FINAL: Usa Firestore."])
    respuesta = nova.procesar(f"conoce la carpeta {carpeta}")
    assert "Voy a conocer" in respuesta
    for _ in range(100):
        if nova.memoria.estado()["documentos"] >= 2 and not nova.indexando:
            break
        time.sleep(0.05)
    assert str(carpeta) in nova.carpetas()
    assert nova.memoria.buscar_archivos("readme novora") == [str(carpeta / "NOVORA-LINK" / "README.md")]
    assert not nova.memoria.buscar_archivos("basura")  # node_modules no se lee
    assert "lanzamiento & fechas" in nova.memoria.buscar_documentos("lanzamiento")[0]["texto"]

    nova.procesar("¿qué base de datos usa NOVORA-LINK?")
    assert "Firestore" in nova.motor.recibidos[-1][0]["content"]  # el archivo llegó como contexto

    assert "ya no reviso" in nova.procesar(f"olvida la carpeta {carpeta}")
    assert str(carpeta) not in nova.carpetas()


def test_no_conoce_carpetas_prohibidas(crear_nova):
    nova = crear_nova()
    assert "No puedo leer" in nova.procesar(r"conoce la carpeta C:\Windows\System32\config") or \
        "No encuentro" in nova.procesar(r"conoce la carpeta C:\Windows\System32\config")


def test_herramienta_buscar_archivos(crear_nova, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "presupuesto-2026.csv").write_text("concepto,monto\nservidor,100", encoding="utf-8")
    nova = crear_nova(['ACCION: {"herramienta": "buscar_archivos", "args": {"nombre": "presupuesto"}}', "FINAL: Está en docs."])
    nova.memoria.indexar([str(tmp_path / "docs")])
    nova.procesar("¿dónde dejé el presupuesto?")
    assert "presupuesto-2026.csv" in nova.motor.recibidos[1][-1]["content"]


def test_mira_mi_pantalla_fuera_de_windows_lo_dice(crear_nova):
    nova = crear_nova()
    assert "No pude ver la pantalla" in nova.procesar("mira mi pantalla")


def test_mira_mi_pantalla_con_vista_manda_la_imagen(crear_nova, monkeypatch):
    captura = pantalla.Captura(b"\x89PNG-falso", 2, 2, pantalla.Ventana("main.rs - NOVORA-LINK - Visual Studio Code", "Code.exe"))
    monkeypatch.setattr(pantalla, "capturar", lambda *a, **k: captura)
    nova = crear_nova(["FINAL: Estás en main.rs de NOVORA-LINK."])
    nova.herramientas.vision = True
    assert nova.procesar("¿qué ves en mi pantalla?") == "Estás en main.rs de NOVORA-LINK."
    ultimo = nova.motor.recibidos[0][-1]["content"]
    assert ultimo[1] == {"type": "image_url", "image_url": {"url": captura.data_url()}}
    assert "Visual Studio Code" in ultimo[0]["text"]
    assert nova.herramientas.leyo_privado  # lo de tu pantalla no sale a internet sin tu clic


def test_sin_vista_usa_ocr(crear_nova, monkeypatch):
    monkeypatch.setattr(pantalla, "capturar", lambda *a, **k: pantalla.Captura(b"x", 1, 1, None))
    monkeypatch.setattr(pantalla, "leer_texto", lambda c: "error CS0103: el nombre 'x' no existe")
    nova = crear_nova(["FINAL: Te falta declarar x."])
    nova.procesar("mira mi pantalla")
    assert "CS0103" in nova.motor.recibidos[0][-1]["content"]


def test_ventana_activa_entra_al_contexto(crear_nova, monkeypatch):
    monkeypatch.setattr(pantalla, "ventana_activa", lambda: pantalla.Ventana("informe.docx - Word", "WINWORD.EXE"))
    nova = crear_nova(["FINAL: Ok."])
    nova.procesar("ayúdame con esto")
    assert "informe.docx - Word (WINWORD.EXE)" in nova.motor.recibidos[0][0]["content"]


def test_observador_recuerda_ventanas_distintas():
    o = pantalla.Observador(maximo=3)
    for t in ["a", "a", "b", "c", "a", "d"]:
        o.anotar(pantalla.Ventana(t, "x.exe"))
    assert [v.titulo for _, v in o.recientes] == ["c", "a", "d"]
    assert o.resumen().startswith("Ventana en uso: d (x.exe).")


def test_png_valido():
    png = pantalla.png_desde_bgra(bytes([0, 0, 255, 255, 0, 255, 0, 255]), 2, 1)  # rojo, verde
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    largo = struct.unpack(">I", png[33:37])[0]
    assert png[37:41] == b"IDAT"
    assert zlib.decompress(png[41:41 + largo]) == b"\x00" + bytes([255, 0, 0, 0, 255, 0])


def test_mensaje_resultado_con_y_sin_imagen():
    assert _mensaje_resultado("x", Resultado("hola"))["content"].startswith("RESULTADO de x")
    con = _mensaje_resultado("x", Resultado("hola", imagen="data:image/png;base64,AA"))
    assert con["content"][1]["image_url"]["url"] == "data:image/png;base64,AA"


def test_consulta_limpia():
    assert _consulta("Oye NOVA, busca en internet el precio del dólar hoy") == "el precio del dólar hoy"
    assert _consulta("¿Quién ganó el último mundial?") == "Quién ganó el último mundial"


def test_motor_usa_proyector_de_vista_y_cache_8_bits(config):
    m = config.rutas.modelos
    m.mkdir(parents=True, exist_ok=True)
    for n in ["Qwen3-4B-Instruct-2507-Q4_K_M.gguf", "Qwen3-VL-4B-Instruct-Q4_K_M.gguf", "mmproj-Qwen3VL-4B-Instruct-F16.gguf"]:
        (m / n).write_bytes(b"GGUF")
    assert buscar_modelo(config).name == "Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
    motor = MotorLlama(config)
    assert motor.vision and motor.mmproj.name.startswith("mmproj-Qwen3VL")
    base, extras = motor._argumentos()
    assert "--mmproj" in extras and "--no-mmproj-offload" in extras and "q8_0" in extras
    assert "--mmproj" not in base  # si llama-server no conoce algo, arranca sin extras

    config["vision"] = False
    assert buscar_modelo(config).name == "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
    assert buscar_mmproj(config, m / "Qwen3-VL-4B-Instruct-Q4_K_M.gguf") is None


def test_proyector_generico_solo_con_modelo_de_vista(config):
    m = config.rutas.modelos
    m.mkdir(parents=True, exist_ok=True)
    (m / "mmproj-F16.gguf").write_bytes(b"GGUF")
    assert buscar_mmproj(config, m / "Qwen3-VL-4B-Instruct-Q4_K_M.gguf").name == "mmproj-F16.gguf"
    assert buscar_mmproj(config, m / "Qwen3-4B-Instruct-2507-Q4_K_M.gguf") is None


def test_analiza_una_carpeta_y_la_explica(crear_nova, tmp_path):
    proyecto = tmp_path / "NOVORA-LINK"
    (proyecto / "app" / "src").mkdir(parents=True)
    (proyecto / "node_modules").mkdir()
    (proyecto / "README.md").write_text("# NOVORA-LINK\nEnlaza el celular con la PC por Firestore.", encoding="utf-8")
    (proyecto / "package.json").write_text('{"name": "novora-link", "dependencies": {"firebase": "^11"}}', encoding="utf-8")
    (proyecto / "app" / "src" / "main.kt").write_text("fun main() {}", encoding="utf-8")
    (proyecto / "node_modules" / "x.js").write_text("basura", encoding="utf-8")
    nova = crear_nova(["FINAL: Es una app que enlaza el celular con la PC usando Firebase."])
    assert "Firebase" in nova.procesar(f"analiza la carpeta {proyecto}")
    visto = nova.motor.recibidos[0][-1]["content"]
    assert "Enlaza el celular" in visto and "novora-link" in visto and "main.kt" in visto
    assert "node_modules" not in visto
    assert nova.herramientas.leyo_privado


def test_analizar_carpeta_que_no_existe(crear_nova, tmp_path):
    nova = crear_nova()
    assert "No encuentro" in nova.procesar(f"analiza la carpeta {tmp_path / 'no-existe'}")


def test_onedrive_no_cuenta_como_enlace():
    import os
    from types import SimpleNamespace

    from nova.politica import es_nube

    assert es_nube(SimpleNamespace(st_reparse_tag=0x9000701A))
    assert not es_nube(SimpleNamespace(st_reparse_tag=0xA000000C))  # enlace simbólico
    assert not es_nube(SimpleNamespace(st_reparse_tag=0xA0000003))  # junction
    assert not es_nube(os.stat("."))


def test_piensalo_bien_usa_el_cerebro_grande(config, internet_falso):
    from nova.motor import MotorSimulado

    local, grande = MotorSimulado(["FINAL: local"]), MotorSimulado(["FINAL: Respuesta pensada a fondo."])
    grande.vision = False
    nova = Nova(config, motor=local, internet=internet_falso, cerebro=grande)
    try:
        assert nova.procesar("Piénsalo bien: ¿cómo organizo NOVORA-LINK?") == "Respuesta pensada a fondo."
        assert grande.recibidos and not local.recibidos
        assert grande.recibidos[0][-1]["content"] == "¿cómo organizo NOVORA-LINK?"
        assert nova.procesar("hola") == "local"  # lo demás sigue en el modelo de la PC
        assert "piénsalo bien" in local.recibidos[0][0]["content"]  # sabe que puede sugerirlo
    finally:
        nova.cerrar()
        nova.memoria.cerrar()


def test_si_el_cerebro_grande_no_responde_piensa_local(config, internet_falso):
    from nova.motor import MotorSimulado

    class Caido(MotorSimulado):
        modelo = "Qwen3 8B en Oracle"

        def chat(self, *a, **k):
            raise RuntimeError("no responde")

    nova = Nova(config, motor=MotorSimulado(["FINAL: Lo pensé aquí."]), internet=internet_falso, cerebro=Caido())
    eventos = nova.bus.suscribir()
    try:
        assert nova.procesar("piénsalo bien, ¿qué hago?") == "Lo pensé aquí."
        avisos = []
        while not eventos.empty():
            e = eventos.get()
            if e["tipo"] == "aviso":
                avisos.append(e["texto"])
        assert any("No pude usar el cerebro grande" in a for a in avisos)
    finally:
        nova.cerrar()
        nova.memoria.cerrar()


def test_motor_remoto_manda_la_clave(config):
    import http.server
    import threading

    from nova.motor import MotorRemoto

    vistos = []

    class Manejador(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            vistos.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            vistos.append(self.headers.get("Authorization"))
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"FINAL: hola"}}]}\n\ndata: [DONE]\n\n')

    srv = http.server.HTTPServer(("127.0.0.1", 0), Manejador)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        config["cerebro_endpoint"] = f"http://127.0.0.1:{srv.server_address[1]}"
        config["cerebro_clave"] = "secreta"
        m = MotorRemoto(config)
        assert m.disponible()
        assert m.chat([{"role": "user", "content": "hola"}]) == "FINAL: hola"
        assert vistos == ["Bearer secreta"] * 3
    finally:
        srv.shutdown()


def test_no_se_queda_pegada_en_platicas_viejas(crear_nova, config, internet_falso):
    from nova.motor import MotorSimulado

    nova = crear_nova(["FINAL: ok"])
    nova.procesar("Estoy peleando con el error de Gradle en NOVORA-LINK")
    hace_dos_dias = time.time() - 2 * 86400
    with nova.memoria._cerrojo:
        nova.memoria._db.execute("UPDATE conversaciones SET fecha=?", (hace_dos_dias,))
        nova.memoria._db.commit()
    nueva = Nova(config, motor=MotorSimulado(["FINAL: Hola.", "FINAL: Era de Gradle."]), memoria=nova.memoria, internet=internet_falso)
    assert nueva.historial == []  # lo de hace 2 días no entra a la plática
    nueva.procesar("Hola, ¿qué tal el error de Gradle?")
    assert "peleando con el error" not in json.dumps(nueva.motor.recibidos[0], ensure_ascii=False)
    nueva.procesar("¿Te acuerdas del error de Gradle que te dije?")  # si lo pides, sí lo busca
    assert "peleando con el error" in json.dumps(nueva.motor.recibidos[1], ensure_ascii=False)
    assert [m["texto"] for m in nueva.conversacion()][0].startswith("Estoy peleando")  # en pantalla sigue visible
    nueva.cerrar()


def test_cerebro_en_la_nube_para_todo(config, internet_falso):
    from nova.motor import MotorRemoto, MotorSimulado

    config["cerebro_endpoint"] = "https://inference.generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/actions/v1"
    config["cerebro_api_modelo"] = "openai.gpt-oss-120b"
    remoto = MotorRemoto(config)
    assert remoto._url_chat().endswith("/20231130/actions/v1/chat/completions")
    assert remoto._nombre_api() == "openai.gpt-oss-120b" and remoto.disponible()

    config["cerebro_auto"] = True
    grande = MotorSimulado(["FINAL: rápido y listo"])
    nova = Nova(config, motor=MotorSimulado([]), internet=internet_falso, cerebro=grande)
    try:
        assert nova.procesar("revisa este código") == "rápido y listo"
        internet_falso.activo = False
        nova.motor.guion.append("FINAL: sin red, local")
        assert nova.procesar("hola") == "sin red, local"
    finally:
        nova.cerrar()
        nova.memoria.cerrar()
