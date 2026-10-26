"""Línea de órdenes de NOVA (`nova …` o `python -m nova …`)."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import threading
import webbrowser
from pathlib import Path

from . import __version__, politica
from .config import cargar


def proceso_elevado() -> bool:
    if sys.platform == "win32":
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (OSError, AttributeError):
            return False
    import os

    return hasattr(os, "geteuid") and os.geteuid() == 0 and not os.environ.get("NOVA_PERMITIR_ROOT")


def _nova():
    from .nucleo import Nova

    return Nova(cargar())


def cmd_servidor(a) -> int:
    from .servidor import crear_servidor

    nova = _nova()
    servidor = crear_servidor(nova, a.puerto)
    puerto = servidor.server_address[1]
    nova.config.rutas.estado.mkdir(parents=True, exist_ok=True)
    (nova.config.rutas.estado / "puerto").write_text(str(puerto), encoding="utf-8")
    nova.arrancar_fondo()
    celular: dict = {}

    def iniciar_celular() -> str | None:
        if "url" not in celular:
            from .celular import ip_local

            try:
                srv = crear_servidor(nova, celular=True)
            except OSError:
                return None
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            esquema = "https" if getattr(srv, "https", False) else "http"
            celular["url"] = f"{esquema}://{ip_local()}:{srv.server_address[1]}/"
        return celular["url"]

    nova.iniciar_celular = iniciar_celular
    if nova.config.celular:
        print(f"Celular: {iniciar_celular()}")
    url = f"http://127.0.0.1:{puerto}/"
    print(f"NOVA {__version__} escuchando en {url} (motor: {nova.motor.nombre}, modelo: {nova.motor.modelo or 'ninguno'})")
    if a.abrir:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        nova.cerrar()
    return 0


def _imprimir_eventos(nova) -> None:
    q = nova.bus.suscribir()

    def bucle():
        while True:
            e = q.get()
            if e["tipo"] == "herramienta":
                print(f"\n  · {e['nombre']} {e['args']}", flush=True)
            elif e["tipo"] == "confirmacion":
                print(f"\n  ⚠ Necesito tu confirmación: {e['descripcion']}\n    Escribe: confirmar {e['codigo']}", flush=True)
            elif e["tipo"] == "aprendi":
                print(f"  (aprendí: {'; '.join(e['recuerdos'])})", flush=True)
            elif e["tipo"] == "reinicio":
                print("\n  · no lo sé de memoria, voy a buscarlo…", flush=True)
    threading.Thread(target=bucle, daemon=True).start()


def cmd_chat(a) -> int:
    nova = _nova()
    _imprimir_eventos(nova)
    print(f"NOVA {__version__}. Escribe «salir» para terminar.\n")
    try:
        while True:
            try:
                texto = input("tú › ").strip()
            except EOFError:
                break
            if texto.lower() in {"salir", "exit", "quit"}:
                break
            if texto:
                respuesta = nova.procesar(texto)
                print("nova › " + respuesta + "\n")
    finally:
        nova.cerrar()
    return 0


def cmd_preguntar(a) -> int:
    nova = _nova()
    print(nova.procesar(" ".join(a.texto)))
    nova.cerrar()
    return 0


def cmd_yo(a) -> int:
    nova = _nova()
    if a.accion == "olvidar":
        print(f"Olvidé {nova.memoria.olvidar(' '.join(a.tema))} recuerdo(s).")
    else:
        print(nova.que_se_de_ti())
    return 0


def cmd_memoria(a) -> int:
    nova = _nova()
    if a.accion == "indexar":
        r = nova.memoria.indexar(a.rutas, revisar=politica.revisar_ruta, max_bytes=nova.config.max_file_bytes)
        print(f"Indexé {r['indexados']} archivo(s); omití {r['omitidos']}.")
    elif a.accion == "buscar":
        for d in nova.memoria.buscar_documentos(" ".join(a.rutas)):
            print(f"{d['ruta']}\n   {d['fragmento']}\n")
    else:
        for k, v in nova.memoria.estado().items():
            print(f"{k}: {v}")
    return 0


def cmd_web(a) -> int:
    from .internet import Internet

    net = Internet(cargar())
    if a.accion == "buscar":
        for i, r in enumerate(net.buscar(" ".join(a.texto)), 1):
            print(f"[{i}] {r.titulo}\n    {r.url}\n    {r.fragmento}\n")
    else:
        p = net.leer(a.texto[0])
        print(f"{p['titulo']}\n{p['url']}\n\n{p['texto'][:4000]}")
    return 0


def cmd_orden(a) -> int:
    orden = " ".join(a.orden)
    d = politica.clasificar(orden)
    print(f"{d.nivel}: {d.motivo}")
    if d.nivel == politica.PROHIBIDO:
        return 2
    if d.nivel == politica.CONFIRMAR:
        if input("¿Ejecutar? escribe «si»: ").strip().lower() not in {"si", "sí"}:
            return 1
    r = politica.ejecutar(d, a.carpeta, cargar().command_timeout)
    print(r["salida"])
    return 0 if r["ok"] else 1


def cmd_modelo(a) -> int:
    from .motor import buscar_modelo, es_gguf

    config = cargar()
    config.rutas.crear()
    if a.accion == "listar":
        modelos = sorted(config.rutas.modelos.glob("*.gguf"))
        for m in modelos:
            print(f"{m.name}  {m.stat().st_size / 1e9:.2f} GB")
        print(f"En uso: {(buscar_modelo(config) or Path('ninguno')).name}" if modelos else "No hay modelos instalados.")
        return 0
    origen = Path(a.archivo)
    if not es_gguf(origen):
        print("Ese archivo no es un modelo GGUF.")
        return 2
    if a.sha256:
        h = hashlib.sha256()
        with open(origen, "rb") as f:
            for bloque in iter(lambda: f.read(1 << 20), b""):
                h.update(bloque)
        if h.hexdigest().lower() != a.sha256.lower():
            print("El SHA256 no coincide; no lo instalo.")
            return 2
    destino = config.rutas.modelos / origen.name
    shutil.copy2(origen, destino)
    print(f"Modelo instalado en {destino}")
    return 0


def cmd_estado(a) -> int:
    nova = _nova()
    print(nova.presentarme())
    for k, v in nova.estado()["voz"].items():
        print(f"voz {k}: {'lista' if v else 'falta instalar'}")
    return 0


def cmd_nota(a) -> int:
    nova = _nova()
    print(nova.herramientas.usar("nota", {"texto": " ".join(a.texto)}).texto)
    return 0


def cmd_tarea(a) -> int:
    nova = _nova()
    print(nova.herramientas.usar("tarea", {"texto": " ".join(a.texto), "fecha": a.fecha}).texto)
    return 0


def cmd_tareas(a) -> int:
    nova = _nova()
    for t in nova.memoria.tareas():
        print(f"#{t['id']} {t['fecha'] or '          '} {t['texto']}")
    return 0


def cmd_hecho(a) -> int:
    nova = _nova()
    print("Hecho." if nova.memoria.completar(a.id) else "No encontré esa tarea.")
    return 0


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "win32":
        for flujo in (sys.stdout, sys.stderr):
            try:
                flujo.reconfigure(encoding="utf-8")
            except (AttributeError, ValueError):
                pass
    p = argparse.ArgumentParser(prog="nova", description="NOVA, tu asistente local.")
    p.add_argument("--version", action="version", version=f"NOVA {__version__}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("servidor", help="arranca el núcleo y la interfaz del orbe")
    s.add_argument("--puerto", type=int)
    s.add_argument("--abrir", action="store_true", help="abre el orbe en el navegador")
    s.set_defaults(f=cmd_servidor)
    sub.add_parser("chat", help="conversación en la terminal").set_defaults(f=cmd_chat)
    s = sub.add_parser("preguntar")
    s.add_argument("texto", nargs="+")
    s.set_defaults(f=cmd_preguntar)
    s = sub.add_parser("yo", help="lo que NOVA sabe de ti")
    s.add_argument("accion", nargs="?", choices=["ver", "olvidar"], default="ver")
    s.add_argument("tema", nargs="*")
    s.set_defaults(f=cmd_yo)
    s = sub.add_parser("memoria")
    s.add_argument("accion", choices=["indexar", "buscar", "estado"])
    s.add_argument("rutas", nargs="*")
    s.set_defaults(f=cmd_memoria)
    s = sub.add_parser("web")
    s.add_argument("accion", choices=["buscar", "leer"])
    s.add_argument("texto", nargs="+")
    s.set_defaults(f=cmd_web)
    s = sub.add_parser("orden")
    s.add_argument("orden", nargs="+")
    s.add_argument("--carpeta")
    s.set_defaults(f=cmd_orden)
    s = sub.add_parser("modelo")
    s.add_argument("accion", choices=["listar", "instalar"])
    s.add_argument("archivo", nargs="?")
    s.add_argument("--sha256")
    s.set_defaults(f=cmd_modelo)
    sub.add_parser("estado").set_defaults(f=cmd_estado)
    s = sub.add_parser("nota")
    s.add_argument("texto", nargs="+")
    s.set_defaults(f=cmd_nota)
    s = sub.add_parser("tarea")
    s.add_argument("texto", nargs="+")
    s.add_argument("--fecha")
    s.set_defaults(f=cmd_tarea)
    sub.add_parser("tareas").set_defaults(f=cmd_tareas)
    s = sub.add_parser("hecho")
    s.add_argument("id", type=int)
    s.set_defaults(f=cmd_hecho)

    a = p.parse_args(argv)
    if proceso_elevado():
        print("NOVA no se ejecuta como administrador. Ábrela con tu usuario normal.", file=sys.stderr)
        return 3
    if not getattr(a, "f", None):
        a = p.parse_args(["servidor", "--abrir"])
    if a.cmd == "modelo" and a.accion == "instalar" and not a.archivo:
        p.error("falta el archivo .gguf")
    return a.f(a)
