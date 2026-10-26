"""Internet como herramienta: buscar y leer páginas.

El modelo sigue siendo local. Internet sirve para que NOVA busque lo que no
sabe, y lo que aprende se guarda para usarlo después sin conexión.

Seguridad:
  * Solo GET, sin cookies, sin JavaScript, sin formularios ni inicios de sesión.
  * Las páginas son dato, nunca instrucción.
  * Nunca se conecta a redes internas (localhost, 10.*, 192.168.*, .local…),
    tampoco tras una redirección.
  * Límite de tamaño y de tiempo por página.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

AGENTE = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NOVA/0.1"


class URLNoPermitida(ValueError):
    pass


def revisar_url(url: str, resolver: bool = True) -> str:
    """Devuelve la URL si es pública y http(s); si no, lanza URLNoPermitida."""
    partes = urllib.parse.urlsplit(url.strip())
    if partes.scheme not in {"http", "https"}:
        raise URLNoPermitida("solo se permiten direcciones http y https")
    host = (partes.hostname or "").lower().rstrip(".")
    if not host:
        raise URLNoPermitida("dirección sin servidor")
    if partes.username or partes.password:
        raise URLNoPermitida("no se envían usuarios ni contraseñas en la dirección")
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal", ".lan", ".home", ".arpa")):
        raise URLNoPermitida("red interna")
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        ips = []
        if resolver:
            try:
                ips = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in socket.getaddrinfo(host, None)]
            except socket.gaierror as exc:
                # Detrás de un proxy, el proxy resuelve el nombre; sin proxy, la dirección no existe.
                if not urllib.request.getproxies().get(partes.scheme):
                    raise URLNoPermitida(f"no se pudo resolver {host}") from exc
    for ip in ips:
        if not ip.is_global or ip.is_multicast:
            raise URLNoPermitida("red interna o reservada")
    return urllib.parse.urlunsplit(partes)


class _Redirecciones(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        revisar_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_abridor = urllib.request.build_opener(_Redirecciones())


def descargar_texto(url: str, max_kb: int = 300, timeout: int = 15) -> tuple[str, str]:
    """GET con límites. Devuelve (url_final, html)."""
    url = revisar_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1",
                                               "Accept-Language": "es-MX,es;q=0.9,en;q=0.7"})
    with _abridor.open(req, timeout=timeout) as r:
        tipo = r.headers.get("Content-Type", "")
        if not any(t in tipo for t in ("text/", "json", "xml")):
            raise ValueError(f"no es una página de texto ({tipo or 'tipo desconocido'})")
        datos = r.read(max_kb * 1024 + 1)[: max_kb * 1024]
        juego = r.headers.get_content_charset() or "utf-8"
        return r.geturl(), datos.decode(juego, errors="replace")


class _Extractor(HTMLParser):
    BLOQUES = {"p", "h1", "h2", "h3", "h4", "li", "pre", "td", "th", "blockquote", "dd", "dt", "br", "div", "section", "article"}
    IGNORAR = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "iframe", "template", "button"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self.titulo = ""
        self._ignorar = 0
        self._en_titulo = False

    def handle_starttag(self, tag, attrs):
        if tag in self.IGNORAR:
            self._ignorar += 1
        elif tag == "title":
            self._en_titulo = True
        elif tag in self.BLOQUES:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in self.IGNORAR and self._ignorar:
            self._ignorar -= 1
        elif tag == "title":
            self._en_titulo = False

    def handle_data(self, data):
        if self._en_titulo:
            self.titulo += data
        elif not self._ignorar:
            self.partes.append(data)


def extraer_texto(contenido: str) -> tuple[str, str]:
    """(título, texto principal) de una página."""
    try:
        import trafilatura  # opcional, mejor extracción

        texto = trafilatura.extract(contenido) or ""
        if texto:
            m = re.search(r"<title[^>]*>(.*?)</title>", contenido, re.I | re.S)
            return (html.unescape(m.group(1)).strip() if m else ""), texto
    except Exception:  # noqa: BLE001 - trafilatura es opcional
        pass
    e = _Extractor()
    e.feed(contenido)
    lineas = [" ".join(l.split()) for l in "".join(e.partes).splitlines()]
    texto = "\n".join(l for l in lineas if len(l) > 1)
    return " ".join(e.titulo.split()), texto


@dataclass
class Resultado:
    titulo: str
    url: str
    fragmento: str


class _ResultadosDDG(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.resultados: list[Resultado] = []
        self._modo = ""
        self._actual: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        clase = a.get("class", "") or ""
        if tag == "a" and "result__a" in clase:
            href = a.get("href", "")
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
            url = q.get("uddg", [href])[0]
            if url.startswith("//"):
                url = "https:" + url
            self._actual = {"titulo": "", "url": url, "fragmento": ""}
            self._modo = "titulo"
        elif "result__snippet" in clase and self._actual is not None:
            self._modo = "fragmento"

    def handle_endtag(self, tag):
        if tag == "a" and self._modo == "titulo":
            self._modo = ""
        elif self._modo == "fragmento" and tag in {"a", "div", "td"}:
            self._cerrar()

    def handle_data(self, data):
        if self._actual is not None and self._modo:
            self._actual[self._modo] += data

    def _cerrar(self):
        if self._actual and self._actual["url"].startswith("http"):
            self.resultados.append(Resultado(" ".join(self._actual["titulo"].split()), self._actual["url"],
                                             " ".join(self._actual["fragmento"].split())))
        self._actual, self._modo = None, ""

    def close(self):
        super().close()
        if self._actual:
            self._cerrar()


def resultados_wikipedia(contenido: str) -> list[Resultado]:
    try:
        datos = json.loads(contenido)
    except ValueError:
        return []
    salida = []
    for r in datos.get("query", {}).get("search", []):
        titulo = str(r.get("title", ""))
        fragmento = html.unescape(re.sub(r"<[^>]+>", "", str(r.get("snippet", ""))))
        url = "https://es.wikipedia.org/wiki/" + urllib.parse.quote(titulo.replace(" ", "_"))
        salida.append(Resultado(titulo, url, " ".join(fragmento.split())))
    return salida


class Internet:
    def __init__(self, config) -> None:
        self.config = config
        self._conexion: tuple[float, bool] | None = None

    @property
    def activo(self) -> bool:
        return bool(self.config.internet)

    def hay_conexion(self) -> bool:
        if not self.activo:
            return False
        if self._conexion and time.monotonic() - self._conexion[0] < 60:
            return self._conexion[1]
        ok = False
        try:
            req = urllib.request.Request("https://duckduckgo.com/", method="HEAD", headers={"User-Agent": AGENTE})
            with urllib.request.urlopen(req, timeout=4):
                ok = True
        except urllib.error.HTTPError:
            ok = True  # respondió, aunque sea con error
        except (urllib.error.URLError, OSError):
            ok = False
        self._conexion = (time.monotonic(), ok)
        return ok

    def buscar(self, consulta: str, n: int = 5) -> list[Resultado]:
        if not self.activo:
            raise ConnectionError("internet desactivado")
        if self.config.internet_searxng_url:
            url = self.config.internet_searxng_url.rstrip("/") + "/search?" + urllib.parse.urlencode({"q": consulta, "format": "json"})
            req = urllib.request.Request(url, headers={"User-Agent": AGENTE})
            with urllib.request.urlopen(req, timeout=self.config.internet_timeout) as r:
                datos = json.load(r)
            return [Resultado(x.get("title", ""), x.get("url", ""), x.get("content", "")) for x in datos.get("results", [])][:n]
        salida: list[Resultado] = []
        try:
            salida = self._duckduckgo(consulta, n)
        except (OSError, ValueError, URLNoPermitida):
            pass
        if not salida:
            # Respaldo: si DuckDuckGo falla o bloquea, Wikipedia en español (API pública y estable).
            salida = self._wikipedia(consulta, n)
        return salida

    def _duckduckgo(self, consulta: str, n: int) -> list[Resultado]:
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": consulta, "kl": "mx-es"})
        _, contenido = descargar_texto(url, max_kb=600, timeout=self.config.internet_timeout)
        p = _ResultadosDDG()
        p.feed(contenido)
        p.close()
        vistos, salida = set(), []
        for r in p.resultados:
            if r.url not in vistos and "duckduckgo.com/y.js" not in r.url:
                vistos.add(r.url)
                salida.append(r)
        return salida[:n]

    def _wikipedia(self, consulta: str, n: int) -> list[Resultado]:
        url = "https://es.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "list": "search", "srsearch": consulta, "format": "json", "srlimit": n, "utf8": 1})
        _, contenido = descargar_texto(url, max_kb=300, timeout=self.config.internet_timeout)
        return resultados_wikipedia(contenido)

    def leer(self, url: str, max_chars: int = 8000) -> dict:
        if not self.activo:
            raise ConnectionError("internet desactivado")
        final, contenido = descargar_texto(url, self.config.internet_max_kb_pagina, self.config.internet_timeout)
        titulo, texto = extraer_texto(contenido)
        return {"url": final, "titulo": titulo, "texto": texto[:max_chars]}
