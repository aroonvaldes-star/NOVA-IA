# Cerebro grande con Oracle IA generativa (rápido, de pago)

Con esto NOVA usa un modelo grande de Oracle para todo cuando hay internet (rápido y bueno con código), y su modelo de la laptop cuando no hay red.

**Importante:** lo que le preguntes a NOVA, incluido el código o los archivos que analice, se manda a Oracle (y, con los modelos Grok o Gemini, a xAI o Google). El primer mes lo pagan los créditos de prueba; después Oracle cobra por uso.

## 1. Activar una región que tenga el servicio

La IA generativa no está en Querétaro. Las regiones que aceptan claves simples son Phoenix, Chicago, Ashburn, Frankfurt, Hyderabad y Osaka. La más cercana es **US West (Phoenix)**.

1. En la consola, menú de regiones (arriba a la derecha) → **Administrar regiones**.
2. Busca **US West (Phoenix)** y pulsa **Suscribirse**. Tarda unos minutos.
3. Cambia a Phoenix en el menú de regiones.

## 2. Crear la clave de la IA generativa

1. Menú → **Análisis y AI → IA generativa**.
2. Busca **Claves de API** (API keys) y crea una. Nombre: `nova`.
3. **Copia el secreto en ese momento**: Oracle solo lo muestra una vez. No lo pegues en ningún chat, tampoco aquí.
4. Si Oracle pide permisos para usar la clave, agrega la política que indica su página de claves de API (en Identidad → Políticas).

Documentación de Oracle: [Claves de API](https://docs.oracle.com/en-us/iaas/Content/generative-ai/api-keys.htm) · [Endpoints compatibles con OpenAI](https://docs.oracle.com/en-us/iaas/Content/generative-ai/openai-compatible-api.htm)

## 3. Decirle a NOVA

Abre `%APPDATA%\NOVA\config.json` (si no existe, créalo) y deja algo así:

```json
{
  "cerebro_endpoint": "https://inference.generativeai.us-phoenix-1.oci.oraclecloud.com/20231130/actions/v1",
  "cerebro_clave": "EL-SECRETO-QUE-COPIASTE",
  "cerebro_api_modelo": "openai.gpt-oss-120b",
  "cerebro_modelo": "gpt-oss 120B en Oracle",
  "cerebro_auto": true
}
```

Reinicia NOVA. Si algo falla, NOVA te avisa y contesta con su modelo local; mándame el aviso.

Otros modelos que puedes probar en `cerebro_api_modelo` (según lo que Oracle tenga en Phoenix): `meta.llama-4-maverick-17b-128e-instruct-fp8`, `xai.grok-4`. Los nombres exactos aparecen en la consola de IA generativa, en la lista de modelos.
