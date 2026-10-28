from pathlib import Path

from nova.skills import Skills, separar


def nueva(base: Path, nombre: str, descripcion: str, cuerpo: str = "Paso 1.") -> Path:
    d = base / nombre
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {nombre}\ndescription: {descripcion}\n---\n\n{cuerpo}\n", encoding="utf-8")
    return d


def test_frontmatter_de_varias_lineas():
    datos, cuerpo = separar("---\nname: pdf\ndescription: >\n  Lee y llena\n  formularios PDF.\nlicense: MIT\n---\n# PDF\n")
    assert datos == {"name": "pdf", "description": "Lee y llena formularios PDF.", "license": "MIT"}
    assert cuerpo.strip() == "# PDF"


def test_carga_incluidas_y_propias_y_la_propia_gana(tmp_path):
    propia = tmp_path / "mias"
    nueva(propia, "revisar-codigo", "Mi forma de revisar código.", "Usa mis reglas.")
    nueva(propia, "sin-descripcion", "")
    s = Skills([Path(__file__).resolve().parent.parent / "skills", propia])
    todas = s.todas()
    assert {"crear-skill", "mensaje-commit", "explicar-error", "revisar-codigo"} <= set(todas)
    assert "sin-descripcion" not in todas
    assert todas["revisar-codigo"].descripcion == "Mi forma de revisar código."
    assert "Usa mis reglas." in s.leer("revisar-codigo")


def test_ve_skills_nuevas_sin_reiniciar(tmp_path):
    s = Skills([tmp_path])
    assert s.todas() == {}
    nueva(tmp_path, "resumen-reunion", "Resumir una reunión en acuerdos y pendientes.")
    assert list(s.todas()) == ["resumen-reunion"]


def test_elige_por_nombre_o_por_parecido(tmp_path):
    nueva(tmp_path, "revisar-codigo", "Revisar código buscando errores y fallas de seguridad.")
    nueva(tmp_path, "mensaje-commit", "Escribir el mensaje de commit de git.")
    s = Skills([tmp_path])
    assert s.elegir("usa la skill mensaje-commit para esto").nombre == "mensaje-commit"
    assert s.elegir("/revisar-codigo app.py").nombre == "revisar-codigo"
    assert s.elegir("¿me revisas el código de login.py?").nombre == "revisar-codigo"
    assert s.elegir("hazme el mensaje del commit").nombre == "mensaje-commit"
    assert s.elegir("hola, ¿cómo estás?") is None
    assert s.elegir("¿qué hora es?") is None


def test_catalogo_corto(tmp_path):
    for i in range(30):
        nueva(tmp_path, f"skill-{i:02d}", "Una descripción bastante larga " * 10)
    catalogo = Skills([tmp_path]).catalogo(max_chars=600)
    assert len(catalogo) < 1000 and "Otras" in catalogo and "skill-29" in catalogo


def test_archivos_de_la_skill_sin_salirse(tmp_path):
    d = nueva(tmp_path / "s", "plantillas", "Usar plantillas.")
    (d / "plantilla.md").write_text("Hola {nombre}", encoding="utf-8")
    (tmp_path / "secreto.txt").write_text("clave", encoding="utf-8")
    s = Skills([tmp_path / "s"])
    assert "plantilla.md" in s.leer("plantillas")
    assert s.leer("plantillas", "plantilla.md") == "Hola {nombre}"
    assert "dentro de la carpeta" in s.leer("plantillas", "../../secreto.txt")
    assert "No tengo la skill" in s.leer("otra")


def test_nova_usa_la_skill_en_el_prompt(config, crear_nova):
    config["skills_carpetas"] = []
    nova = crear_nova(["FINAL: Agrega login."])
    nova.procesar("usa la skill mensaje-commit para mis cambios")
    sistema = nova.motor.recibidos[0][0]["content"]
    assert "## Mis skills" in sistema and "- revisar-codigo:" in sistema
    assert "## Skill para esta tarea" in sistema and "imperativo" in sistema
    nova.procesar("hola")
    assert "## Skill para esta tarea" not in nova.motor.recibidos[1][0]["content"]


def test_herramienta_usar_skill(config, crear_nova):
    config["skills_carpetas"] = []
    nova = crear_nova(['ACCION: {"herramienta": "usar_skill", "args": {"nombre": "explicar-error"}}', "FINAL: Listo."])
    assert nova.procesar("me salió algo raro") == "Listo."
    resultado = nova.motor.recibidos[1][-1]["content"]
    assert "ver_pantalla" in resultado and '<datos origen="skill explicar-error">' in resultado


def test_orden_rapida_lista_skills(config, crear_nova):
    config["skills_carpetas"] = []
    nova = crear_nova()
    respuesta = nova.procesar("¿qué skills tienes?")
    assert "crear-skill" in respuesta and str(config.rutas.config / "skills") in respuesta
    assert (config.rutas.config / "skills" / "LEEME.txt").is_file()


def test_escribir_en_skills_pide_clic(config, crear_nova):
    config["skills_carpetas"] = []
    nova = crear_nova()
    destino = Path(__file__).resolve().parent.parent / "skills" / "nueva" / "SKILL.md"
    r = nova.herramientas.usar("escribir_archivo", {"ruta": str(destino), "contenido": "---\nname: nueva\n---"})
    assert r.confirmacion and r.confirmacion["solo_clic"] and "carpeta de skills" in r.confirmacion["descripcion"]
    assert not destino.exists()


def test_no_se_activa_con_una_palabra_suelta():
    s = Skills([Path(__file__).resolve().parent.parent / "skills"])
    assert s.elegir("revisa mi correo") is None
    assert s.elegir("tengo un error al compilar la app").nombre == "explicar-error"


def _zip_falso(ruta: Path) -> str:
    import zipfile

    apache = "Apache License\nVersion 2.0"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("skills-main/README.md", "x")
        z.writestr("skills-main/skills/mcp-builder/SKILL.md", "---\nname: mcp-builder\ndescription: Build MCP servers.\n---\nSteps.")
        z.writestr("skills-main/skills/mcp-builder/LICENSE.txt", apache)
        z.writestr("skills-main/skills/mcp-builder/reference/guia.md", "guía")
        z.writestr("skills-main/skills/docx/SKILL.md", "---\nname: docx\ndescription: Word.\nlicense: Proprietary. LICENSE.txt\n---\n")
        z.writestr("skills-main/skills/docx/LICENSE.txt", "All rights reserved. users may not")
        z.writestr("skills-main/skills/mcp-builder/../../../fuera.txt", "malo")
    return ruta.as_uri()


def test_bajar_skills_de_anthropic(tmp_path):
    from nova.skills import bajar_anthropic

    destino = tmp_path / "skills-anthropic"
    (destino / "vieja").mkdir(parents=True)
    r = bajar_anthropic(destino, url=_zip_falso(tmp_path / "skills.zip"))
    assert r["instaladas"] == ["mcp-builder"] and "docx" in r["omitidas"]
    assert not (destino / "vieja").exists() and not (tmp_path / "fuera.txt").exists()
    s = Skills([destino])
    assert s.todas()["mcp-builder"].descripcion.startswith("Crear servidores MCP")  # descripción en español
    assert "reference/guia.md" in s.leer("mcp-builder")
    assert s.elegir("quiero crear un servidor mcp en python").nombre == "mcp-builder"


def test_catalogo_nombra_las_que_no_caben(tmp_path):
    for i in range(10):
        nueva(tmp_path, f"skill-{i}", "Descripción " * 8)
    catalogo = Skills([tmp_path]).catalogo(max_chars=300)
    assert "Otras (ábrelas con usar_skill): " in catalogo and "skill-9" in catalogo


def test_orden_bajar_skills():
    from nova.voz import orden_rapida

    assert orden_rapida("baja las skills de anthropic").accion == "bajar_skills"
    assert orden_rapida("instala todas las skills de agent skills").accion == "bajar_skills"
