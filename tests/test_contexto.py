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
