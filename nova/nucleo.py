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
import os
import queue
import re
import sys
import threading
import time
import uuid
from typing import Any, Callable

from . import __version__, aprendizaje, pantalla, platica, personalidad, politica
from .config import Config
from .celular import Emparejador
from .confirmaciones import Confirmaciones
from .herramientas import CajaDeHerramientas, envolver
from .internet import Internet
from .memoria import Memoria
from .motor import Motor, MotorRemoto, MotorSimulado, elegir
from .voz import Voz, orden_rapida, para_hablar

NO_SABE = re.compile(r"\b(no (lo )?s[eé]\b|no tengo (esa )?informaci[oó]n|no estoy segur[ao]|no conozco|"
                     r"no tengo datos|no puedo saber|mi conocimiento (llega|tiene)|no dispongo|"
                     r"no tengo acceso a (internet|la web|informaci[oó]n)|no puedo (navegar|buscar|acceder)|"
                     r"en tiempo real|fecha de corte|[uú]ltima actualizaci[oó]n de mis datos)", re.I)
# Preguntas que dependen de datos recientes: NOVA busca antes de contestar.
RECIENTE = re.compile(r"\b(hoy|ahorita|actual(es|mente)?|[uú]ltim[oa]s?|reciente(s|mente)?|noticias?|precio|cu[aá]nto (cuesta|vale)|"
                      r"clima|cotizaci[oó]n|d[oó]lar|qui[eé]n gan[oó]|marcador|estreno|lanzamiento|sali[oó]|nueva versi[oó]n|"
                      r"20(2[5-9]|3\d)|esta semana|este (mes|a[nñ]o)|busca|investiga|googlea|en internet|en la web)\b", re.I)
URL = re.compile(r"https?://[^\s<>\"']+")
PASADO = re.compile(r"\b(te acuerdas|recuerdas|la otra vez|ayer|antier|hace (unos |un par de )?d[ií]as|la semana pasada|"
                    r"(me )?dijiste|hablamos|platicamos|quedamos|lo de antes)\b", re.I)
# «Piénsalo bien: …» manda esa pregunta al cerebro grande (otro equipo, más listo y más lento).
PIENSALO = re.compile(r"^\s*(?:oye,?\s*)?(?:nova,?\s*)?(?:pi[eé]nsalo (?:bien|a fondo)|con (?:tu|el) cerebro grande|"
                      r"usa (?:tu|el) cerebro grande|cerebro grande)\s*[:,.]?\s*", re.I)
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
                 internet: Internet | None = None, cerebro: Motor | None = None):
        self.config = config
        config.rutas.crear()
        self.motor = motor or elegir(config)
        self.cerebro = cerebro or (MotorRemoto(config) if config.cerebro_endpoint else None)
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
        self.observador = pantalla.Observador()
        self.herramientas.vision = bool(getattr(self.motor, "vision", False))
        self.indexando = False
        # Retoma la conversación si fue hace poco (la cerraste y la volviste a abrir); lo de días
        # atrás se queda en la memoria, no en la plática, para que no se quede pegada en eso.
        limite = time.time() - float(config.retomar_horas) * 3600
        for m in self.memoria.ultimos_mensajes(10):
            if m["fecha"] >= limite:
                self.historial.append({"role": "user" if m["rol"] == "usuario" else "assistant", "content": m["texto"]})
        self._ultimo_mensaje = time.time()
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
            if time.time() - self._ultimo_mensaje > float(self.config.retomar_horas) * 3600:
                self.historial = []  # pasó mucho rato: empieza una plática nueva
            self._ultimo_mensaje = time.time()
            if self.platica.pendiente:
                platica.respuesta(self.platica, siguio=True)
            ctx = personalidad.contexto(texto)
            self._estado("activa", ctx)
            try:
                self.herramientas.nueva_tarea(texto)
                rapida = orden_rapida(texto)
                respuesta = self._orden_rapida(rapida, texto, por_voz) if rapida else None
                if respuesta is None:
                    usar_cerebro = PIENSALO.match(texto) or (self.cerebro and self.config.cerebro_auto and self.internet.activo)
                    respuesta = self._pensar_con_cerebro(texto, ctx) if usar_cerebro else self._pensar(texto, ctx)
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

    def conversacion(self, limite: int = 40) -> list[dict]:
        """Los últimos mensajes, para que la interfaz los muestre al abrir."""
        return [{"rol": m["rol"], "texto": m["texto"], "fecha": m["fecha"]} for m in self.memoria.ultimos_mensajes(limite)]

    def carpetas(self) -> list[str]:
        """Las carpetas que NOVA conoce: las de config.json más las que le pediste."""
        propias = json.loads(self.memoria.ajuste("carpetas", "[]") or "[]")
        salida = []
        for c in [*self.config.carpetas, *propias]:
            for r in _variantes_carpeta(c):
                if os.path.isdir(r) and r not in salida:
                    salida.append(r)
                    break
        return salida

    def conocer_carpeta(self, ruta: str, quitar: bool = False) -> str:
        propias = json.loads(self.memoria.ajuste("carpetas", "[]") or "[]")
        ruta = os.path.expandvars(os.path.expanduser(ruta.strip()))
        if quitar:
            propias = [p for p in propias if os.path.normcase(p) != os.path.normcase(ruta)]
            self.memoria.fijar("carpetas", json.dumps(propias, ensure_ascii=False))
            return f"Listo, ya no reviso {ruta}."
        chequeo = politica.revisar_ruta(ruta, excluidas=self.config.unidades_excluidas)
        if not chequeo.ok:
            return f"No puedo leer esa carpeta: {chequeo.motivo}"
        if not os.path.isdir(chequeo.ruta):
            return f"No encuentro la carpeta {ruta}."
        if str(chequeo.ruta) not in propias:
            propias.append(str(chequeo.ruta))
            self.memoria.fijar("carpetas", json.dumps(propias, ensure_ascii=False))
        threading.Thread(target=self.indexar, args=([str(chequeo.ruta)],), daemon=True).start()
        return (f"Voy a conocer {chequeo.ruta}. Leo los archivos de texto, código y Word (no los binarios ni node_modules, "
                "bin, obj y similares); te aviso cuando termine. Desde ahí podré contestarte sobre tus archivos.")

    def indexar(self, carpetas: list[str] | None = None) -> dict:
        if self.indexando:
            return {"indexados": 0, "omitidos": 0}
        self.indexando = True
        try:
            r = self.memoria.indexar(carpetas or self.carpetas(), revisar=politica.revisar_ruta,
                                     max_bytes=self.config.max_file_bytes, detener=self._detener.is_set)
            if carpetas:
                self.bus.publicar({"tipo": "aviso", "texto": f"Terminé de revisar {', '.join(carpetas)}: "
                                                             f"{r['indexados']} archivo(s) nuevos o cambiados."})
            return r
        finally:
            self.indexando = False

    def _contexto_relevante(self, texto: str) -> str:
        partes = []
        for d in self.memoria.buscar_documentos(texto, 3):
            partes.append(f"- (archivo {d['ruta']}) {d['fragmento']}")
        for r in self.memoria.buscar_recuerdos(texto, 5):
            partes.append(f"- ({r.tipo}) {r.texto}")
        for k in self.memoria.buscar_conocimiento(texto, 3):
            partes.append(f"- (aprendido de {k['url']}, {dt.date.fromtimestamp(k['creado'])}) {k['tema']}: {k['texto'][:500]}")
        if PASADO.search(texto):  # solo si hablas de algo que ya platicaron
            for c in self.memoria.buscar_conversaciones(texto, 3):
                partes.append(f"- (conversación del {dt.date.fromtimestamp(c['fecha'])}, {c['rol']}) {c['texto'][:300]}")
        return envolver("memoria", "\n".join(partes)) if partes else ""

    def _pensar_con_cerebro(self, texto: str, ctx: str) -> str:
        pregunta = PIENSALO.sub("", texto, count=1).strip() or texto
        if not self.cerebro:
            self.bus.publicar({"tipo": "aviso", "texto": "Todavía no tengo cerebro grande configurado; lo pienso con mi modelo de siempre."})
            return self._pensar(pregunta, ctx)
        if not self.config.cerebro_auto:
            self.bus.publicar({"tipo": "aviso", "texto": f"Lo pienso con mi cerebro grande ({self.cerebro.modelo}). Puede tardar unos minutos."})
        try:
            return self._pensar(pregunta, ctx, motor=self.cerebro)
        except (RuntimeError, OSError) as exc:
            self.bus.publicar({"tipo": "aviso", "texto": f"No pude usar el cerebro grande ({exc}); lo pienso aquí."})
            return self._pensar(pregunta, ctx)

    def _pensar(self, texto: str, ctx: str, sembrado: list[dict] | None = None, motor: Motor | None = None) -> str:
        motor = motor or self.motor
        vision_antes = self.herramientas.vision
        self.herramientas.vision = vision_antes and bool(getattr(motor, "vision", False))
        try:
            return self._pensar_con(motor, texto, ctx, sembrado)
        finally:
            self.herramientas.vision = vision_antes

    def _pensar_con(self, motor: Motor, texto: str, ctx: str, sembrado: list[dict] | None) -> str:
        if not self.memoria.privado and self.config.memoria_personal:
            nuevos = aprendizaje.aprender_por_reglas(self.memoria, texto)
            if nuevos:
                self.bus.publicar({"tipo": "aprendi", "recuerdos": nuevos})
        en_linea = self.internet.hay_conexion()
        self.observador.anotar(pantalla.ventana_activa())
        sistema = personalidad.prompt_sistema(
            self.personalidad,
            personalidad.estado_propio(motor.nombre, motor.modelo, en_linea, self.memoria.estado(), self.voz.capacidades(),
                                       vision=self.herramientas.vision,
                                       entorno=self.observador.resumen() + (
                                           " Para tareas difíciles tengo un cerebro grande en otro equipo, más lento: el usuario "
                                           "lo usa empezando con «piénsalo bien:». Puedo sugerírselo." if self.cerebro and motor is not self.cerebro else "")),
            self.memoria.perfil(self.config.perfil_max_chars) if self.config.memoria_personal else "",
            self._contexto_relevante(texto),
        )
        if sembrado is None and en_linea and self.internet.activo:
            sembrado = self._investigar_antes(texto, ctx)
        mensajes = [{"role": "system", "content": sistema}, *self.historial[-10:], {"role": "user", "content": texto}, *(sembrado or [])]
        busco, reintento, ultima_accion = bool(sembrado), False, None
        for _ in range(int(self.config.agente_max_pasos)):
            self._estado("procesando", ctx)
            salida = sin_pensamientos(motor.chat(mensajes, al_token=self._emisor()))
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
                mensajes += [{"role": "assistant", "content": salida}, _mensaje_resultado(nombre, resultado)]
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

    def _investigar_antes(self, texto: str, ctx: str) -> list[dict] | None:
        """Si escribiste un enlace, lo lee; si la pregunta depende de datos recientes, busca primero."""
        urls = URL.findall(texto)[:2]
        if urls:
            sembrado = []
            for url in urls:
                r = self._usar("leer_web", {"url": url}, ctx)
                sembrado += [{"role": "assistant", "content": 'ACCION: {"herramienta": "leer_web", "args": ' + json.dumps({"url": url}) + "}"},
                             _mensaje_resultado("leer_web", r)]
            return sembrado
        if RECIENTE.search(texto):
            consulta = _consulta(texto)
            r = self._usar("buscar_web", {"consulta": consulta}, ctx)
            return [{"role": "assistant", "content": 'ACCION: {"herramienta": "buscar_web", "args": ' + json.dumps({"consulta": consulta}, ensure_ascii=False) + "}"},
                    _mensaje_resultado("buscar_web", r, "Usa esto si sirve para responder; cita las páginas.")]
        return None

    def _usar(self, nombre: str, args: dict, ctx: str):
        self._estado("buscando" if nombre in {"buscar_web", "leer_web"} else "procesando",
                     "investigacion" if nombre in {"buscar_web", "leer_web"} else ctx)
        if nombre == "ver_pantalla":
            self.bus.publicar({"tipo": "viendo"})
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
        if a == "zona":
            return "Todavía no sé mirar solo una zona; dime «mira mi pantalla» y veo todo."
        if a == "pantalla":
            resultado = self._usar("ver_pantalla", {}, "general")
            if isinstance(self.motor, MotorSimulado) and not self.motor.guion:
                return resultado.texto
            sembrado = [{"role": "assistant", "content": 'ACCION: {"herramienta": "ver_pantalla", "args": {}}'},
                        _mensaje_resultado("ver_pantalla", resultado, "Describe brevemente qué estoy haciendo y ofrece ayuda concreta, con FINAL:.")]
            return self._pensar(texto, "general", sembrado)
        if a == "carpeta":
            return self.conocer_carpeta(args["ruta"], quitar=bool(args.get("quitar")))
        if a == "analizar_carpeta":
            resultado = self._usar("analizar_carpeta", args, "analisis")
            if resultado.texto.startswith(("No puedo", "No encuentro")):
                return resultado.texto
            self.conocer_carpeta(args["ruta"])  # además la indexa para preguntas después
            if isinstance(self.motor, MotorSimulado) and not self.motor.guion:
                return resultado.texto[:1500]
            sembrado = [{"role": "assistant", "content": 'ACCION: {"herramienta": "analizar_carpeta", "args": ' + json.dumps(args, ensure_ascii=False) + "}"},
                        _mensaje_resultado("analizar_carpeta", resultado,
                                           "Explica en pocas frases qué es este proyecto o carpeta, cómo está organizado y qué "
                                           "llama la atención. Ofrece profundizar en una parte. Usa FINAL:.")]
            return self._pensar(texto, "analisis", sembrado)
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
        self.observador.arrancar()

        def indexador():
            # Revisa tus carpetas al abrir y luego cada cierto tiempo (solo relee lo que cambió).
            if self._detener.wait(20):
                return
            while True:
                try:
                    self.indexar()
                except Exception:  # noqa: BLE001
                    pass
                if self._detener.wait(max(5, int(self.config.indexar_cada_min)) * 60):
                    return
        threading.Thread(target=indexador, daemon=True, name="nova-indexador").start()

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
        self.observador.detener()
        self.motor.detener()


def _mensaje_resultado(nombre: str, resultado, indicacion: str = "") -> dict:
    """El resultado de una herramienta como mensaje; con imagen si el motor tiene vista."""
    texto = f"RESULTADO de {nombre}:\n{envolver(resultado.origen, resultado.texto)}" + (f"\n{indicacion}" if indicacion else "")
    if getattr(resultado, "imagen", ""):
        return {"role": "user", "content": [{"type": "text", "text": texto},
                                             {"type": "image_url", "image_url": {"url": resultado.imagen}}]}
    return {"role": "user", "content": texto}


def _consulta(texto: str) -> str:
    """Pregunta del usuario convertida en consulta de búsqueda (sin saludos ni relleno)."""
    t = re.sub(r"^(oye,? )?(nova,? )?", "", texto.strip(), flags=re.I)
    t = re.sub(r"^(por favor,? )?(me (puedes|podr[ií]as) )?(busca(r)?|investiga(r)?|googlea(r)?|dime|sabes)( en internet| en la web)?\s*", "", t, flags=re.I)
    return t.strip(" ¿?¡!.") or texto


def _variantes_carpeta(c: str) -> list[str]:
    """«~/Documents» puede estar en OneDrive o con nombre en español."""
    r = os.path.expandvars(os.path.expanduser(c))
    variantes = [r]
    casa = os.path.expanduser("~")
    nombre = os.path.basename(r.rstrip("\\/"))
    if os.path.dirname(r.rstrip("\\/")) == casa:
        otros = {"Documents": ["Documentos"], "Desktop": ["Escritorio"], "Downloads": ["Descargas"]}.get(nombre, [])
        for n in [nombre, *otros]:
            variantes += [os.path.join(casa, "OneDrive", n), os.path.join(casa, n)]
    return variantes


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
