"""El cerebro de NOVA: une motor, memoria, personalidad, herramientas, voz e internet.

Cada mensaje sigue este camino:
  1. Órdenes rápidas (sin modelo, al instante).
  2. Aprende de lo que dijiste (reglas).
  3. Arma el contexto: personalidad, su propio estado, tu perfil y recuerdos.
  4. Piensa en pasos (ACCION/FINAL) usando herramientas.
  5. Si no sabe algo y hay internet, busca en seguida y vuelve a responder.
  6. Guarda la conversación y, de vez en cuando, extrae recuerdos nuevos.

Todo lo que pasa se publica como eventos para que el orbe lo muestre.
"""

from __future__ import annotations

import datetime as dt
import json
import queue
import re
import sys
import threading
import time
import uuid
from typing import Any, Callable

from . import __version__, aprendizaje, platica, personalidad
from .config import Config
from .celular import Emparejador
from .confirmaciones import Confirmaciones
from .herramientas import CajaDeHerramientas, envolver
from .internet import Internet
from .memoria import Memoria
from .motor import Motor, MotorSimulado, elegir
from .voz import Voz, orden_rapida, para_hablar

NO_SABE = re.compile(r"\b(no (lo )?s[eé]\b|no tengo (esa )?informaci[oó]n|no estoy segur[ao]|no conozco|"
                     r"no tengo datos|no puedo saber|mi conocimiento (llega|tiene)|no dispongo)", re.I)
ACCION = re.compile(r"^\s*ACCI[OÓ]N\s*:\s*(\{.*\})", re.S | re.I)
FINAL = re.compile(r"^\s*FINAL\s*:\s*", re.I)


class Bus:
    """Publicación de eventos para la interfaz (cada cliente tiene su cola)."""

    def __init__(self) -> None:
        self._colas: list[queue.Queue] = []
        self._cerrojo = threading.Lock()
        self.ultimo_estado: dict = {"tipo": "estado", "estado": "reposo", "contexto": "general", "color": personalidad.COLORES["general"]}

    def suscribir(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._cerrojo:
            self._colas.append(q)
        q.put(self.ultimo_estado)
        return q

    def desuscribir(self, q: queue.Queue) -> None:
        with self._cerrojo:
            if q in self._colas:
                self._colas.remove(q)

    def publicar(self, evento: dict) -> None:
        if evento.get("tipo") == "estado":
            self.ultimo_estado = evento
        with self._cerrojo:
            for q in list(self._colas):
                try:
                    q.put_nowait(evento)
                except queue.Full:
                    pass


class Nova:
    def __init__(self, config: Config, motor: Motor | None = None, memoria: Memoria | None = None,
                 internet: Internet | None = None):
        self.config = config
        config.rutas.crear()
        self.motor = motor or elegir(config)
        self.memoria = memoria or Memoria(config.rutas.datos / "nova.db")
        self.internet = internet or Internet(config)
        self.confirmaciones = Confirmaciones()
        self.herramientas = CajaDeHerramientas(config, self.memoria, self.internet, self.confirmaciones)
        self.voz = Voz(config)
        self.emparejador = Emparejador(self.memoria)
        self.iniciar_celular: Callable[[], str | None] | None = None  # lo pone el servidor del escritorio
        self.bus = Bus()
        self.personalidad = personalidad.cargar_personalidad(config.rutas.config)
        self.sesion = uuid.uuid4().hex[:12]
        self.historial: list[dict] = []
        self.mensajes_sin_extraer: list[str] = []
        self.platica = platica.EstadoPlatica()
        self.ultima_actividad = time.monotonic()
        self.inicio_sesion_trabajo = time.monotonic()
        self._cerrojo = threading.Lock()
        self._detener = threading.Event()
        if not self.memoria.recuerdos("nova", 1):
            self.memoria.recordar("nova", f"Nací el {dt.date.today().isoformat()} como NOVA {__version__}.", "nova")

    # --------------------------------------------------------------- eventos

    def _estado(self, estado: str, ctx: str | None = None) -> None:
        ctx = ctx or self.bus.ultimo_estado.get("contexto", "general")
        self.bus.publicar({"tipo": "estado", "estado": estado, "contexto": ctx, "color": personalidad.COLORES[ctx]})

    def estado(self) -> dict:
        return {
            "nombre": "NOVA", "version": __version__, "motor": self.motor.nombre, "modelo": self.motor.modelo,
            "internet": self.internet.activo, "privado": self.memoria.privado, "voz": self.voz.capacidades(),
            "memoria": self.memoria.estado(), "confirmaciones": self.confirmaciones.lista(),
            "estado": self.bus.ultimo_estado, "colores": personalidad.COLORES,
        }

    # ------------------------------------------------------------- mensajes

    def procesar(self, texto: str, por_voz: bool = False) -> str:
        """Atiende un mensaje del usuario y devuelve la respuesta final (también la publica)."""
        texto = texto.strip()
        if not texto:
            return ""
        with self._cerrojo:
            self.ultima_actividad = time.monotonic()
            if self.platica.pendiente:
                platica.respuesta(self.platica, siguio=True)
            ctx = personalidad.contexto(texto)
            self._estado("activa", ctx)
            try:
                rapida = orden_rapida(texto)
                respuesta = self._orden_rapida(rapida, texto, por_voz) if rapida else None
                if respuesta is None:
                    respuesta = self._pensar(texto, ctx)
            except Exception as exc:  # noqa: BLE001 - NOVA explica el error en vez de caerse
                respuesta = f"Algo falló de mi lado: {exc}"
                self.bus.publicar({"tipo": "error", "texto": str(exc)})
            self._cerrar_turno(texto, respuesta, por_voz)
            return respuesta

    def _cerrar_turno(self, texto: str, respuesta: str, por_voz: bool) -> None:
        self.historial += [{"role": "user", "content": texto}, {"role": "assistant", "content": respuesta}]
        self.historial = self.historial[-16:]
        self.memoria.anotar_mensaje(self.sesion, "usuario", texto)
        self.memoria.anotar_mensaje(self.sesion, "nova", respuesta)
        hablar = por_voz and self.config.voz_responder_hablando and self.voz.capacidades()["tts"]
        self.bus.publicar({"tipo": "final", "texto": respuesta, "hablar": bool(hablar), "voz": para_hablar(respuesta) if hablar else ""})
        self._estado("hablando" if hablar else "reposo")
        if not self.memoria.privado:
            self.mensajes_sin_extraer.append(texto)
            if len(self.mensajes_sin_extraer) >= 10 and not isinstance(self.motor, MotorSimulado):
                pendientes, self.mensajes_sin_extraer = self.mensajes_sin_extraer, []
                threading.Thread(target=aprendizaje.extraer_con_modelo, args=(self.motor, self.memoria, pendientes), daemon=True).start()

    def _contexto_relevante(self, texto: str) -> str:
        partes = []
        for r in self.memoria.buscar_recuerdos(texto, 5):
            partes.append(f"- ({r.tipo}) {r.texto}")
        for k in self.memoria.buscar_conocimiento(texto, 3):
            partes.append(f"- (aprendido de {k['url']}, {dt.date.fromtimestamp(k['creado'])}) {k['tema']}: {k['texto'][:500]}")
        for c in self.memoria.buscar_conversaciones(texto, 3):
            partes.append(f"- (conversación {dt.date.fromtimestamp(c['fecha'])}, {c['rol']}) {c['texto'][:300]}")
        return envolver("memoria", "\n".join(partes)) if partes else ""

    def _pensar(self, texto: str, ctx: str, sembrado: list[dict] | None = None) -> str:
        if not self.memoria.privado and self.config.memoria_personal:
            nuevos = aprendizaje.aprender_por_reglas(self.memoria, texto)
            if nuevos:
                self.bus.publicar({"tipo": "aprendi", "recuerdos": nuevos})
        en_linea = self.internet.hay_conexion()
        sistema = personalidad.prompt_sistema(
            self.personalidad,
            personalidad.estado_propio(self.motor.nombre, self.motor.modelo, en_linea, self.memoria.estado(), self.voz.capacidades()),
            self.memoria.perfil(self.config.perfil_max_chars) if self.config.memoria_personal else "",
            self._contexto_relevante(texto),
        )
        mensajes = [{"role": "system", "content": sistema}, *self.historial[-10:], {"role": "user", "content": texto}, *(sembrado or [])]
        self.herramientas.nueva_tarea(texto)
        busco, reintento, ultima_accion = bool(sembrado), False, None
        for _ in range(int(self.config.agente_max_pasos)):
            self._estado("procesando", ctx)
            salida = sin_pensamientos(self.motor.chat(mensajes, al_token=self._emisor()))
            m = ACCION.match(salida)
            if m:
                try:
                    accion = json.loads(_primer_json(m.group(1)))
                    nombre, args = str(accion.get("herramienta", "")), accion.get("args") or {}
                except (ValueError, AttributeError):
                    mensajes += [{"role": "assistant", "content": salida},
                                 {"role": "user", "content": "Formato inválido. Usa ACCION: {\"herramienta\": ..., \"args\": {...}} o FINAL: ..."}]
                    continue
                firma = (nombre, json.dumps(args, sort_keys=True))
                if firma == ultima_accion:
                    mensajes += [{"role": "assistant", "content": salida},
                                 {"role": "user", "content": "Ya usaste esa herramienta con lo mismo. Responde con FINAL:."}]
                    continue
                ultima_accion = firma
                resultado = self._usar(nombre, args, ctx)
                busco = busco or nombre in {"buscar_web", "leer_web"}
                mensajes += [{"role": "assistant", "content": salida},
                             {"role": "user", "content": f"RESULTADO de {nombre}:\n{envolver(resultado.origen, resultado.texto)}"}]
                continue
            final = FINAL.sub("", salida, count=1).strip()
            if (not busco and not reintento and en_linea and NO_SABE.search(final)):
                # No sabe: busca en seguida y vuelve a responder con lo que encontró.
                reintento = busco = True
                self.bus.publicar({"tipo": "reinicio"})
                resultado = self._usar("buscar_web", {"consulta": texto}, ctx)
                mensajes += [{"role": "assistant", "content": salida},
                             {"role": "user", "content": f"RESULTADO de buscar_web:\n{envolver('web', resultado.texto)}\n"
                                                         "Con esto, responde con FINAL: y cita las fuentes."}]
                continue
            return final or "…"
        return "Me enredé en demasiados pasos. ¿Me lo dices de otra forma o lo partimos en partes?"

    def _usar(self, nombre: str, args: dict, ctx: str):
        self._estado("buscando" if nombre in {"buscar_web", "leer_web"} else "procesando",
                     "investigacion" if nombre in {"buscar_web", "leer_web"} else ctx)
        self.bus.publicar({"tipo": "herramienta", "nombre": nombre, "args": args})
        resultado = self.herramientas.usar(nombre, args)
        if resultado.uso_internet:
            self.bus.publicar({"tipo": "internet", "urls": resultado.uso_internet})
        if resultado.confirmacion:
            self.bus.publicar({"tipo": "confirmacion", **resultado.confirmacion})
        return resultado

    def _emisor(self) -> Callable[[str], None]:
        """Publica tokens solo si la salida es una respuesta final (no una ACCION)."""
        buffer: list[str] = []
        modo = {"v": "?"}

        def al_token(t: str) -> None:
            if modo["v"] == "accion":
                return
            if modo["v"] == "?" and "<think>" in "".join(buffer) + t and "</think>" not in "".join(buffer) + t:
                buffer.append(t)
                return
            if "</think>" in "".join(buffer) + t and modo["v"] == "?":
                resto = ("".join(buffer) + t).split("</think>", 1)[1]
                buffer.clear()
                if not resto:
                    return
                t = resto
            if modo["v"] == "final":
                self.bus.publicar({"tipo": "token", "texto": t})
                return
            buffer.append(t)
            junto = "".join(buffer).lstrip()
            arriba = junto.upper()
            if len(junto) < 7 and any(p.startswith(arriba) for p in ("ACCION:", "ACCIÓN:", "FINAL:")):
                return  # todavía no se sabe qué es
            if re.match(r"ACCI[OÓ]N", junto, re.I):
                modo["v"] = "accion"
                return
            modo["v"] = "final"
            resto = FINAL.sub("", junto, count=1)
            if resto:
                self.bus.publicar({"tipo": "token", "texto": resto})
        return al_token

    # --------------------------------------------------------- órdenes rápidas

    def _orden_rapida(self, orden, texto: str, por_voz: bool) -> str | None:
        a, args = orden.accion, orden.args
        if a == "parar":
            self.bus.publicar({"tipo": "parar"})
            return "Listo, me callo."
        if a in {"pantalla", "zona"}:
            return "Todavía no puedo ver la pantalla; llega en la siguiente versión."
        if a == "tarea":
            return self.herramientas.usar("tarea", args).texto
        if a == "nota":
            return self.herramientas.usar("nota", args).texto
        if a == "resumen":
            return self.resumen_del_dia()
        if a == "buscar_web":
            ctx = "investigacion"
            resultado = self._usar("buscar_web", args, ctx)
            sembrado = [{"role": "assistant", "content": 'ACCION: {"herramienta": "buscar_web", "args": ' + json.dumps(args, ensure_ascii=False) + "}"},
                        {"role": "user", "content": f"RESULTADO de buscar_web:\n{envolver('web', resultado.texto)}\nResume lo importante con FINAL: y cita las fuentes."}]
            if isinstance(self.motor, MotorSimulado) and not self.motor.guion:
                return "Esto encontré:\n" + resultado.texto
            return self._pensar(texto, ctx, sembrado)
        if a == "buscar_memoria":
            return self.herramientas.usar("buscar_memoria", args).texto
        if a == "unidades":
            return self.herramientas.usar("unidades", {}).texto
        if a == "internet":
            self.config["internet"] = args["activo"]
            return "Listo, me conecté a internet." if args["activo"] else "Listo, trabajo sin internet."
        if a == "escucha":
            self.config["voz_escucha"] = args["activo"]
            return "Dejo de escuchar. Llámame con el atajo cuando me necesites."
        if a == "yo":
            return self.que_se_de_ti()
        if a == "olvidar":
            n = self.memoria.olvidar(args["tema"])
            return f"Olvidé {n} recuerdo(s) sobre eso." if n else "No tenía nada guardado sobre eso."
        if a == "privado":
            self.memoria.privado = args["activo"]
            return ("Modo privado: no voy a recordar nada de lo que hablemos hasta que me digas."
                    if args["activo"] else "Salí del modo privado; vuelvo a recordar.")
        if a == "estado":
            return self.presentarme()
        if a == "confirmar":
            return self.confirmar(args["codigo"], por_voz=por_voz)["salida"]
        if a == "celular":
            return self.conectar_celular() if args["activo"] else f"Desconecté {self.memoria.quitar_dispositivos()} dispositivo(s)."
        return None

    def confirmar(self, codigo: str, por_voz: bool = False) -> dict:
        self._estado("procesando")
        r = self.confirmaciones.confirmar(codigo, por_voz=por_voz)
        self.bus.publicar({"tipo": "resultado", "codigo": codigo, **r})
        self._estado("reposo")
        return r

    def conectar_celular(self) -> str:
        try:
            url = self.iniciar_celular() if self.iniciar_celular else None
        except RuntimeError as exc:  # p. ej., falta el cifrado: no se abre sin él
            return str(exc)
        if not url:
            return "Para conectar tu celular necesito estar abierta en el escritorio (nova servidor)."
        codigo = self.emparejador.nuevo_codigo()
        return (f"En tu celular, conectado al mismo Wi-Fi, abre {url} y escribe el código {codigo}. Vale 5 minutos."
                " La primera vez tu navegador dirá que el certificado no es de confianza: es el de NOVA, acéptalo.")

    def presentarme(self) -> str:
        e = self.estado()
        mem = e["memoria"]
        return (f"Soy NOVA {__version__}, tu asistente. Corro en tu equipo con {e['motor']}"
                f"{' (' + e['modelo'] + ')' if e['modelo'] and e['modelo'] != 'ninguno' else ''}. "
                f"Internet: {'activo' if e['internet'] else 'apagado'}. "
                f"Llevo {mem['recuerdos']} recuerdos, {mem['conocimiento']} cosas aprendidas y {mem['tareas_pendientes']} tareas pendientes tuyas.")

    def que_se_de_ti(self) -> str:
        nombres = {"quien": "Quién eres", "estilo": "Cómo hablas", "gusto": "Gustos", "proyecto": "Proyectos",
                   "momento": "Momentos", "preferencia": "Cómo quieres que te hable"}
        partes = []
        for tipo, titulo in nombres.items():
            items = self.memoria.recuerdos(tipo, 20)
            if items:
                partes.append(f"**{titulo}**\n" + "\n".join(f"- {r.texto} (#{r.id})" for r in items))
        if not partes:
            return "Todavía no sé mucho de ti. Cuéntame: ¿cómo te llamas y en qué andas trabajando?"
        return "\n\n".join(partes) + "\n\nSi algo está mal, dime «olvida …» y lo borro."

    def resumen_del_dia(self) -> str:
        hoy = dt.date.today().isoformat()
        tareas = self.memoria.tareas()
        atrasadas = [t for t in tareas if t["fecha"] and t["fecha"] < hoy]
        de_hoy = [t for t in tareas if t["fecha"] == hoy]
        otras = [t for t in tareas if not t["fecha"] or t["fecha"] > hoy]
        lineas = [f"Resumen de hoy, {hoy}:"]
        if atrasadas:
            lineas.append("Atrasadas: " + "; ".join(t["texto"] for t in atrasadas))
        if de_hoy:
            lineas.append("Para hoy: " + "; ".join(t["texto"] for t in de_hoy))
        if otras:
            lineas.append("Pendientes: " + "; ".join(t["texto"] + (f" ({t['fecha']})" if t["fecha"] else "") for t in otras[:5])
                          + (f" y {len(otras) - 5} más" if len(otras) > 5 else ""))
        if not tareas:
            lineas.append("No tienes tareas pendientes.")
        notas = self.memoria.notas(3)
        if notas:
            lineas.append("Notas recientes: " + "; ".join(n["texto"] for n in notas))
        from .herramientas import unidades
        bajas = [u for u in unidades() if u.get("libre_pct", 100) < 10]
        if bajas:
            lineas.append("Ojo, poco espacio en: " + ", ".join(f"{u['unidad']} ({u['libre_pct']} %)" for u in bajas))
        return "\n".join(lineas)

    # ----------------------------------------------------------- plática

    def situacion(self) -> platica.Situacion:
        return platica.Situacion(
            ahora=dt.datetime.now(),
            inactivo_min=_inactivo_min(self.ultima_actividad),
            sesion_continua_min=(time.monotonic() - self.inicio_sesion_trabajo) / 60,
            no_molestar=platica.no_molestar_windows(),
        )

    def revisar_platica(self) -> str | None:
        s = self.situacion()
        if s.inactivo_min > 20:
            self.inicio_sesion_trabajo = time.monotonic()
        if self.platica.pendiente and self.platica.ultima and (s.ahora - self.platica.ultima).total_seconds() > 1800:
            platica.respuesta(self.platica, siguio=False)
        ok, _ = platica.puede_hablar(self.config, self.platica, s)
        if not ok:
            return None
        nombre = None
        for r in self.memoria.recuerdos("quien", 10):
            m = re.match(r"Se llama (\w+)", r.texto)
            if m:
                nombre = m.group(1)
                break
        frase = platica.elegir_tema(self.memoria, s, nombre)
        if frase:
            platica.registrar(self.platica, s.ahora)
            self._estado("activa", "apoyo")
            self.bus.publicar({"tipo": "platica", "texto": frase})
            self._estado("reposo", "general")
        return frase

    def arrancar_fondo(self) -> None:
        def ciclo():
            ultimo_sueno = dt.date.today()
            while not self._detener.wait(300):
                try:
                    self.revisar_platica()
                    hoy = dt.date.today()
                    if hoy != ultimo_sueno and dt.datetime.now().hour >= 3:
                        self.memoria.consolidar()
                        ultimo_sueno = hoy
                except Exception:  # noqa: BLE001 - el ciclo de fondo nunca debe morir
                    pass
        threading.Thread(target=ciclo, daemon=True, name="nova-fondo").start()

    def cerrar(self) -> None:
        self._detener.set()
        self.motor.detener()


def sin_pensamientos(texto: str) -> str:
    """Quita bloques <think>…</think> que algunos modelos (Qwen3, DeepSeek) dejan en la respuesta."""
    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.S)
    return re.sub(r"^.*?</think>", "", texto, flags=re.S).strip()


def _primer_json(texto: str) -> str:
    """Recorta el primer objeto JSON balanceado (los modelos pequeños a veces agregan texto después)."""
    nivel, en_cadena, escape = 0, False, False
    for i, c in enumerate(texto):
        if en_cadena:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                en_cadena = False
        elif c == '"':
            en_cadena = True
        elif c == "{":
            nivel += 1
        elif c == "}":
            nivel -= 1
            if nivel == 0:
                return texto[: i + 1]
    return texto


def _inactivo_min(ultima_actividad: float) -> float:
    """Minutos sin teclado ni ratón (Windows: GetLastInputInfo; otros: desde el último mensaje)."""
    if sys.platform == "win32":
        try:
            import ctypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

            info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO), 0)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
                return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 60000
        except (OSError, AttributeError):
            pass
    return (time.monotonic() - ultima_actividad) / 60


def crear(config: Config | None = None, **kw: Any) -> Nova:
    from .config import cargar

    return Nova(config or cargar(), **kw)
