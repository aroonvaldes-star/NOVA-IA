"""Cómo aprende NOVA de ti.

1. Al instante, con reglas: «recuerda que…», «me llamo…», «me gusta…»,
   «estoy trabajando en…», «no me hables tan…».
2. Al terminar una conversación (o cada N mensajes), el modelo local propone
   recuerdos candidatos SOLO a partir de lo que tú escribiste; los de
   confianza alta se guardan.
3. Por la noche, la consolidación («sueño») junta y envejece recuerdos.

NOVA no reentrena el modelo: aprende con memoria, perfil y ejemplos de tu estilo.
"""

from __future__ import annotations

import json
import re

from .memoria import TIPOS_USUARIO, Memoria

_REGLAS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(?:me llamo|mi nombre es)\s+([A-Za-zÁÉÍÓÚÑáéíóúñ]+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)?)", re.I), "quien", "Se llama {0}"),
    (re.compile(r"\b(?:trabajo (?:como|de)|soy (?:un |una )?(?:desarrollador|programador|ingeniero|estudiante|diseñador)\w*)\s*([^.,;!?]*)", re.I), "quien", "{m}"),
    (re.compile(r"\bno me gusta(?:n)?\s+([^.;!?]{2,80})", re.I), "gusto", "No le gusta {0}"),
    (re.compile(r"(?<!no )\bme (?:gusta(?:n)?|encanta(?:n)?)\s+([^.;!?]{2,80})", re.I), "gusto", "Le gusta {0}"),
    (re.compile(r"\b(?:estoy trabajando en|estoy haciendo|mi proyecto(?: se llama)?(?: es)?)\s+([^.;!?]{2,120})", re.I), "proyecto", "Trabaja en {0}"),
    (re.compile(r"\b(?:prefiero que|no me hables|háblame|hablame|dime siempre|no me digas)\s*([^.;!?]{0,120})", re.I), "preferencia", "{m}"),
    (re.compile(r"\b(?:mañana|hoy|el (?:lunes|martes|miércoles|jueves|viernes|sábado|domingo)) tengo\s+([^.;!?]{2,100})", re.I), "momento", "{m}"),
]

_RECUERDA = re.compile(r"^\s*(?:nova[,:]?\s*)?(?:recuerda|acuérdate|acuerdate|apunta en tu memoria)\s+(?:que\s+)?(.+)$", re.I | re.S)


def tipo_de(texto: str) -> str:
    t = texto.lower()
    if re.search(r"\bme gusta|\bno me gusta|\bme encanta|\bodio\b|favorit", t):
        return "gusto"
    if re.search(r"\bproyecto|\btrabajo en|\bestoy haciendo|\brepo", t):
        return "proyecto"
    if re.search(r"\bh[aá]blame|\bprefiero|\bno me hables|\bresp[oó]nde", t):
        return "preferencia"
    if re.search(r"\bmañana|\bhoy\b|\bexamen|\bcita\b|\bviaje", t):
        return "momento"
    return "quien"


def recuerdo_explicito(texto: str) -> str | None:
    m = _RECUERDA.match(texto)
    return m.group(1).strip().rstrip(".") if m else None


def aprender_por_reglas(memoria: Memoria, texto: str, fuente: str = "usuario") -> list[str]:
    """Aplica las reglas a un mensaje del usuario. Devuelve los recuerdos guardados."""
    guardados = []
    explicito = recuerdo_explicito(texto)
    if explicito:
        if memoria.recordar(tipo_de(explicito), _tercera_persona(explicito), fuente, confianza=1.0, importancia=1.5):
            guardados.append(explicito)
        return guardados
    for patron, tipo, plantilla in _REGLAS:
        for m in patron.finditer(texto):
            valor = " ".join(m.group(1).split()).strip(" ,")
            if not valor and "{0}" in plantilla:
                continue
            frase = plantilla.format(valor, m=" ".join(m.group(0).split()))
            if memoria.recordar(tipo, _tercera_persona(frase), fuente, confianza=0.85):
                guardados.append(frase)
    return guardados


def _tercera_persona(texto: str) -> str:
    """«me gusta el café» -> «le gusta el café» (aproximado, suficiente para el perfil)."""
    cambios = [(r"\bme llamo\b", "se llama"), (r"\bmi\b", "su"), (r"\bmis\b", "sus"), (r"\bme\b", "le"),
               (r"\byo\b", "él/ella"), (r"\bestoy\b", "está"), (r"\btengo\b", "tiene"), (r"\bsoy\b", "es"),
               (r"\bprefiero\b", "prefiere"), (r"\btrabajo\b", "trabaja")]
    t = texto
    for a, b in cambios:
        t = re.sub(a, b, t, flags=re.I)
    return t[:1].upper() + t[1:]


PROMPT_EXTRACCION = """Lee SOLO los mensajes del usuario y extrae datos duraderos sobre él o ella.
Tipos: quien (identidad, trabajo, equipo), estilo (cómo escribe o habla), gusto, proyecto (estado, decisiones), momento (algo próximo o reciente), preferencia (cómo quiere que NOVA le hable o actúe).
No extraigas contraseñas, claves, números de cuenta ni códigos. No inventes.
Responde SOLO con un JSON: [{"tipo": "...", "texto": "frase corta en tercera persona", "confianza": 0.0-1.0}]
Si no hay nada, responde []."""


def extraer_con_modelo(motor, memoria: Memoria, mensajes_usuario: list[str], umbral: float = 0.75) -> list[str]:
    """El modelo local propone recuerdos; se guardan los de confianza alta."""
    if not mensajes_usuario:
        return []
    texto = "\n".join(f"- {m[:600]}" for m in mensajes_usuario[-20:])
    try:
        salida = motor.chat([{"role": "system", "content": PROMPT_EXTRACCION},
                             {"role": "user", "content": texto}], temperatura=0.1, max_tokens=600)
    except Exception:  # noqa: BLE001 - sin motor no se aprende, pero no se rompe nada
        return []
    m = re.search(r"\[.*\]", salida, re.S)
    if not m:
        return []
    try:
        candidatos = json.loads(m.group(0))
    except ValueError:
        return []
    guardados = []
    for c in candidatos if isinstance(candidatos, list) else []:
        if not isinstance(c, dict):
            continue
        tipo, frase = str(c.get("tipo", "")), str(c.get("texto", "")).strip()
        try:
            confianza = float(c.get("confianza", 0))
        except (TypeError, ValueError):
            continue
        if tipo in TIPOS_USUARIO and frase and confianza >= umbral:
            if memoria.recordar(tipo, frase, "usuario", confianza=confianza):
                guardados.append(frase)
    return guardados
