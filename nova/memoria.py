"""Memoria de NOVA, toda en un SQLite local.

Capas:
  recuerdos       lo que NOVA sabe de ti (quién eres, cómo hablas, gustos,
                  proyectos, momentos, preferencias) y de sí misma (tipo "nova")
  conocimiento    lo que aprendió buscando en internet, para usarlo sin conexión
  conversaciones  memoria episódica: lo que platicaron
  notas, tareas   organización
  documentos      índice de tus carpetas (búsqueda de texto completo)

Reglas:
  * Nunca guarda contraseñas, claves, tarjetas ni códigos (filtro de secretos).
  * Los recuerdos sobre ti solo salen de lo que tú dices, nunca de páginas,
    archivos o la pantalla.
  * Modo privado: nada se guarda.
  * "Olvida" borra de verdad.
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .rutas import proteger

TIPOS_USUARIO = ("quien", "estilo", "gusto", "proyecto", "momento", "preferencia")
TIPOS = TIPOS_USUARIO + ("nova",)

ESQUEMA = """
CREATE TABLE IF NOT EXISTS recuerdos (
    id INTEGER PRIMARY KEY, tipo TEXT NOT NULL, texto TEXT NOT NULL, fuente TEXT NOT NULL,
    confianza REAL NOT NULL DEFAULT 0.8, importancia REAL NOT NULL DEFAULT 1.0,
    creado REAL NOT NULL, actualizado REAL NOT NULL, clave TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS recuerdos_clave ON recuerdos(clave);
CREATE VIRTUAL TABLE IF NOT EXISTS recuerdos_fts USING fts5(texto, content='recuerdos', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS recuerdos_ai AFTER INSERT ON recuerdos BEGIN
    INSERT INTO recuerdos_fts(rowid, texto) VALUES (new.id, new.texto); END;
CREATE TRIGGER IF NOT EXISTS recuerdos_ad AFTER DELETE ON recuerdos BEGIN
    INSERT INTO recuerdos_fts(recuerdos_fts, rowid, texto) VALUES ('delete', old.id, old.texto); END;
CREATE TRIGGER IF NOT EXISTS recuerdos_au AFTER UPDATE ON recuerdos BEGIN
    INSERT INTO recuerdos_fts(recuerdos_fts, rowid, texto) VALUES ('delete', old.id, old.texto);
    INSERT INTO recuerdos_fts(rowid, texto) VALUES (new.id, new.texto); END;

CREATE TABLE IF NOT EXISTS conocimiento (
    id INTEGER PRIMARY KEY, tema TEXT NOT NULL, texto TEXT NOT NULL, url TEXT NOT NULL, creado REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS conocimiento_fts USING fts5(tema, texto, content='conocimiento', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS conocimiento_ai AFTER INSERT ON conocimiento BEGIN
    INSERT INTO conocimiento_fts(rowid, tema, texto) VALUES (new.id, new.tema, new.texto); END;
CREATE TRIGGER IF NOT EXISTS conocimiento_ad AFTER DELETE ON conocimiento BEGIN
    INSERT INTO conocimiento_fts(conocimiento_fts, rowid, tema, texto) VALUES ('delete', old.id, old.tema, old.texto); END;

CREATE TABLE IF NOT EXISTS conversaciones (
    id INTEGER PRIMARY KEY, sesion TEXT NOT NULL, rol TEXT NOT NULL, texto TEXT NOT NULL, fecha REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS conversaciones_fts USING fts5(texto, content='conversaciones', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS conversaciones_ai AFTER INSERT ON conversaciones BEGIN
    INSERT INTO conversaciones_fts(rowid, texto) VALUES (new.id, new.texto); END;
CREATE TRIGGER IF NOT EXISTS conversaciones_ad AFTER DELETE ON conversaciones BEGIN
    INSERT INTO conversaciones_fts(conversaciones_fts, rowid, texto) VALUES ('delete', old.id, old.texto); END;

CREATE TABLE IF NOT EXISTS notas (id INTEGER PRIMARY KEY, texto TEXT NOT NULL, creado REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tareas (
    id INTEGER PRIMARY KEY, texto TEXT NOT NULL, fecha TEXT, hecha INTEGER NOT NULL DEFAULT 0, creado REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY, ruta TEXT NOT NULL UNIQUE, mtime REAL NOT NULL, tam INTEGER NOT NULL, disponible INTEGER NOT NULL DEFAULT 1
);
CREATE VIRTUAL TABLE IF NOT EXISTS trozos USING fts5(ruta UNINDEXED, doc UNINDEXED, texto, tokenize='unicode61 remove_diacritics 2');

CREATE TABLE IF NOT EXISTS ajustes (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dispositivos (
    id INTEGER PRIMARY KEY, nombre TEXT NOT NULL, huella TEXT NOT NULL UNIQUE, creado REAL NOT NULL, ultimo REAL
);
"""

# ------------------------------------------------------------------ secretos

_SECRETOS = [
    re.compile(r"(contrase(ñ|n)a|password|passwd|pin|clave|token|api[\s_-]?key|secret)\w*\s*(es|:|=|era)\s*\S+", re.I),
    re.compile(r"\b(sk|pk|ghp|gho|xox[bap]|AKIA)[-_A-Za-z0-9]{12,}\b"),
    re.compile(r"\b\d{6}(-\d{6}){7}\b"),  # clave de recuperación de BitLocker
    re.compile(r"(c(ó|o)digo|code|verificaci(ó|o)n|otp)\D{0,20}\b\d{4,8}\b", re.I),
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),
    re.compile(r"\b(clabe|cuenta|iban|cvv|cvc)\b\D{0,15}\d{3,}", re.I),
]


def _luhn(digitos: str) -> bool:
    total, doble = 0, False
    for d in reversed(digitos):
        n = int(d)
        if doble:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
        doble = not doble
    return total % 10 == 0


def tiene_secreto(texto: str) -> bool:
    if any(p.search(texto) for p in _SECRETOS):
        return True
    for m in re.finditer(r"(?:\d[ -]?){13,19}", texto):
        digitos = re.sub(r"\D", "", m.group())
        if 13 <= len(digitos) <= 19 and _luhn(digitos):
            return True
    return False


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", t).split())


def consulta_fts(texto: str) -> str:
    """Convierte texto libre en una consulta FTS5 segura (palabras entre comillas, unidas con OR)."""
    vacias = {"que", "de", "la", "el", "los", "las", "en", "y", "a", "un", "una", "me", "mi", "lo", "es", "por",
              "con", "para", "del", "al", "se", "te", "tu", "yo", "como", "sobre", "sabes", "cual", "the", "of"}
    palabras = [p for p in normalizar(texto).split() if len(p) > 2 and p not in vacias]
    return " OR ".join(f'"{p}"' for p in palabras[:16])


@dataclass
class Recuerdo:
    id: int
    tipo: str
    texto: str
    fuente: str
    confianza: float
    importancia: float
    creado: float


EXT_TEXTO = {".txt", ".md", ".py", ".cs", ".xaml", ".java", ".kt", ".kts", ".js", ".ts", ".tsx", ".jsx", ".json",
             ".csv", ".xml", ".html", ".css", ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".rb", ".php", ".sql",
             ".yml", ".yaml", ".toml", ".ini", ".cfg", ".gradle", ".sln", ".csproj", ".ps1", ".sh", ".bat", ".log"}
CARPETAS_OMITIDAS = {"node_modules", "bin", "obj", "packages", "build", "dist", "target", "__pycache__", "venv", "appdata"}


class Memoria:
    def __init__(self, ruta: Path | str):
        self.ruta = Path(ruta)
        nueva = not self.ruta.exists() and str(ruta) != ":memory:"
        self._db = sqlite3.connect(str(ruta), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._cerrojo = threading.RLock()
        with self._cerrojo:
            self._db.executescript(ESQUEMA)
            self._db.commit()
        if nueva:
            proteger(self.ruta)
        self.privado = False

    def cerrar(self) -> None:
        self._db.close()

    # --------------------------------------------------------- recuerdos

    def recordar(self, tipo: str, texto: str, fuente: str = "usuario", confianza: float = 0.9,
                 importancia: float = 1.0) -> int | None:
        """Guarda un recuerdo. Devuelve su id, o None si no se guardó (privado, secreto, fuente no válida)."""
        texto = " ".join(texto.split()).strip()
        if self.privado or not texto or len(texto) > 500 or tipo not in TIPOS:
            return None
        if tipo in TIPOS_USUARIO and fuente not in {"usuario", "voz"}:
            return None  # una página o un archivo no implanta recuerdos sobre ti
        if tiene_secreto(texto):
            return None
        clave = f"{tipo}:{normalizar(texto)}"
        ahora = time.time()
        with self._cerrojo:
            fila = self._db.execute("SELECT id FROM recuerdos WHERE clave=?", (clave,)).fetchone()
            if fila:
                self._db.execute(
                    "UPDATE recuerdos SET actualizado=?, importancia=MIN(importancia+0.2, 3), confianza=MAX(confianza, ?) WHERE id=?",
                    (ahora, confianza, fila["id"]))
                self._db.commit()
                return fila["id"]
            cur = self._db.execute(
                "INSERT INTO recuerdos(tipo, texto, fuente, confianza, importancia, creado, actualizado, clave) VALUES (?,?,?,?,?,?,?,?)",
                (tipo, texto, fuente, confianza, importancia, ahora, ahora, clave))
            self._db.commit()
            return cur.lastrowid

    def recuerdos(self, tipo: str | None = None, limite: int = 200) -> list[Recuerdo]:
        sql = "SELECT * FROM recuerdos" + (" WHERE tipo=?" if tipo else "") + " ORDER BY importancia DESC, actualizado DESC LIMIT ?"
        with self._cerrojo:
            filas = self._db.execute(sql, ((tipo, limite) if tipo else (limite,))).fetchall()
        return [self._recuerdo(f) for f in filas]

    def buscar_recuerdos(self, texto: str, limite: int = 8) -> list[Recuerdo]:
        q = consulta_fts(texto)
        if not q:
            return []
        with self._cerrojo:
            filas = self._db.execute(
                "SELECT r.* FROM recuerdos_fts f JOIN recuerdos r ON r.id=f.rowid WHERE recuerdos_fts MATCH ? "
                "ORDER BY bm25(recuerdos_fts) - r.importancia LIMIT ?", (q, limite)).fetchall()
        return [self._recuerdo(f) for f in filas]

    def olvidar(self, texto_o_id: str | int) -> int:
        """Olvida un recuerdo por id, o todos los que coincidan con el tema. Devuelve cuántos borró."""
        with self._cerrojo:
            if isinstance(texto_o_id, int) or str(texto_o_id).isdigit():
                n = self._db.execute("DELETE FROM recuerdos WHERE id=?", (int(texto_o_id),)).rowcount
            else:
                ids = [r.id for r in self.buscar_recuerdos(str(texto_o_id), limite=50)]
                n = self._db.execute(f"DELETE FROM recuerdos WHERE id IN ({','.join('?' * len(ids))})", ids).rowcount if ids else 0
            self._db.commit()
            return n

    def olvidar_todo(self) -> None:
        with self._cerrojo:
            for tabla in ("recuerdos", "conversaciones", "conocimiento"):
                self._db.execute(f"DELETE FROM {tabla}")
            self._db.commit()
            self._db.execute("VACUUM")

    def perfil(self, max_chars: int = 1600) -> str:
        """Perfil corto para el prompt: quién eres, cómo te gusta que te hable, proyectos, gustos."""
        titulos = {"quien": "Quién es", "estilo": "Cómo habla", "preferencia": "Cómo le gusta que le hable",
                   "proyecto": "Proyectos", "gusto": "Gustos", "momento": "Momentos recientes"}
        partes = []
        for tipo, titulo in titulos.items():
            limite = 4 if tipo == "momento" else 8
            items = self.recuerdos(tipo, limite)
            if tipo == "momento":
                items = [r for r in items if time.time() - r.creado < 14 * 86400]
            if items:
                partes.append(f"{titulo}: " + "; ".join(r.texto for r in items))
        texto = "\n".join(partes)
        return texto[:max_chars]

    def _recuerdo(self, f: sqlite3.Row) -> Recuerdo:
        return Recuerdo(f["id"], f["tipo"], f["texto"], f["fuente"], f["confianza"], f["importancia"], f["creado"])

    def consolidar(self) -> dict:
        """«Sueño»: baja la importancia de lo viejo y borra lo muy olvidado y poco confiable."""
        ahora = time.time()
        with self._cerrojo:
            self._db.execute("UPDATE recuerdos SET importancia=importancia*0.9 WHERE actualizado < ?", (ahora - 30 * 86400,))
            borrados = self._db.execute(
                "DELETE FROM recuerdos WHERE importancia < 0.2 AND confianza < 0.6").rowcount
            viejas = self._db.execute("DELETE FROM conversaciones WHERE fecha < ?", (ahora - 365 * 86400,)).rowcount
            self._db.commit()
        return {"recuerdos_borrados": borrados, "mensajes_viejos_borrados": viejas}

    # ----------------------------------------------------- conversaciones

    def anotar_mensaje(self, sesion: str, rol: str, texto: str) -> None:
        if self.privado or tiene_secreto(texto):
            return
        with self._cerrojo:
            self._db.execute("INSERT INTO conversaciones(sesion, rol, texto, fecha) VALUES (?,?,?,?)",
                             (sesion, rol, texto[:4000], time.time()))
            self._db.commit()

    def buscar_conversaciones(self, texto: str, limite: int = 4) -> list[dict]:
        q = consulta_fts(texto)
        if not q:
            return []
        with self._cerrojo:
            filas = self._db.execute(
                "SELECT c.rol, c.texto, c.fecha FROM conversaciones_fts f JOIN conversaciones c ON c.id=f.rowid "
                "WHERE conversaciones_fts MATCH ? ORDER BY bm25(conversaciones_fts) LIMIT ?", (q, limite)).fetchall()
        return [dict(f) for f in filas]

    def ultimos_mensajes(self, limite: int = 10) -> list[dict]:
        with self._cerrojo:
            filas = self._db.execute("SELECT sesion, rol, texto, fecha FROM conversaciones ORDER BY id DESC LIMIT ?", (limite,)).fetchall()
        return [dict(f) for f in reversed(filas)]

    # -------------------------------------------------------- conocimiento

    def aprender(self, tema: str, texto: str, url: str) -> None:
        if self.privado or tiene_secreto(texto):
            return
        with self._cerrojo:
            self._db.execute("INSERT INTO conocimiento(tema, texto, url, creado) VALUES (?,?,?,?)",
                             (tema[:300], texto[:6000], url[:1000], time.time()))
            self._db.commit()

    def buscar_conocimiento(self, texto: str, limite: int = 4) -> list[dict]:
        q = consulta_fts(texto)
        if not q:
            return []
        with self._cerrojo:
            filas = self._db.execute(
                "SELECT k.tema, k.texto, k.url, k.creado FROM conocimiento_fts f JOIN conocimiento k ON k.id=f.rowid "
                "WHERE conocimiento_fts MATCH ? ORDER BY bm25(conocimiento_fts) LIMIT ?", (q, limite)).fetchall()
        return [dict(f) for f in filas]

    # ------------------------------------------------------- notas y tareas

    def nota(self, texto: str) -> int:
        with self._cerrojo:
            cur = self._db.execute("INSERT INTO notas(texto, creado) VALUES (?,?)", (texto.strip(), time.time()))
            self._db.commit()
            return cur.lastrowid

    def notas(self, limite: int = 50) -> list[dict]:
        with self._cerrojo:
            return [dict(f) for f in self._db.execute("SELECT * FROM notas ORDER BY id DESC LIMIT ?", (limite,))]

    def tarea(self, texto: str, fecha: str | None = None) -> int:
        with self._cerrojo:
            cur = self._db.execute("INSERT INTO tareas(texto, fecha, creado) VALUES (?,?,?)", (texto.strip(), fecha, time.time()))
            self._db.commit()
            return cur.lastrowid

    def tareas(self, pendientes: bool = True) -> list[dict]:
        sql = "SELECT * FROM tareas" + (" WHERE hecha=0" if pendientes else "") + " ORDER BY fecha IS NULL, fecha, id"
        with self._cerrojo:
            return [dict(f) for f in self._db.execute(sql)]

    def completar(self, id_tarea: int) -> bool:
        with self._cerrojo:
            n = self._db.execute("UPDATE tareas SET hecha=1 WHERE id=?", (id_tarea,)).rowcount
            self._db.commit()
            return n > 0

    # ----------------------------------------------------------- documentos

    def indexar(self, carpetas: Iterable[str], revisar=None, max_bytes: int = 200_000) -> dict:
        """Indexa archivos de texto. `revisar(ruta)->ChequeoRuta` aplica la política de rutas."""
        nuevos = omitidos = 0
        for carpeta in carpetas:
            if revisar and not revisar(carpeta).ok:
                omitidos += 1
                continue
            for raiz, dirs, archivos in os.walk(carpeta, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in CARPETAS_OMITIDAS
                           and not os.path.islink(os.path.join(raiz, d))]
                for nombre in archivos:
                    ruta = os.path.join(raiz, nombre)
                    if nombre.startswith(".") or Path(nombre).suffix.lower() not in EXT_TEXTO or os.path.islink(ruta):
                        continue
                    if revisar and not revisar(ruta).ok:
                        omitidos += 1
                        continue
                    if self._indexar_archivo(ruta, max_bytes):
                        nuevos += 1
        return {"indexados": nuevos, "omitidos": omitidos}

    def _indexar_archivo(self, ruta: str, max_bytes: int) -> bool:
        try:
            st = os.stat(ruta)
        except OSError:
            return False
        if st.st_size > max_bytes:
            return False
        with self._cerrojo:
            fila = self._db.execute("SELECT id, mtime, tam FROM documentos WHERE ruta=?", (ruta,)).fetchone()
            if fila and fila["mtime"] == st.st_mtime and fila["tam"] == st.st_size:
                return False
            try:
                with open(ruta, encoding="utf-8", errors="replace") as f:
                    texto = f.read()
            except OSError:
                return False
            if fila:
                self._db.execute("DELETE FROM trozos WHERE doc=?", (fila["id"],))
                self._db.execute("UPDATE documentos SET mtime=?, tam=?, disponible=1 WHERE id=?", (st.st_mtime, st.st_size, fila["id"]))
                doc = fila["id"]
            else:
                doc = self._db.execute("INSERT INTO documentos(ruta, mtime, tam) VALUES (?,?,?)", (ruta, st.st_mtime, st.st_size)).lastrowid
            for trozo in _trocear(texto):
                self._db.execute("INSERT INTO trozos(ruta, doc, texto) VALUES (?,?,?)", (ruta, doc, trozo))
            self._db.commit()
            return True

    def buscar_documentos(self, texto: str, limite: int = 6) -> list[dict]:
        q = consulta_fts(texto)
        if not q:
            return []
        with self._cerrojo:
            filas = self._db.execute(
                "SELECT ruta, snippet(trozos, 2, '«', '»', '…', 24) AS fragmento, texto FROM trozos WHERE trozos MATCH ? "
                "ORDER BY bm25(trozos) LIMIT ?", (q, limite)).fetchall()
        return [dict(f) for f in filas]

    def estado(self) -> dict:
        with self._cerrojo:
            contar = lambda t: self._db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: E731
            return {"recuerdos": contar("recuerdos"), "conocimiento": contar("conocimiento"),
                    "mensajes": contar("conversaciones"), "notas": contar("notas"),
                    "tareas_pendientes": self._db.execute("SELECT COUNT(*) FROM tareas WHERE hecha=0").fetchone()[0],
                    "documentos": contar("documentos")}

    # ------------------------------------------------------- dispositivos

    def agregar_dispositivo(self, nombre: str, huella: str) -> int:
        with self._cerrojo:
            cur = self._db.execute("INSERT INTO dispositivos(nombre, huella, creado) VALUES (?,?,?)", (nombre[:80], huella, time.time()))
            self._db.commit()
            return cur.lastrowid

    def dispositivo_por_huella(self, huella: str) -> dict | None:
        with self._cerrojo:
            f = self._db.execute("SELECT id, nombre FROM dispositivos WHERE huella=?", (huella,)).fetchone()
            if f:
                self._db.execute("UPDATE dispositivos SET ultimo=? WHERE id=?", (time.time(), f["id"]))
                self._db.commit()
        return dict(f) if f else None

    def dispositivos(self) -> list[dict]:
        with self._cerrojo:
            return [dict(f) for f in self._db.execute("SELECT id, nombre, creado, ultimo FROM dispositivos ORDER BY id")]

    def quitar_dispositivos(self) -> int:
        with self._cerrojo:
            n = self._db.execute("DELETE FROM dispositivos").rowcount
            self._db.commit()
            return n

    # ----------------------------------------------------------- ajustes

    def ajuste(self, clave: str, defecto: str | None = None) -> str | None:
        with self._cerrojo:
            f = self._db.execute("SELECT valor FROM ajustes WHERE clave=?", (clave,)).fetchone()
        return f["valor"] if f else defecto

    def fijar(self, clave: str, valor: str) -> None:
        with self._cerrojo:
            self._db.execute("INSERT INTO ajustes(clave, valor) VALUES (?,?) ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor", (clave, valor))
            self._db.commit()


def _trocear(texto: str, tam: int = 1200, solape: int = 150) -> list[str]:
    if len(texto) <= tam:
        return [texto] if texto.strip() else []
    trozos, i = [], 0
    while i < len(texto):
        trozos.append(texto[i : i + tam])
        i += tam - solape
    return trozos
