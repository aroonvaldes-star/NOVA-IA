"""Configuración de NOVA.

Orden de carga: valores por defecto -> config.json del programa ->
config.json del usuario -> variables de entorno NOVA_<CLAVE>.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .rutas import Rutas, rutas

DEFECTO: dict[str, Any] = {
    # Motor local (llama.cpp)
    "endpoint": "http://127.0.0.1:8181",
    "manage_server": True,
    "server_binary": "",
    "chat_model": "",
    "context": 8192,  # con la caché en 8 bits cabe junto a un modelo 4B Q4 en 4 GB de VRAM
    "kv_cache": "q8_0",  # "" para la caché normal (f16)
    # Vista: si junto al modelo hay un proyector mmproj, NOVA puede ver imágenes y tu pantalla
    "vision": True,
    "vision_mmproj": "",
    "vision_en_gpu": False,  # en GPUs de 4 GB el proyector va mejor en el procesador
    "vision_max_tokens": 1024,
    "pantalla_max_ancho": 1280,
    "gpu_layers": 99,
    "server_idle_seconds": 600,
    "motor": "auto",  # auto | llama | simulado
    # Cerebro grande: un llama-server en otro equipo (Oracle Cloud por Tailscale). Solo con «piénsalo bien: …»
    "cerebro_endpoint": "",
    "cerebro_clave": "",
    "cerebro_modelo": "Qwen3 8B en Oracle",  # nombre para mostrar
    "cerebro_api_modelo": "",  # nombre que pide el servicio (p. ej. "openai.gpt-oss-120b" en Oracle IA generativa)
    "cerebro_auto": False,  # True: usa el cerebro grande para todo cuando responde (rápido en la nube); sin red, el local
    # Servidor de la interfaz
    "web_port": 8190,
    # Conexión directa con el celular (red local, solo dispositivos emparejados)
    "celular": False,
    "celular_puerto": 8191,
    "language": "es",
    # Agente y órdenes
    "agente_max_pasos": 8,
    "agente_auto_lectura": True,
    "command_timeout": 60,
    "max_file_bytes": 200_000,
    # Internet como herramienta
    "internet": True,
    "internet_buscador": "duckduckgo",
    "internet_searxng_url": "",
    "internet_max_paginas": 5,
    "internet_max_kb_pagina": 300,
    "internet_timeout": 15,
    "internet_leer_paginas": 2,  # además de los resultados, lee las primeras páginas para responder con detalle
    # Tus archivos: carpetas que NOVA conoce (además de las que le pidas con «conoce la carpeta …»)
    "carpetas": ["~/Documents", "~/Desktop"],
    "indexar_cada_min": 30,
    # Unidades
    "unidades": "todas",
    "unidades_excluidas": [],
    "unidades_extraibles": True,
    # Voz
    "voz_escucha": False,
    "voz_palabra": "oye nova",
    "voz_stt_binario": "",
    "voz_stt_modelo": "ggml-small.bin",
    "voz_tts_binario": "",
    "voz_tts_voz": "es_MX-claude-high.onnx",
    "voz_responder_hablando": True,
    # Orbe
    "orbe_calidad": "alta",
    "orbe_libre": True,
    # Conversación: al reabrir NOVA retoma lo de las últimas horas; lo más viejo queda en la memoria
    "retomar_horas": 3,
    # Memoria personal
    "memoria_personal": True,
    "perfil_max_chars": 1600,
    # Plática de a ratos
    "platica": True,
    "platica_max_dia": 3,
    "platica_minutos_entre": 90,
    "silencio_desde": "23:00",
    "silencio_hasta": "08:00",
}


class Config(dict):
    """Diccionario de configuración con acceso por atributo y rutas."""

    def __init__(self, valores: dict[str, Any], carpetas: Rutas):
        super().__init__(valores)
        self.rutas = carpetas

    def __getattr__(self, nombre: str) -> Any:
        try:
            return self[nombre]
        except KeyError as exc:
            raise AttributeError(nombre) from exc


def _leer_json(ruta: Path) -> dict[str, Any]:
    try:
        with open(ruta, encoding="utf-8-sig") as f:  # tolera el BOM que deja el Bloc de notas
            datos = json.load(f)
        return datos if isinstance(datos, dict) else {}
    except (OSError, ValueError):
        return {}


def _convertir(texto: str, ejemplo: Any) -> Any:
    if isinstance(ejemplo, bool):
        return texto.strip().lower() in {"1", "true", "si", "sí", "yes", "on"}
    if isinstance(ejemplo, int):
        return int(texto)
    if isinstance(ejemplo, float):
        return float(texto)
    if isinstance(ejemplo, list):
        return [p.strip() for p in texto.split(",") if p.strip()]
    return texto


def cargar(carpetas: Rutas | None = None) -> Config:
    carpetas = carpetas or rutas()
    valores = dict(DEFECTO)
    for archivo in (carpetas.programa / "config.json", carpetas.config / "config.json"):
        for clave, valor in _leer_json(archivo).items():
            if clave in DEFECTO:
                valores[clave] = valor
    for clave, ejemplo in DEFECTO.items():
        env = os.environ.get("NOVA_" + clave.upper())
        if env is not None:
            try:
                valores[clave] = _convertir(env, ejemplo)
            except ValueError:
                pass
    return Config(valores, carpetas)
