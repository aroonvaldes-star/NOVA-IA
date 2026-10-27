"""Servidor local entre el núcleo y el orbe.

Solo escucha en 127.0.0.1. Protecciones:
  * Host debe ser 127.0.0.1:<puerto> o localhost:<puerto> (evita DNS rebinding).
  * Las peticiones que cambian algo exigen el token de la sesión en la cabecera
    X-Nova-Token y un Origin propio o ausente.
  * El token se inyecta en la página del orbe, nunca en una URL.
  * CSP estricta: la interfaz solo carga archivos locales.
"""

from __future__ import annotations

import json
import mimetypes
import queue
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .celular import es_red_local as celular_ok
from .nucleo import Nova

UI = Path(__file__).resolve().parent.parent / "ui"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
MAX_CUERPO = 25 * 1024 * 1024


# Lo único que el celular puede pedir (además de la página y el emparejamiento).
RUTAS_CELULAR = {"/api/eventos", "/api/estado", "/api/conversacion", "/api/tareas", "/api/notas", "/api/mensaje", "/api/confirmar",
                 "/api/cancelar", "/api/voz/stt", "/api/voz/tts"}


def crear_servidor(nova: Nova, puerto: int | None = None, carpeta_ui: Path = UI, celular: bool = False) -> ThreadingHTTPServer:
    """`celular=False`: solo 127.0.0.1, para el orbe del escritorio.
    `celular=True`: en la red local, solo para dispositivos emparejados."""
    puerto = int(puerto if puerto is not None else (nova.config.celular_puerto if celular else nova.config.web_port))
    token = "celular" if celular else secrets.token_urlsafe(24)

    class Manejador(BaseHTTPRequestHandler):
        server_version = "NOVA"
        sys_version = ""

        def log_message(self, *args):  # silencioso
            pass

        # ------------------------------------------------------- seguridad

        def _host_ok(self) -> bool:
            if celular:
                return celular_ok(self.client_address[0])
            host = self.headers.get("Host", "")
            p = self.server.server_address[1]
            return host in {f"127.0.0.1:{p}", f"localhost:{p}"}

        def _origen_ok(self) -> bool:
            origen = self.headers.get("Origin")
            if celular:
                return origen is None or urlsplit(origen).netloc == self.headers.get("Host", "")
            if origen is None or origen in {"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"}:
                return True
            p = self.server.server_address[1]
            return origen in {f"http://127.0.0.1:{p}", f"http://localhost:{p}"}

        def _autorizado(self) -> bool:
            if celular:
                return self._origen_ok() and nova.emparejador.valido(self.headers.get("X-Nova-Dispositivo"))
            return self._origen_ok() and secrets.compare_digest(self.headers.get("X-Nova-Token", ""), token)

        def _enviar(self, codigo: int, cuerpo: bytes, tipo: str = "application/json; charset=utf-8") -> None:
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(cuerpo)

        def _json(self, datos, codigo: int = 200) -> None:
            self._enviar(codigo, json.dumps(datos, ensure_ascii=False).encode("utf-8"))

        def _leer(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_CUERPO:
                raise ValueError("cuerpo demasiado grande")
            return self.rfile.read(n) if n else b""

        def _leer_json(self) -> dict:
            try:
                datos = json.loads(self._leer() or b"{}")
            except ValueError:
                return {}
            return datos if isinstance(datos, dict) else {}

        # ---------------------------------------------------------- GET

        def do_GET(self):
            if not self._host_ok():
                return self._enviar(403, b"Host no permitido", "text/plain")
            ruta = urlsplit(self.path).path
            if ruta in {"/", "/index.html"}:
                html = (carpeta_ui / "index.html").read_text(encoding="utf-8")
                html = html.replace("{{NOVA_TOKEN}}", token).replace("{{NOVA_MODO}}", "celular" if celular else "")
                return self._enviar(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if ruta == "/api/eventos":
                return self._eventos()
            if ruta.startswith("/api/"):
                if celular and ruta not in RUTAS_CELULAR:
                    return self._json({"error": "no disponible desde el celular"}, 403)
                if not (self._autorizado() if celular else secrets.compare_digest(self.headers.get("X-Nova-Token", ""), token)):
                    return self._json({"error": "sin autorización"}, 401)
                if ruta == "/api/estado":
                    return self._json(nova.estado())
                if ruta == "/api/conversacion":
                    return self._json(nova.conversacion())
                if ruta == "/api/yo":
                    return self._json([r.__dict__ for r in nova.memoria.recuerdos(limite=500)])
                if ruta == "/api/tareas":
                    return self._json(nova.memoria.tareas())
                if ruta == "/api/notas":
                    return self._json(nova.memoria.notas())
                return self._json({"error": "no existe"}, 404)
            return self._estatico(ruta)

        def _estatico(self, ruta: str) -> None:
            destino = (carpeta_ui / ruta.lstrip("/")).resolve()
            try:
                destino.relative_to(carpeta_ui.resolve())
            except ValueError:
                return self._enviar(404, b"", "text/plain")
            if not destino.is_file():
                return self._enviar(404, b"", "text/plain")
            tipo = mimetypes.guess_type(destino.name)[0] or "application/octet-stream"
            if destino.suffix in {".js", ".mjs"}:
                tipo = "text/javascript; charset=utf-8"
            return self._enviar(200, destino.read_bytes(), tipo)

        def _eventos(self) -> None:
            # La interfaz lee este flujo con fetch para poder mandar el token en la cabecera.
            if not self._autorizado():
                return self._enviar(401, b"", "text/plain")
            q = nova.bus.suscribir()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                while True:
                    try:
                        evento = q.get(timeout=15)
                        linea = "data: " + json.dumps(evento, ensure_ascii=False) + "\n\n"
                    except queue.Empty:
                        linea = ": latido\n\n"
                    self.wfile.write(linea.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                nova.bus.desuscribir(q)

        # --------------------------------------------------------- POST

        def do_POST(self):
            if not self._host_ok():
                return self._enviar(403, b"Host no permitido", "text/plain")
            ruta = urlsplit(self.path).path
            if celular and ruta == "/api/emparejar":
                datos = self._leer_json()
                llave = nova.emparejador.emparejar(str(datos.get("codigo", "")), str(datos.get("nombre", "celular")))
                if not llave:
                    return self._json({"error": "Código incorrecto o caducado. Pide uno nuevo en la PC."}, 403)
                nova.bus.publicar({"tipo": "platica", "texto": "Tu celular quedó conectado conmigo."})
                return self._json({"dispositivo": llave})
            if celular and ruta not in RUTAS_CELULAR:
                return self._json({"error": "no disponible desde el celular"}, 403)
            if not self._autorizado():
                return self._json({"error": "sin autorización"}, 401)
            try:
                if ruta == "/api/mensaje":
                    datos = self._leer_json()
                    texto = str(datos.get("texto", ""))[:8000]
                    threading.Thread(target=nova.procesar, args=(texto, bool(datos.get("voz")) or celular), daemon=True).start()
                    return self._json({"ok": True})
                if ruta == "/api/confirmar":
                    # Desde el celular cuenta como confirmación remota: lo que exige clic en la PC se rechaza.
                    return self._json(nova.confirmar(str(self._leer_json().get("codigo", "")), por_voz=celular))
                if ruta == "/api/cancelar":
                    return self._json({"ok": nova.confirmaciones.cancelar(str(self._leer_json().get("codigo", "")))})
                if ruta == "/api/olvidar":
                    datos = self._leer_json()
                    if datos.get("todo") is True:
                        nova.memoria.olvidar_todo()
                        return self._json({"ok": True})
                    return self._json({"borrados": nova.memoria.olvidar(datos.get("id") or str(datos.get("tema", "")))})
                if ruta == "/api/voz/stt":
                    wav = self._leer()
                    nova._estado("procesando")
                    return self._json({"texto": nova.voz.transcribir(wav)})
                if ruta == "/api/voz/tts":
                    audio = nova.voz.sintetizar(str(self._leer_json().get("texto", ""))[:3000])
                    return self._enviar(200, audio, "audio/wav")
                if ruta == "/api/estado-orbe":
                    datos = self._leer_json()
                    nova._estado(str(datos.get("estado", "reposo")), datos.get("contexto") if datos.get("contexto") in nova.estado()["colores"] else None)
                    return self._json({"ok": True})
            except (RuntimeError, ValueError) as exc:
                return self._json({"error": str(exc)}, 400)
            return self._json({"error": "no existe"}, 404)

    servidor = ThreadingHTTPServer(("0.0.0.0" if celular else "127.0.0.1", puerto), Manejador)
    servidor.daemon_threads = True
    if celular:
        from .celular import contexto_tls
        tls = contexto_tls(nova.config.rutas.datos)
        if tls:
            servidor.socket = tls.wrap_socket(servidor.socket, server_side=True)
        servidor.https = bool(tls)  # type: ignore[attr-defined]
    servidor.token = token  # type: ignore[attr-defined]
    return servidor
