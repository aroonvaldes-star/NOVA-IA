"""Skills: carpetas con un SKILL.md que le enseñan a NOVA a hacer algo (formato Agent Skills).

Cada skill es una carpeta con un SKILL.md:

    mi-skill/
      SKILL.md        ---\\nname: mi-skill\\ndescription: qué hace y cuándo usarla\\n---\\ninstrucciones…
      scripts/ …      (opcional) archivos que la skill menciona

NOVA las busca en, de menor a mayor prioridad (si dos se llaman igual, gana la última):
  1. la carpeta skills/ de NOVA (las que vienen incluidas; puedes poner las tuyas ahí también),
  2. %LOCALAPPDATA%\\NOVA\\skills-anthropic (las de github.com/anthropics/skills, con «nova skills bajar»),
  3. la carpeta de configuración: %APPDATA%\\NOVA\\skills,
  4. las carpetas de skills_carpetas en config.json (por defecto ~/.claude/skills).

Si junto al SKILL.md hay un descripcion-es.txt, NOVA usa esa descripción: así una skill en inglés
se elige bien cuando le hablas en español.

Para que el modelo chico no se llene, se carga poco a poco, como en Agent Skills:
en el prompt solo va el catálogo (nombre y descripción corta); el cuerpo de una
skill entra cuando la pides por nombre, cuando tu mensaje se parece mucho a su
descripción, o cuando el modelo la abre con la herramienta usar_skill.

Las skills son instrucciones que tú instalas, como personalidad.md: NOVA las sigue.
Instala solo las que conozcas; los scripts que traen nunca se corren solos (pasan
por la herramienta orden y su confirmación).
"""

from __future__ import annotations

import io
import os
import re
import shutil
import unicodedata
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

INCLUIDAS = Path(__file__).resolve().parent.parent / "skills"
MAX_SKILL_MD = 64_000
DESCRIPCION_ES = "descripcion-es.txt"
ANTHROPIC_ZIP = "https://codeload.github.com/anthropics/skills/zip/refs/heads/main"
# Descripciones en español para las skills de anthropics/skills: el modelo local elige mejor con ellas.
DESCRIPCIONES_ANTHROPIC = {
    "academy-guide": "Recomendar cursos y tutoriales de Claude Academy cuando preguntan cómo aprender a usar Claude.",
    "algorithmic-art": "Crear arte generativo con código (p5.js): patrones, partículas, fractales y animaciones.",
    "brand-guidelines": "Aplicar los colores y la tipografía de la marca Anthropic a un documento o página.",
    "canvas-design": "Diseñar pósters, portadas, ilustraciones o imágenes estáticas en PNG o PDF.",
    "claude-api": "Programar apps que usan la API de Claude (Anthropic SDK): modelos, herramientas, streaming, caché.",
    "discernment-nudge": "Después de un consejo, plan o análisis importante, sugerir qué revisar antes de actuar.",
    "doc-coauthoring": "Escribir juntos documentación, propuestas, especificaciones técnicas o documentos de decisión.",
    "frontend-design": "Diseño visual de interfaces, páginas web y apps: estilo, tipografía, colores, que no se vea genérico.",
    "internal-comms": "Redactar comunicados internos: reportes de avance, actualizaciones, boletines, FAQ, reportes de incidentes.",
    "mcp-builder": "Crear servidores MCP (Model Context Protocol) en Python o TypeScript para conectar una IA con APIs.",
    "skill-creator": "Crear, mejorar y evaluar skills (guía completa en inglés, con pruebas y scripts).",
    "slack-gif-creator": "Crear GIFs animados para Slack con Python.",
    "theme-factory": "Aplicar un tema de colores y tipografías a presentaciones, documentos o páginas HTML.",
    "web-artifacts-builder": "Hacer páginas web complejas con React, Tailwind y shadcn/ui.",
    "webapp-testing": "Probar una aplicación web local con Playwright: revisar pantallas, errores y capturas.",
}
NOMBRE_VALIDO = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# «usa la skill revisar-codigo …», «con la habilidad X», o empezar con «/revisar-codigo».
PIDE_SKILL = re.compile(r"(?:^\s*/([\w-]+))|(?:\b(?:usa|usando|con|aplica|sigue)\s+(?:la\s+|tu\s+)?(?:skill|habilidad)\s+[«\"']?([\w-]+))", re.I)
VACIAS = set("""para como esta este esto estos estas pero porque cuando donde sobre entre desde hasta tiene tienen
hacer puede puedes quiero quieres necesito ayuda ayudame favor tambien todos todas todo cosas cualquier mismo
with that this from have when your what which into uses used using also more other only than then them they
their there these those will would should could about after before every skill skills""".split())


@dataclass
class Skill:
    nombre: str
    descripcion: str
    carpeta: Path
    archivo: Path

    def cuerpo(self) -> str:
        try:
            texto = self.archivo.read_text(encoding="utf-8", errors="replace")[:MAX_SKILL_MD]
        except OSError:
            return ""
        return separar(texto)[1].strip()

    def archivos(self, limite: int = 20) -> list[str]:
        """Los demás archivos de la skill (rutas relativas), para que el modelo sepa que existen."""
        salida = []
        for raiz, dirs, nombres in os.walk(self.carpeta):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
            for n in sorted(nombres):
                ruta = Path(raiz) / n
                if ruta != self.archivo and not n.startswith("."):
                    salida.append(ruta.relative_to(self.carpeta).as_posix())
                    if len(salida) >= limite:
                        return salida
        return salida


def separar(texto: str) -> tuple[dict[str, str], str]:
    """Separa el frontmatter (subconjunto de YAML: clave: valor, con > o | para varias líneas) del cuerpo."""
    texto = texto.lstrip("﻿")
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", texto, re.S)
    if not m:
        return {}, texto
    datos: dict[str, str] = {}
    clave, varias = None, []
    for linea in m.group(1).splitlines():
        if clave and (linea.startswith((" ", "\t")) or not linea.strip()):
            varias.append(linea.strip())
            continue
        if clave:
            datos[clave] = " ".join(p for p in varias if p)
            clave, varias = None, []
        par = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", linea)
        if not par:
            continue
        k, v = par.group(1).lower(), par.group(2).strip()
        if v in {"", ">", "|", ">-", "|-"}:
            clave, varias = k, []
        else:
            datos[k] = v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'" else v
    if clave:
        datos[clave] = " ".join(p for p in varias if p)
    return datos, texto[m.end():]


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _raices(texto: str, minimo: int = 4) -> set[str]:
    """Raíces de 5 letras de las palabras con significado (revisar, revisa, revisión → revis)."""
    return {p[:5] for p in re.findall(r"[a-z0-9]+", _plano(texto)) if len(p) >= minimo and p not in VACIAS}


class Skills:
    def __init__(self, carpetas: list[Path], al_final: list[Path] | None = None):
        self.carpetas = carpetas
        self.al_final = set(al_final or [])  # sus skills van al final del catálogo (las descargadas)
        self._firma: tuple = ()
        self._todas: dict[str, Skill] = {}

    @classmethod
    def desde_config(cls, config) -> "Skills":
        carpetas = [INCLUIDAS, descargadas(config), config.rutas.config / "skills"]
        for c in config.skills_carpetas or []:
            carpetas.append(Path(os.path.expandvars(os.path.expanduser(str(c)))))
        return cls(carpetas, al_final=[carpetas[1]])

    @property
    def propia(self) -> Path:
        """La carpeta donde el usuario pone sus skills (se crea con un LEEME)."""
        return self.carpetas[2] if len(self.carpetas) > 2 else self.carpetas[-1]

    def contiene(self, ruta: Path) -> bool:
        """¿La ruta cae dentro de alguna carpeta de skills?"""
        try:
            r = Path(ruta).resolve()
        except OSError:
            return True
        for base in self.carpetas:
            try:
                r.relative_to(base.resolve())
                return True
            except (OSError, ValueError):
                continue
        return False

    def preparar(self) -> None:
        try:
            self.propia.mkdir(parents=True, exist_ok=True)
            leeme = self.propia / "LEEME.txt"
            if not leeme.exists():
                leeme.write_text(
                    "Pon aquí tus skills: una carpeta por skill con un archivo SKILL.md adentro.\n"
                    "El SKILL.md empieza así:\n\n---\nname: mi-skill\ndescription: Qué hace y cuándo usarla.\n---\n"
                    "Instrucciones para NOVA…\n\nNOVA las ve sin reiniciar. Dile «qué skills tienes» para ver la lista.\n",
                    encoding="utf-8")
        except OSError:
            pass

    def _archivos(self) -> list[Path]:
        salida = []
        for base in self.carpetas:
            try:
                hijos = sorted(base.iterdir())
            except OSError:
                continue
            for d in hijos:
                if not d.is_dir() or d.name.startswith("."):
                    continue
                for n in ("SKILL.md", "skill.md", "Skill.md"):
                    if (d / n).is_file():
                        salida.append(d / n)
                        break
        return salida

    def todas(self) -> dict[str, Skill]:
        """Vuelve a mirar las carpetas en cada llamada: si agregas o cambias una skill, NOVA la ve sin reiniciar."""
        archivos = self._archivos()
        firma = tuple((str(a), _mtime(a), _mtime(a.parent / DESCRIPCION_ES)) for a in archivos)
        if firma == self._firma:
            return self._todas
        todas: dict[str, Skill] = {}
        for a in archivos:
            try:
                with a.open(encoding="utf-8", errors="replace") as f:
                    datos, _ = separar(f.read(MAX_SKILL_MD))
            except OSError:
                continue
            nombre = (datos.get("name") or a.parent.name).strip().lower()
            descripcion = datos.get("description", "")
            try:
                descripcion = (a.parent / DESCRIPCION_ES).read_text(encoding="utf-8")[:500] or descripcion
            except OSError:
                pass
            descripcion = re.sub(r"\s+", " ", descripcion).strip()
            if not NOMBRE_VALIDO.match(nombre) or not descripcion:
                continue  # sin nombre válido o sin descripción no se puede elegir bien
            todas[nombre] = Skill(nombre, descripcion, a.parent, a)
        self._firma, self._todas = firma, dict(sorted(todas.items()))
        return self._todas

    def buscar(self, nombre: str) -> Skill | None:
        todas = self.todas()
        clave = _plano(nombre).strip().strip("«»\"'/").replace(" ", "-").replace("_", "-")
        return todas.get(clave)

    def catalogo(self, max_chars: int = 1400, max_desc: int = 90) -> str:
        """Lista corta para el prompt: «- nombre: descripción». Estable entre turnos (sirve a la caché)."""
        lineas, total, sobran = [], 0, []
        for s in sorted(self.todas().values(), key=lambda s: (s.carpeta.parent in self.al_final, s.nombre)):
            d = s.descripcion if len(s.descripcion) <= max_desc else s.descripcion[:max_desc].rsplit(" ", 1)[0] + "…"
            linea = f"- {s.nombre}: {d}"
            if sobran or total + len(linea) > max_chars:
                sobran.append(s.nombre)  # ya no caben con descripción: solo el nombre
                continue
            lineas.append(linea)
            total += len(linea) + 1
        if sobran:
            lineas.append("- Otras (ábrelas con usar_skill): " + ", ".join(sobran))
        return "\n".join(lineas)

    def elegir(self, texto: str) -> Skill | None:
        """La skill que pide este mensaje: por nombre, o la que más se parece si se parece mucho."""
        todas = self.todas()
        if not todas:
            return None
        for m in PIDE_SKILL.finditer(texto):
            s = self.buscar(m.group(1) or m.group(2))
            if s:
                return s
        palabras = _raices(texto, 3)
        if not palabras:
            return None
        puntos = []
        for s in todas.values():
            del_nombre = _raices(s.nombre.replace("-", " "), 3)
            en_nombre = len(palabras & del_nombre)
            en_desc = len(palabras & (_raices(s.descripcion, 5) - del_nombre))
            puntos.append((3 * en_nombre + en_desc, en_nombre, s))
        puntos.sort(key=lambda p: (p[0], p[1]), reverse=True)
        mejor = puntos[0]
        empate = len(puntos) > 1 and puntos[1][0] == mejor[0]
        # Con el nombre y algo más, o con tres palabras de la descripción; si empata con otra, mejor no adivinar.
        if not empate and (mejor[1] and mejor[0] >= 4 or mejor[0] >= 3 and not mejor[1]):
            return mejor[2]
        return None

    def instrucciones(self, s: Skill, max_chars: int = 2500) -> str:
        """El cuerpo de la skill para el prompt, recortado, con la lista de sus archivos."""
        cuerpo = s.cuerpo()
        if len(cuerpo) > max_chars:
            cuerpo = cuerpo[:max_chars].rsplit("\n", 1)[0] + "\n[… recortado; pide más con usar_skill y un archivo]"
        extra = s.archivos()
        if extra:
            cuerpo += f"\n\nArchivos de la skill (en {s.carpeta}; léelos con usar_skill {{\"nombre\": \"{s.nombre}\", \"archivo\": \"…\"}}): " + ", ".join(extra)
        return cuerpo

    def leer(self, nombre: str, archivo: str = "", max_chars: int = 2500, max_bytes: int = 200_000) -> str:
        s = self.buscar(nombre)
        if not s:
            disponibles = ", ".join(self.todas()) or "ninguna"
            return f"No tengo la skill «{nombre}». Tengo: {disponibles}."
        if not archivo:
            return f"Skill {s.nombre}: {s.descripcion}\n\n" + self.instrucciones(s, max_chars)
        try:
            ruta = (s.carpeta / archivo).resolve()
            ruta.relative_to(s.carpeta.resolve())
        except (OSError, ValueError):
            return "Solo puedo leer archivos que están dentro de la carpeta de la skill."
        if not ruta.is_file():
            return f"La skill {s.nombre} no tiene el archivo {archivo}."
        if ruta.stat().st_size > max_bytes:
            return f"{archivo} es muy grande para leerlo completo ({ruta.stat().st_size // 1024} KB)."
        texto = ruta.read_text(encoding="utf-8", errors="replace")
        return texto if len(texto) <= max_chars * 2 else texto[:max_chars * 2] + "\n[… recortado]"

    def resumen(self) -> str:
        """Para «qué skills tienes»."""
        todas = self.todas()
        if not todas:
            return (f"Todavía no tengo skills. Pon una carpeta con un SKILL.md en {self.propia} "
                    "(o en la carpeta skills de NOVA) y la veo sin reiniciar.")
        filas = [f"- {s.nombre}: {s.descripcion}" for s in todas.values()]
        return (f"Tengo {len(todas)} skill(s):\n" + "\n".join(filas) +
                f"\n\nPara usar una dime «usa la skill nombre …». Agrega las tuyas en {self.propia}.")


def _mtime(ruta: Path) -> float:
    try:
        return ruta.stat().st_mtime
    except OSError:
        return 0.0


def descargadas(config) -> Path:
    return config.rutas.datos / "skills-anthropic"


def bajar_anthropic(destino: Path, url: str = ANTHROPIC_ZIP, timeout: float = 60, max_mb: int = 80) -> dict:
    """Baja las skills de github.com/anthropics/skills a destino (reemplaza lo que había).

    Se salta las que tienen licencia restringida (docx, pdf, pptx, xlsx: solo se pueden usar dentro de los
    servicios de Anthropic, no con otro modelo). Nada se ejecuta: solo se guardan archivos.
    """
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "NOVA"}), timeout=timeout) as r:
        datos = r.read(max_mb * 1024 * 1024 + 1)
    if len(datos) > max_mb * 1024 * 1024:
        raise ValueError(f"la descarga pasa de {max_mb} MB")
    zf = zipfile.ZipFile(io.BytesIO(datos))
    grupos: dict[str, list[tuple[zipfile.ZipInfo, str]]] = {}
    for info in zf.infolist():
        partes = info.filename.split("/")
        # <raíz>/skills/<nombre>/<ruta…>
        if len(partes) < 4 or partes[1] != "skills" or info.is_dir() or not partes[-1]:
            continue
        nombre, relativa = partes[2], "/".join(partes[3:])
        if not NOMBRE_VALIDO.match(nombre) or any(p in {"", ".", ".."} for p in partes[3:]) or "\\" in relativa or ":" in relativa:
            continue
        if (info.external_attr >> 16) & 0o170000 == 0o120000:  # enlace simbólico
            continue
        grupos.setdefault(nombre, []).append((info, relativa))
    instaladas, omitidas = [], {}
    temporal = destino.with_name(destino.name + ".nuevo")
    shutil.rmtree(temporal, ignore_errors=True)
    for nombre, archivos in sorted(grupos.items()):
        rel = {r: i for i, r in archivos}
        if "SKILL.md" not in rel:
            continue
        licencia = zf.read(rel["LICENSE.txt"]).decode("utf-8", "replace") if "LICENSE.txt" in rel else ""
        cabecera = separar(zf.read(rel["SKILL.md"])[:MAX_SKILL_MD].decode("utf-8", "replace"))[0]
        if "proprietary" in cabecera.get("license", "").lower() or (licencia and "Apache License" not in licencia):
            omitidas[nombre] = "licencia restringida a los servicios de Anthropic"
            continue
        for relativa, info in rel.items():
            if info.file_size > 20 * 1024 * 1024:
                continue
            ruta = temporal / nombre / relativa
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_bytes(zf.read(info))
        if nombre in DESCRIPCIONES_ANTHROPIC:
            (temporal / nombre / DESCRIPCION_ES).write_text(DESCRIPCIONES_ANTHROPIC[nombre], encoding="utf-8")
        instaladas.append(nombre)
    if not instaladas:
        shutil.rmtree(temporal, ignore_errors=True)
        raise ValueError("la descarga no traía skills")
    shutil.rmtree(destino, ignore_errors=True)
    temporal.rename(destino)
    return {"instaladas": instaladas, "omitidas": omitidas, "carpeta": str(destino)}
