# Cerebro grande gratis con Groq (rápido, con límites)

No existen servidores gratis con GPU para dejar un modelo grande prendido todo el día (Oracle Always Free no trae GPU; Colab y Kaggle se apagan solos). Lo que sí existe son **APIs gratis** de modelos grandes. La mejor para NOVA hoy es **Groq**: muy rápida, buena con código y compatible con NOVA sin cambiar nada.

**Importante:** lo que le preguntes a NOVA con el cerebro grande, incluido código o archivos, sale de tu PC hacia Groq. Groq dice que no entrena con tus datos, pero guarda registros hasta 30 días. Si no hay internet o se acaba el límite, NOVA contesta con su modelo local.

## Límites gratis (octubre 2026)

- Modelo `openai/gpt-oss-120b`: 30 peticiones por minuto, 1,000 al día, 200,000 tokens al día.
- Alternativa: `qwen/qwen3.8-27b` (en vista previa, puede cambiar).

## 1. Crear la clave

1. Entra a https://console.groq.com y crea una cuenta.
2. Ve a **API Keys** → **Create API Key**, nombre `nova`.
3. Copia la clave en ese momento. No la pegues en ningún chat, tampoco aquí.

## 2. Decirle a NOVA

Abre `%APPDATA%\NOVA\config.json` (si no existe, créalo) y deja algo así:

```json
{
  "cerebro_endpoint": "https://api.groq.com/openai/v1",
  "cerebro_clave": "LA-CLAVE-QUE-COPIASTE",
  "cerebro_api_modelo": "openai/gpt-oss-120b",
  "cerebro_modelo": "gpt-oss 120B en Groq",
  "cerebro_auto": true
}
```

Con `cerebro_auto` en `true` usa el cerebro grande siempre que haya red. Si prefieres usarlo solo cuando lo pidas, ponlo en `false` y empieza tu mensaje con «piénsalo bien:».

Si lo haces en el Bloc de notas, en «Guardar como» elige **Tipo: Todos los archivos**. Si se guarda como `config.json.txt`, NOVA también lo lee.

Reinicia NOVA. Si algo falla, NOVA te avisa y contesta con su modelo local; mándame el aviso.

## Segunda opción: OpenRouter

- Base: `https://openrouter.ai/api/v1`, modelos `qwen/qwen3-coder:free` (especial para código) u `openai/gpt-oss-120b:free`.
- Gratis son unas 50 peticiones al día (1,000 si compras 10 dólares de crédito una vez). Cada modelo gratis lo da otro proveedor con sus propias reglas de datos.

## Listas que revisé

- https://agentdeals.dev/free-llm-apis (la más al día, octubre 2026)
- https://openrouter.ai/blog/tutorials/free-llm-apis-compared/
- https://github.com/cheahjs/free-llm-api-resources
- https://github.com/amardeeplakshkar/awesome-free-llm-apis (de marzo 2026, varios límites ya cambiaron)

Descartadas: GitHub Models (al parecer lo retiraron en julio 2026), Hugging Face (ya no da créditos gratis), Mistral (su API ya no es para uso personal), Google Gemini y NVIDIA (límites muy bajos o usan tus datos).
