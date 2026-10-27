"""Herramientas que NOVA puede usar mientras piensa.

Todo lo que devuelven es DATO para el modelo, nunca instrucción. Lo que
cambia cosas (escribir, órdenes que no son de solo lectura, abrir una URL que
no salió de ti ni de una búsqueda) se convierte en una propuesta con código de
confirmación.
"""

from __future__ import annotations

import ast
import operator
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import politica
from .confirmaciones import Confirmaciones
from .internet import Internet, URLNoPermitida
from .memoria import Memoria


@dataclass
class Resultado:
    texto: str
    origen: str = "herramienta"
    confirmacion: dict | None = None  # {"codigo", "descripcion", "solo_clic"}
    uso_internet: list[str] = field(default_factory=list)


def envolver(origen: str, texto: str) -> str:
    """Pone el resultado entre <datos> y neutraliza cualquier cierre falso dentro."""
    limpio = re.sub(r"</?\s*datos[^>]*>", "[etiqueta eliminada]", texto, flags=re.I)
    return f'<datos origen="{origen}">\n{limpio}\n</datos>'


# ----------------------------------------------------------------- calcular

_OPS: dict[type, Callable] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def calcular(expresion: str) -> float | int:
    """Aritmética segura (sin eval)."""
    def ev(n: ast.AST):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
                raise ValueError("potencia demasiado grande")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("expresión no permitida")
    if len(expresion) > 200:
        raise ValueError("expresión demasiado larga")
    return ev(ast.parse(expresion.replace("^", "**").replace(",", ""), mode="eval"))


# ----------------------------------------------------------------- unidades

def unidades() -> list[dict]:
    """Unidades con letra (Windows) o la raíz y la carpeta personal (otros sistemas), con espacio libre."""
    salida = []
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.windll.kernel32
        mascara = k32.GetLogicalDrives()
        tipos = {2: "extraíble", 3: "interna", 4: "red", 5: "CD/DVD", 6: "RAM"}
        for i in range(26):
            if not mascara & (1 << i):
                continue
            letra = f"{chr(65 + i)}:\\"
            tipo = tipos.get(k32.GetDriveTypeW(letra), "desconocida")
            nombre = ctypes.create_unicode_buffer(261)
            fs = ctypes.create_unicode_buffer(261)
            k32.GetVolumeInformationW(letra, nombre, 261, None, None, None, fs, 261)
            info = {"unidad": letra[:2], "nombre": nombre.value, "tipo": tipo, "sistema": fs.value}
            try:
                uso = shutil.disk_usage(letra)
                info.update(total_gb=round(uso.total / 1e9, 1), libre_gb=round(uso.free / 1e9, 1),
                            libre_pct=round(100 * uso.free / uso.total, 1) if uso.total else 0)
            except OSError:
                info["estado"] = "no disponible (¿BitLocker bloqueado o unidad vacía?)"
            salida.append(info)
    else:
        for ruta in sorted({"/", str(Path.home())}):
            uso = shutil.disk_usage(ruta)
            salida.append({"unidad": ruta, "total_gb": round(uso.total / 1e9, 1), "libre_gb": round(uso.free / 1e9, 1),
                           "libre_pct": round(100 * uso.free / uso.total, 1) if uso.total else 0})
    return salida


# ----------------------------------------------------------------- caja

class CajaDeHerramientas:
    def __init__(self, config, memoria: Memoria, internet: Internet, confirmaciones: Confirmaciones):
        self.config = config
        self.memoria = memoria
        self.internet = internet
        self.confirmaciones = confirmaciones
        self.urls_permitidas: set[str] = set()
        self.leyo_privado = False

    def nueva_tarea(self, mensaje_usuario: str) -> None:
        """Al empezar un turno: las URLs que escribiste tú quedan permitidas."""
        self.urls_permitidas = set(re.findall(r"https?://[^\s<>\"']+", mensaje_usuario))
        self.leyo_privado = False

    def usar(self, nombre: str, args: dict[str, Any]) -> Resultado:
        metodo = getattr(self, "h_" + nombre, None)
        if not metodo or not isinstance(args, dict):
            return Resultado(f"Herramienta desconocida o argumentos inválidos: {nombre}")
        try:
            return metodo(**{k: v for k, v in args.items() if isinstance(k, str)})
        except TypeError as exc:
            return Resultado(f"Argumentos inválidos para {nombre}: {exc}")
        except Exception as exc:  # noqa: BLE001 - el error vuelve al modelo como dato
            return Resultado(f"Error en {nombre}: {exc}")

    # --- internet

    def h_buscar_web(self, consulta: str) -> Resultado:
        if not self.internet.activo:
            return Resultado("Internet está desactivado.", "web")
        if self.leyo_privado:
            return self._proponer(f"Buscar en internet «{consulta}» (acabo de leer un archivo tuyo)",
                                  lambda: self._buscar(consulta), solo_clic=True)
        return self._buscar(consulta)

    def _buscar(self, consulta: str) -> Resultado:
        try:
            resultados = self.internet.buscar(consulta, self.config.internet_max_paginas)
        except (OSError, ConnectionError, ValueError) as exc:
            return Resultado(f"Sin conexión o la búsqueda falló: {exc}", "web")
        if not resultados:
            return Resultado("La búsqueda no devolvió resultados.", "web")
        for r in resultados:
            self.urls_permitidas.add(r.url)
        texto = "\n".join(f"[{i}] {r.titulo}\n{r.url}\n{r.fragmento}" for i, r in enumerate(resultados, 1))
        self.memoria.aprender(consulta, " ".join(r.fragmento for r in resultados)[:2000], resultados[0].url)
        return Resultado(texto, "web", uso_internet=[r.url for r in resultados])

    def h_leer_web(self, url: str) -> Resultado:
        if not self.internet.activo:
            return Resultado("Internet está desactivado.", "web")
        try:
            from .internet import revisar_url
            revisar_url(url, resolver=False)
        except URLNoPermitida as exc:
            return Resultado(f"No abro esa dirección: {exc}", "web")
        if url not in self.urls_permitidas:
            return self._proponer(f"Abrir la página {url}", lambda: self._leer(url))
        return self._leer(url)

    def _leer(self, url: str) -> Resultado:
        try:
            pagina = self.internet.leer(url)
        except (URLNoPermitida, OSError, ConnectionError, ValueError) as exc:
            return Resultado(f"No pude leer la página: {exc}", "web")
        self.memoria.aprender(pagina["titulo"] or url, pagina["texto"][:4000], pagina["url"])
        return Resultado(f"{pagina['titulo']}\n{pagina['url']}\n\n{pagina['texto']}", "web", uso_internet=[pagina["url"]])

    # --- memoria

    def h_buscar_memoria(self, consulta: str) -> Resultado:
        partes = []
        for r in self.memoria.buscar_recuerdos(consulta, 6):
            partes.append(f"[recuerdo:{r.tipo}] {r.texto}")
        for k in self.memoria.buscar_conocimiento(consulta, 3):
            partes.append(f"[aprendido de {k['url']}] {k['tema']}: {k['texto'][:600]}")
        for d in self.memoria.buscar_documentos(consulta, 5):
            partes.append(f"[archivo {d['ruta']}] {d['fragmento']}")
        return Resultado("\n".join(partes) or "No encontré nada relacionado en mi memoria.", "memoria")

    # --- archivos

    def h_leer_archivo(self, ruta: str) -> Resultado:
        chequeo = politica.revisar_ruta(ruta, excluidas=self.config.unidades_excluidas)
        if not chequeo.ok:
            return Resultado(f"No puedo leer esa ruta: {chequeo.motivo}", "archivo")
        if not self.config.agente_auto_lectura:
            return self._proponer(f"Leer el archivo {ruta}", lambda: self._leer_archivo(chequeo.ruta))
        return self._leer_archivo(chequeo.ruta)

    def _leer_archivo(self, ruta: Path) -> Resultado:
        if not ruta.is_file():
            return Resultado("No existe ese archivo.", "archivo")
        if ruta.stat().st_size > self.config.max_file_bytes:
            return Resultado(f"El archivo pesa más de {self.config.max_file_bytes} bytes; léelo por partes.", "archivo")
        with open(ruta, "rb") as f:
            datos = f.read(self.config.max_file_bytes)
        if b"\x00" in datos[:4096]:
            return Resultado("Es un archivo binario; no lo leo como texto.", "archivo")
        self.leyo_privado = True
        return Resultado(datos.decode("utf-8", errors="replace"), f"archivo {ruta}")

    def h_listar(self, ruta: str) -> Resultado:
        chequeo = politica.revisar_ruta(ruta, excluidas=self.config.unidades_excluidas)
        if not chequeo.ok:
            return Resultado(f"No puedo listar esa ruta: {chequeo.motivo}", "archivo")
        p = chequeo.ruta
        if not p.is_dir():
            return Resultado("No es una carpeta.", "archivo")
        filas = []
        for i, e in enumerate(sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))):
            if i >= 200:
                filas.append("…")
                break
            if e.name.startswith("."):
                continue
            filas.append(f"{e.name}{os.sep if e.is_dir() else ''}")
        return Resultado("\n".join(filas) or "(vacía)", f"carpeta {p}")

    def h_escribir_archivo(self, ruta: str, contenido: str) -> Resultado:
        chequeo = politica.revisar_ruta(ruta, escribir=True, excluidas=self.config.unidades_excluidas)
        if not chequeo.ok:
            return Resultado(f"No puedo escribir ahí: {chequeo.motivo}", "archivo")
        vista = contenido[:400] + ("…" if len(contenido) > 400 else "")
        return self._proponer(f"Escribir {len(contenido)} caracteres en {chequeo.ruta}\n\n{vista}",
                              lambda: self._escribir(ruta, contenido), solo_clic=chequeo.confirmar)

    def _escribir(self, ruta: str, contenido: str) -> dict:
        chequeo = politica.revisar_ruta(ruta, escribir=True, excluidas=self.config.unidades_excluidas)  # se revalida
        if not chequeo.ok:
            return {"ok": False, "salida": f"La ruta cambió y ya no es segura: {chequeo.motivo}"}
        destino = chequeo.ruta
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists():
            shutil.copy2(destino, destino.with_name(destino.name + ".nova-bak"))
        banderas = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(destino, banderas, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(contenido.encode("utf-8"))
        return {"ok": True, "salida": f"Guardado en {destino}"}

    # --- órdenes

    def h_orden(self, orden: str, carpeta: str | None = None) -> Resultado:
        decision = politica.clasificar(orden)
        if decision.nivel == politica.PROHIBIDO:
            return Resultado(f"PROHIBIDO, no se ejecuta: {decision.motivo}", "orden")
        timeout = self.config.command_timeout
        if decision.nivel == politica.SOLO_LECTURA and decision.autoejecutable:
            r = politica.ejecutar(decision, carpeta, timeout)
            return Resultado(r["salida"] or "(sin salida)", f"orden {orden}")
        return self._proponer(f"Ejecutar: {orden}" + (f"\nEn: {carpeta}" if carpeta else ""),
                              lambda: politica.ejecutar(decision, carpeta, timeout))

    # --- organización y utilidades

    def h_nota(self, texto: str) -> Resultado:
        return Resultado(f"Nota guardada (#{self.memoria.nota(texto)}).")

    def h_tarea(self, texto: str, fecha: str | None = None) -> Resultado:
        if fecha and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
            fecha = None
        return Resultado(f"Tarea guardada (#{self.memoria.tarea(texto, fecha)}).")

    def h_calcular(self, expresion: str) -> Resultado:
        return Resultado(str(calcular(expresion)))

    def h_unidades(self) -> Resultado:
        filas = []
        for u in unidades():
            extra = f"{u.get('libre_gb', '?')} GB libres de {u.get('total_gb', '?')} GB" if "total_gb" in u else u.get("estado", "")
            filas.append(f"{u['unidad']} {u.get('nombre', '')} ({u.get('tipo', '')}): {extra}")
        return Resultado("\n".join(filas), "unidades")

    def _proponer(self, descripcion: str, accion: Callable[[], Any], solo_clic: bool = False) -> Resultado:
        p = self.confirmaciones.pedir(descripcion, accion, solo_clic)
        return Resultado(
            "Necesita la confirmación del usuario; quedó como propuesta. No supongas que ya se hizo.",
            confirmacion={"codigo": p.codigo, "descripcion": descripcion, "solo_clic": solo_clic},
        )
