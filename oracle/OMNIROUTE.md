# OmniRoute: un solo cerebro grande con varios proveedores gratis

OmniRoute es un enrutador que corre en tu propia PC. NOVA le pide todo con el modelo `auto` y OmniRoute escoge un proveedor con cuota. Si Groq se agota, cambia solo a Cerebras, OpenRouter o el que tengas. Así ya no se acaba el cerebro grande a las 4 preguntas.

- Proyecto: https://github.com/diegosouzapw/OmniRoute (licencia MIT). NOVA instala la versión 3.8.52 fija.
- Corre con tu usuario normal, sin administrador, en `%LOCALAPPDATA%\NOVA\omniroute`, con su propio Node en `%LOCALAPPDATA%\NOVA\node`.
- Sus datos (claves cifradas, registros) quedan en `%LOCALAPPDATA%\NOVA\omniroute-datos`.

## Instalarlo

`actualizar.bat` lo instala solo la primera vez. También puedes abrir `omniroute.bat` cuando quieras agregar o cambiar claves. Hace esto:

1. Baja Node 24 oficial y verifica su huella SHA256.
2. Instala OmniRoute. Pesa unos 500 MB y tarda unos minutos.
3. Lo deja escuchando **solo en 127.0.0.1**: nadie de tu red ni de internet puede usar tus cuotas. Crea secretos propios, una contraseña del panel al azar y una clave que solo NOVA conoce.
4. Si ya usabas Groq, le pasa esa clave a OmniRoute.
5. Te pregunta por más claves gratis. Escríbelas ahí: no se ven al escribir y nunca hay que pegarlas en un chat.
6. Cambia el cerebro grande de NOVA a `http://127.0.0.1:20128/v1` con el modelo `auto`. Tu config anterior queda en `config.json.antes-omniroute`.

NOVA prende OmniRoute sola cada vez que abre.

## Proveedores gratis recomendados

Usa solo **claves de API oficiales** de cada servicio. No conectes cuentas de suscripción (Claude Code, Copilot, ChatGPT Plus, Kiro…): sus reglas no permiten usarlas desde otro programa y te pueden cerrar la cuenta.

| Proveedor | Dónde sacas la clave | Notas |
|---|---|---|
| `groq` | https://console.groq.com → API Keys | Muy rápido; unas 1,000 peticiones al día. |
| `cerebras` | https://cloud.cerebras.ai → API Keys | Rápido; plan gratis con límite diario. |
| `openrouter` | https://openrouter.ai/keys | Modelos `:free`; unas 50 peticiones al día. |
| `gemini` | https://aistudio.google.com/apikey | Límites bajos; en el plan gratis Google puede usar lo que mandes para mejorar sus modelos. |

Lo que mandes al cerebro grande sale de tu PC hacia el proveedor que conteste. Si no hay internet o todos se agotan, NOVA contesta con su modelo local.

## La auditoría: quién contestó cada vez

- Pregúntale a NOVA **«¿quién contestó?»**. Te dice las últimas llamadas: qué modelo respondió (el real, aunque OmniRoute haya cambiado de proveedor), cuánto tardó y si falló.
- Todo queda en `%LOCALAPPDATA%\NOVA\estado\auditoria.jsonl`.
- El panel de OmniRoute, http://127.0.0.1:20128, tiene el detalle completo por proveedor: tokens, cuotas, costos y cada cambio automático.
- La contraseña inicial del panel está en `%LOCALAPPDATA%\NOVA\omniroute-datos\contrasena-del-panel.txt`. Cámbiala en Settings → Security.

## Si algo falla

- Corre `nova diagnostico`. En «Cerebro grande» te dice si OmniRoute está prendido y si contesta.
- Error 503: OmniRoute no tiene proveedores con cuota. Abre `omniroute.bat` y agrega otra clave.
- Para volver a Groq directo, copia `config.json.antes-omniroute` encima de `config.json`.
