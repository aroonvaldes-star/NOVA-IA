# NOVA

NOVA es una asistente de inteligencia artificial que vive en tu computadora. Es un proyecto propio e independiente: tiene su motor, su memoria, su voz, su personalidad y su avatar, un orbe holográfico que flota libre por tu escritorio. Funciona sin internet y, cuando hay conexión y no sabe algo, lo busca en seguida.

> **Estado: versión 0.1 (MVP).** El núcleo, la memoria, la política de seguridad, internet, la conexión con el celular y el orbe están hechos y probados en Linux (193 pruebas automáticas y prueba del orbe en Chromium). En un Windows real todavía **no se ha probado nada**, ni con un modelo de lenguaje real: ver [Pendiente de verificar](#pendiente-de-verificar).

## Qué hace

| | |
|---|---|
| **Motor propio** | Lanza `llama-server` (llama.cpp) dentro de la app, solo en `127.0.0.1`, cuando lo necesitas, y lo apaga tras 10 minutos sin uso. En Windows vive en un Job Object: si NOVA se cierra, el motor muere con ella. |
| **Personalidad** | Cálida, mexicana, directa y honesta de que es una IA. Editable en `personalidad.md`. |
| **Conciencia de sí misma** | En cada respuesta sabe qué versión es, qué modelo usa, si hay internet, qué voz tiene y qué recuerda. Recuerda cuándo «nació». |
| **Memoria de ti** | Quién eres, cómo hablas, gustos, proyectos, momentos y preferencias. Aprende por reglas al instante («me llamo…», «recuerda que…») y con el modelo cada 10 mensajes. «¿Qué sabes de mí?», «olvida lo de…», «esto no lo recuerdes» (modo privado). Nunca guarda contraseñas, claves, tarjetas ni códigos. |
| **Aprende de internet** | Si responde «no sé» y hay conexión, busca y vuelve a responder citando fuentes. Lo que aprende queda guardado para usarlo sin conexión. |
| **Herramientas** | Buscar y leer en la web, buscar en su memoria y en tus documentos indexados, leer y listar archivos de todas tus unidades, proponer escribir archivos, órdenes (git, dotnet…), calculadora, notas, tareas y discos. |
| **Seguridad** | Nunca corre como administrador. Nada de formatear, particionar, arranque, BitLocker, registro ni borrado masivo. Lo que cambia algo se confirma con un código de 4 cifras que solo aparece en pantalla. Páginas, archivos y pantalla son datos, nunca órdenes. |
| **Voz** | Pulsar para hablar (Ctrl+Alt+Espacio): whisper.cpp para oír y Piper con voz es_MX para hablar, todo local. El orbe late con su voz. |
| **Plática de a ratos** | Hasta 3 veces al día te habla por iniciativa (retoma tus proyectos, pregunta cómo te fue, sugiere un descanso). Nunca con pantalla completa, en llamada, con «No molestar» ni de 23:00 a 08:00. |
| **Orbe** | Red de nodos y conexiones con anillos orbitales. Reposo (azul tenue), activa (azul brillante), procesando (dorado), buscando (verde), hablando (púrpura). Colores por contexto: azul general, cian tecnología, púrpura creatividad, dorado análisis, verde investigación, rojo prioridad, rosa apoyo. Se mueve libre por el escritorio y se aparta del cursor. |
| **Celular** | Conexión directa celular ↔ PC por el Wi-Fi de tu casa, sin nube. Emparejas con un código de 6 cifras. |

## Estructura

```
nova/                 núcleo en Python (sin dependencias obligatorias)
  nucleo.py           el cerebro: une todo y piensa en pasos (ACCION/FINAL)
  motor.py            llama-server administrado (+ motor simulado sin modelo)
  memoria.py          SQLite: recuerdos, conocimiento, conversaciones, notas, tareas, documentos
  aprendizaje.py      cómo aprende de ti
  personalidad.py     identidad, estado propio, colores por contexto
  herramientas.py     lo que puede usar mientras piensa
  politica.py         política de órdenes y rutas (falla cerrada)
  internet.py         buscar y leer, sin redes internas
  voz.py              whisper.cpp, Piper y órdenes rápidas
  platica.py          iniciativa con límites
  celular.py          emparejamiento y HTTPS propio para el celular
  servidor.py         API local para el orbe (y para el celular)
  cli.py              la orden `nova`
ui/                   el orbe (Three.js, sin internet) y la interfaz
desktop/src-tauri/    envoltorio de escritorio: ventana transparente, bandeja, atajos
tests/                pruebas automáticas
```

## Instalar en Windows (por ahora a mano)

Todo se instala con tu usuario, sin permisos de administrador.

1. **Python 3.12** de python.org (marca «Add python.exe to PATH»). Luego, en la carpeta de NOVA:
   ```
   pip install -e .[completo]
   ```
   (`completo` agrega `trafilatura` para leer mejor las páginas y `cryptography` para que la conexión con el celular vaya cifrada.)
2. **Motor (llama.cpp):** de las *releases* de `ggml-org/llama.cpp`, descarga la versión para Windows con **CUDA 12** (tu RTX 3050) y su paquete `cudart`, o la de **Vulkan** si CUDA da problemas. Copia el contenido a `%LOCALAPPDATA%\NOVA\llama\` (debe quedar `llama-server.exe` ahí).
3. **Modelo:** para 4 GB de VRAM, un modelo de 3 a 4B en `Q4_K_M` en formato GGUF (por ejemplo Qwen3 4B Instruct o Llama 3.2 3B Instruct). Instálalo con:
   ```
   nova modelo instalar C:\ruta\al\modelo.gguf
   ```
4. **Oír (opcional):** de las *releases* de `ggml-org/whisper.cpp`, la versión para Windows x64, en `%LOCALAPPDATA%\NOVA\whisper\` (debe quedar `whisper-cli.exe`), y el modelo `ggml-small.bin` en esa misma carpeta.
5. **Hablar (opcional):** Piper para Windows (`rhasspy/piper`) en `%LOCALAPPDATA%\NOVA\piper\` y una voz es_MX de `rhasspy/piper-voices` (por ejemplo `es_MX-claude-high.onnx` con su `.onnx.json`) en la misma carpeta.

Revisa todo con `nova estado`.

## Usar

- **Navegador (lo más rápido para probar):** doble clic en `iniciar-nova.bat`, o `python -m nova servidor --abrir`.
- **Escritorio (orbe libre sobre todas las ventanas):** necesitas Rust y la CLI de Tauri (`cargo install tauri-cli --version "^2"`). Luego en `desktop\src-tauri`:
  ```
  cargo tauri dev        (para probar)
  cargo tauri build      (instalador .exe por usuario)
  ```
  El envoltorio arranca el núcleo solo y lo cierra al salir. **Ctrl+Alt+A** abre la barra para escribir, **Esc** la esconde, mantén **Ctrl+Alt+Espacio** para hablar.
- **Terminal:** `nova chat`, `nova preguntar "…"`, `nova yo`, `nova tareas`, `nova memoria indexar D:\Proyectos`, `nova web buscar "…"`, `nova orden "git status"`.

### Celular

1. Con NOVA abierta en la PC, dile **«conecta mi celular»**. Te da una dirección (por ejemplo `https://192.168.1.20:8191/`) y un código de 6 cifras que vale 5 minutos.
2. En el celular, conectado al mismo Wi-Fi, abre esa dirección. La primera vez el navegador dirá que el certificado no es de confianza: es el certificado propio de NOVA, acéptalo.
3. Escribe el código. Listo: el celular queda emparejado y puedes agregarlo a la pantalla de inicio como app.

Windows te preguntará si permites a Python en redes privadas: acepta solo **redes privadas**. «Desconecta mis celulares» revoca todos. Desde el celular puedes chatear, hablarle y confirmar acciones normales; lo que exige clic en la PC (escribir fuera de tu carpeta o en otra unidad) solo se confirma en la PC. Fuera de tu casa no funciona todavía (ver hoja de ruta).

Para que el servidor del celular arranque siempre, pon `"celular": true` en `%APPDATA%\NOVA\config.json`.

## Configuración

`%APPDATA%\NOVA\config.json` (solo las claves que quieras cambiar). Las principales: `internet`, `internet_searxng_url`, `unidades_excluidas`, `platica`, `platica_max_dia`, `silencio_desde`, `silencio_hasta`, `voz_responder_hablando`, `celular`, `gpu_layers`, `context`. Todas están en `nova/config.py`.

## Pruebas

```
pip install pytest
python -m pytest
```

## Pendiente de verificar

Lo que no se ha podido comprobar todavía, en orden de importancia:

1. **Con un modelo real**: que un modelo de 3–4B siga bien el formato `ACCION:/FINAL:` y la velocidad en la RTX 3050.
2. **El envoltorio de escritorio en Windows**: el código pasa la revisión de tipos de Rust para Windows (`cargo check --target x86_64-pc-windows-msvc`), pero no se ha ejecutado. Falta ver la transparencia de la ventana, el paso de clics y los atajos.
3. **Búsqueda en DuckDuckGo**: el lector de resultados está probado con HTML de ejemplo; si DuckDuckGo bloquea las consultas, usa una instancia de SearXNG (`internet_searxng_url`).
4. **Voz**: los argumentos de `whisper-cli` y `piper` según las versiones que instales.
5. **Celular**: que la red de tu casa permita la conexión y que el micrófono del celular funcione con el certificado propio.

## Hoja de ruta

- **0.2 Siempre atenta:** palabra de activación «Oye, NOVA» (openWakeWord), ver la pantalla con OCR de Windows, vigilantes de proyectos, instalador con todo incluido.
- **0.3 Aprende más:** búsqueda por significado (embeddings locales), consolidación nocturna más inteligente, redactar como tú.
- **0.4 Desarrolladora:** integración con VS Code, crear y probar proyectos completos, crear documentos e imágenes.
- **Celular fuera de casa:** conexión cifrada punto a punto (por ejemplo con Tailscale) sin pasar por servidores de NOVA.
