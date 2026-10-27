"""Voz de NOVA, toda local.

  oír:     whisper.cpp (whisper-cli) en español
  hablar:  Piper con una voz es_MX
  activar: pulsar para hablar (Ctrl+Alt+Espacio). La palabra «Oye, NOVA»
           con openWakeWord llega en la versión 0.2.

El audio nunca se guarda: se escribe a un archivo temporal solo el tiempo que
tarda whisper en leerlo y se borra en seguida.

Las órdenes rápidas se reconocen sin pasar por el modelo y responden al instante.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path

SIN_VENTANA = 0x08000000 if sys.platform == "win32" else 0


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(re.sub(r"[¡!¿?.,;:]+", " ", t).split())


_ACTIVACION = re.compile(r"^(?:oye|hey|ey|oiga)?\s*nova\b\s*", re.I)


def quitar_activacion(texto: str) -> str:
    return _ACTIVACION.sub("", texto.strip(), count=1).strip(" ,.")


@dataclass
class OrdenRapida:
    accion: str
    args: dict


def _ruta(texto: str) -> str:
    return texto.strip().strip('"«»').rstrip("?.!").strip()


def orden_rapida(texto: str) -> OrdenRapida | None:
    """Reconoce las órdenes rápidas de voz o texto. None si hay que pasarlo al modelo."""
    original = quitar_activacion(texto)
    t = _plano(original)
    if not t:
        return None
    if re.fullmatch(r"(para|alto|silencio|callate|detente|ya)", t):
        return OrdenRapida("parar", {})
    if re.fullmatch(r"(mira|ve|revisa|checa|lee) (la |mi |esta )?pantalla|que (ves|hay) en (la |mi )?pantalla|que ves", t):
        return OrdenRapida("pantalla", {})
    m = re.match(r"^(?:conoce|aprende|indexa|estudia)\s+(?:la |mi |esta )?carpeta\s*:?\s*(.+)$", original, re.I)
    if m:
        return OrdenRapida("carpeta", {"ruta": _ruta(m.group(1))})
    # «analiza la carpeta X», «revisa los archivos de X», «qué hay en X»: la conoce y además la explica.
    m = re.match(r"^(?:analiza|explora|revisa|checa|mira|lee|examina|resume|¿?qu[eé] hay en|expl[ií]came)\s+"
                 r"(?:(?:todos )?(?:los )?archivos (?:de|en) )?(?:la |mi |esta |el )?(?:carpeta|proyecto)?\s*:?\s*"
                 r"([\"«]?(?:[a-z]:[\\/]|~[\\/]|/).+)$", original, re.I)
    if m:
        return OrdenRapida("analizar_carpeta", {"ruta": _ruta(m.group(1))})
    m = re.match(r"^(?:olvida|deja de leer)\s+(?:la |mi )?carpeta\s*:?\s*(.+)$", original, re.I)
    if m:
        return OrdenRapida("carpeta", {"ruta": _ruta(m.group(1)), "quitar": True})
    if re.fullmatch(r"(mira|ve) (esta|la) zona", t):
        return OrdenRapida("zona", {})
    m = re.match(r"^(?:nueva tarea|agrega (?:una )?tarea|tarea)\s*:?\s*(.+)$", original, re.I)
    if m:
        return OrdenRapida("tarea", {"texto": m.group(1).strip()})
    m = re.match(r"^(?:apunta|anota|nota)\s*:?\s*(.+)$", original, re.I)
    if m:
        return OrdenRapida("nota", {"texto": m.group(1).strip()})
    if re.fullmatch(r"(que tengo (para )?hoy|resumen del dia|como va mi dia|que tengo pendiente)", t):
        return OrdenRapida("resumen", {})
    m = re.match(r"^busca en internet\s+(.+)$", original, re.I)
    if m:
        return OrdenRapida("buscar_web", {"consulta": m.group(1).strip()})
    m = re.match(r"^busca en mis (?:documentos|archivos|notas)\s+(.+)$", original, re.I)
    if m:
        return OrdenRapida("buscar_memoria", {"consulta": m.group(1).strip()})
    if re.fullmatch(r"(cuanto espacio (libre )?tengo|que discos tengo|mis discos|mis unidades)", t):
        return OrdenRapida("unidades", {})
    if re.fullmatch(r"(desconectate|apaga (el )?internet|sin internet)", t):
        return OrdenRapida("internet", {"activo": False})
    if re.fullmatch(r"(conectate|prende (el )?internet|activa (el )?internet)", t):
        return OrdenRapida("internet", {"activo": True})
    if re.fullmatch(r"(deja de escuchar|ya no escuches)", t):
        return OrdenRapida("escucha", {"activo": False})
    if re.fullmatch(r"(que sabes de mi|que recuerdas de mi)", t):
        return OrdenRapida("yo", {})
    m = re.match(r"^olvida (?:todo lo de |lo de |que )?(.+)$", t)
    if m:
        return OrdenRapida("olvidar", {"tema": m.group(1)})
    if re.fullmatch(r"(esto no lo recuerdes|modo privado|no recuerdes esto)", t):
        return OrdenRapida("privado", {"activo": True})
    if re.fullmatch(r"(ya puedes recordar|sal del modo privado|termina el modo privado)", t):
        return OrdenRapida("privado", {"activo": False})
    if re.fullmatch(r"(tu estado|estado|que eres|quien eres)", t):
        return OrdenRapida("estado", {})
    if re.fullmatch(r"(conecta|conectar|vincula|empareja) (mi |el )?(celular|telefono|movil)", t):
        return OrdenRapida("celular", {"activo": True})
    if re.fullmatch(r"(desconecta|desvincula) (mis |los |el |mi )?(celulares|celular|telefonos|telefono)", t):
        return OrdenRapida("celular", {"activo": False})
    m = re.match(r"^confirmar?\s+(\d{4})$", t)
    if m:
        return OrdenRapida("confirmar", {"codigo": m.group(1)})
    return None


class Voz:
    def __init__(self, config):
        self.config = config
        self.stt_bin = self._binario(config.voz_stt_binario, ["whisper-cli", "whisper-cpp", "main"], "whisper")
        self.tts_bin = self._binario(config.voz_tts_binario, ["piper"], "piper")
        self.stt_modelo = self._modelo(config.voz_stt_modelo, "whisper")
        self.tts_voz = self._modelo(config.voz_tts_voz, "piper")

    def _binario(self, configurado: str, nombres: list[str], carpeta: str) -> Path | None:
        ext = ".exe" if sys.platform == "win32" else ""
        if configurado and Path(os.path.expandvars(configurado)).is_file():
            return Path(os.path.expandvars(configurado))
        for base in (self.config.rutas.programa / carpeta, self.config.rutas.datos / carpeta):
            for n in nombres:
                if (base / (n + ext)).is_file():
                    return base / (n + ext)
        for n in nombres[:1]:
            w = shutil.which(n)
            if w:
                return Path(w)
        return None

    def _modelo(self, nombre: str, carpeta: str) -> Path | None:
        p = Path(os.path.expandvars(nombre))
        if p.is_absolute():
            return p if p.is_file() else None
        for base in (self.config.rutas.datos / carpeta, self.config.rutas.programa / carpeta, self.config.rutas.modelos):
            if (base / nombre).is_file():
                return base / nombre
        return None

    def capacidades(self) -> dict:
        return {"stt": bool(self.stt_bin and self.stt_modelo), "tts": bool(self.tts_bin and self.tts_voz)}

    def transcribir(self, wav: bytes) -> str:
        if not (self.stt_bin and self.stt_modelo):
            raise RuntimeError("No tengo instalado el oído (whisper.cpp y su modelo).")
        fd, ruta = tempfile.mkstemp(suffix=".wav", dir=self.config.rutas.estado)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(wav)
            r = subprocess.run([str(self.stt_bin), "-m", str(self.stt_modelo), "-l", "es", "-nt", "-np", "-f", ruta],
                               capture_output=True, text=True, timeout=120, creationflags=SIN_VENTANA,
                               encoding="utf-8", errors="replace")
        finally:
            try:
                os.remove(ruta)
            except OSError:
                pass
        if r.returncode != 0:
            raise RuntimeError("whisper falló: " + r.stderr[-300:])
        return " ".join(re.sub(r"\[[^\]]*\]", " ", r.stdout).split())

    def sintetizar(self, texto: str) -> bytes:
        if not (self.tts_bin and self.tts_voz):
            raise RuntimeError("No tengo instalada mi voz (Piper y su voz es_MX).")
        fd, ruta = tempfile.mkstemp(suffix=".wav", dir=self.config.rutas.estado)
        os.close(fd)
        try:
            r = subprocess.run([str(self.tts_bin), "--model", str(self.tts_voz), "--output_file", ruta],
                               input=para_hablar(texto), capture_output=True, text=True, timeout=120,
                               creationflags=SIN_VENTANA, encoding="utf-8")
            if r.returncode != 0:
                raise RuntimeError("Piper falló: " + r.stderr[-300:])
            with open(ruta, "rb") as f:
                return f.read()
        finally:
            try:
                os.remove(ruta)
            except OSError:
                pass


def para_hablar(texto: str, max_chars: int = 600) -> str:
    """Versión hablada: sin bloques de código, sin Markdown y breve."""
    t = re.sub(r"```.*?```", " (te dejo el código en pantalla) ", texto, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"https?://\S+", "el enlace", t)
    t = re.sub(r"[*_#>|]+", " ", t)
    t = " ".join(t.split())
    if len(t) > max_chars:
        corte = t.rfind(". ", 0, max_chars)
        t = t[: corte + 1 if corte > 100 else max_chars] + " El resto te lo dejo en pantalla."
    return t
