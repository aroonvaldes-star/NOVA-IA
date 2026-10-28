# Primera prueba de NOVA en tu Windows

## Recibir los cambios sin zip (desde GitHub)

NOVA ahora vive en tu repositorio privado `aroonvaldes-star/NOVA-IA`. Ya no hace falta bajar el zip.

**La primera vez**, abre PowerShell (la terminal de VS Code sirve) dentro de tu carpeta de NOVA, la que tiene `instalar.bat`, y pega:

```powershell
git init -b main; git remote add origin https://github.com/aroonvaldes-star/NOVA-IA.git; git fetch origin main; git reset --hard origin/main; .\instalar.bat
```

Si GitHub te pide entrar, hazlo con tu cuenta en la ventana que abre. El instalador reutiliza lo que ya bajaste.

**Desde entonces**, cada vez que te avise de cambios: doble clic en `actualizar.bat`. Cierra NOVA, baja solo lo que cambió, reinstala dependencias si hace falta y la vuelve a abrir. Si cambia el instalador, te avisa para correr `instalar.bat`.

**NOVA libre en el escritorio.** Con la carpeta conectada a GitHub, el instalador y `actualizar.bat` bajan el orbe de escritorio ya compilado. Quedan dos accesos:

- **NOVA** (Ctrl+Alt+N): el orbe flota libre sobre tus ventanas, sin marco, y deja pasar los clics. Clic en el orbe, **Ctrl+Alt+A** (o Alt Gr+A) o clic en su icono junto al reloj abre la barra para escribirle y **Ctrl+Alt+Espacio** (mantenido) es para hablarle. **Alt Gr+Q** (o Ctrl+Shift+Q) la esconde en el icono junto al reloj y la vuelve a sacar; mientras NOVA está abierta, Alt Gr+Q no escribe @. Para cerrarla: clic derecho en ese icono → Salir.
- **NOVA en ventana**: la ventana de siempre.

## Actualizar a la 0.2 con el zip (si no usas GitHub)

1. Cierra NOVA.
2. Descomprime `NOVA-0.2.zip` en una carpeta nueva, por ejemplo `N:\NOVORA_WORKSPACE\NOVA IA\NOVA-0.2`.
3. En PowerShell, dentro de esa carpeta `NOVA`: `Get-ChildItem -Recurse | Unblock-File` y luego `.\instalar.bat`.
4. El instalador reutiliza lo que ya bajaste y solo agrega el **modelo con vista** (unos 3.3 GB). Los accesos directos pasan a la carpeta nueva; la vieja ya la puedes borrar.
5. Abre NOVA y prueba lo nuevo:
   - `conoce la carpeta N:\NOVORA_WORKSPACE` → te avisa cuando termine de leerla. Luego pregúntale algo de tus proyectos.
   - Abre VS Code con NOVORA-LINK, vuelve a NOVA y escribe `mira mi pantalla` → debe decir qué estás haciendo.
   - `¿Cuál es el precio del dólar hoy?` → busca antes de contestar y abajo muestra las páginas.
   - Cierra y vuelve a abrir NOVA → la conversación sigue ahí y NOVA se acuerda de lo que hablaron.

Unos 20 a 40 minutos, casi todo es la descarga del modelo (2.5 GB). Ve marcando cada paso; si algo sale distinto, mándame el archivo `diagnostico.txt` (paso 3) y lo que viste.

## 1. Pon la carpeta en su lugar

1. Descomprime `NOVA-0.2.zip` en una carpeta que no vayas a mover, por ejemplo `Documentos\NOVA`.
2. Si Windows marca los archivos como «descargados de internet»: clic derecho en `instalar.bat` → Propiedades → casilla **Desbloquear** → Aceptar.

## 2. Instala con doble clic

1. Doble clic en **`instalar.bat`**. **No** uses «Ejecutar como administrador»: NOVA se niega a correr así.
2. Si aparece «Windows protegió tu PC», pulsa **Más información → Ejecutar de todas formas** (el script no está firmado).
3. Verás pasos en azul. Lo que hace:
   - Busca Python 3.11 o más nuevo; si no tienes, lo instala para tu usuario con winget.
   - Crea el entorno de NOVA en `%LOCALAPPDATA%\NOVA\venv`.
   - Detecta tu RTX 3050 y baja **llama.cpp con CUDA 12** (o Vulkan si tu controlador es viejo).
   - Baja el modelo **Qwen3 4B Instruct** y verifica su huella SHA256.
   - Baja el oído (whisper.cpp + modelo small) y la voz (Piper, español de México).
   - Crea el acceso **NOVA** en el menú Inicio y el escritorio (atajo **Ctrl+Alt+N**).
4. Al final corre el diagnóstico y abre NOVA en su propia ventana.

Si se corta la descarga, vuelve a ejecutar `instalar.bat`: lo que ya bajó y está verificado no lo repite. Cierra NOVA antes de reinstalar.

## 3. Lee el diagnóstico

El instalador muestra una lista con ✔ (bien), ! (aviso) y ✘ (falla). Se guarda en `%LOCALAPPDATA%\NOVA\estado\diagnostico.txt`. Puedes volver a correrlo cuando quieras desde una terminal en la carpeta de NOVA:

```
%LOCALAPPDATA%\NOVA\venv\Scripts\python.exe -m nova diagnostico
```

Fíjate sobre todo en estas líneas:

| Línea | Qué esperar en tu RTX 3050 |
|---|---|
| `llama-server` y `Versión` | ✔. Si dice que no arranca, falta actualizar el controlador de NVIDIA. |
| `El motor arrancó en … s` | De 5 a 30 s la primera vez. |
| `Primera respuesta … tokens/s` | Más de 20 tokens/s con CUDA. Si sale muy bajo, no está usando la GPU. |
| `Uso de herramientas` | Debe empezar con `ACCION:`. Si no, el modelo no sigue el formato y lo cambiamos. |
| `Memoria de video con el modelo cargado` | Menos de 4096 MiB. Si llega al tope, bajamos `context`. |
| `DuckDuckGo` / `Wikipedia` | Al menos uno con resultados. |
| `Me escuché a mí misma` | El texto debe parecerse a «Hola, soy NOVA». |

## 4. Prueba a NOVA

Abre NOVA (acceso del menú Inicio o Ctrl+Alt+N) y escribe, una por una:

1. `Hola, me llamo Aroon y estoy trabajando en NOVORA-LINK` → debe saludarte y en pantalla aparecer «Lo recordaré».
2. `¿Qué sabes de mí?` → debe listar tu nombre y el proyecto.
3. `¿Cuál es la última versión de llama.cpp?` → el orbe se pone **verde** (buscando) y responde citando una página.
4. `¿Cuánto es 1234 por 5678?` → 7006652.
5. `Lista los archivos de C:\Users\<tu usuario>\Documents` → debe listarlos sin pedir permiso.
6. `Crea el archivo C:\Users\<tu usuario>\Documents\prueba-nova.txt con el texto hola` → debe aparecer una **tarjeta de confirmación con código**. Confirma y revisa que el archivo exista.
7. `Ejecuta format C:` → debe negarse (PROHIBIDO). Nada se ejecuta.
8. Mantén presionado el botón 🎙, di «¿qué tengo hoy?» y suelta → debe transcribirte y responder. Si en el paso anterior le pediste algo con la voz, te contesta hablando y el orbe se pone **púrpura**.
9. `Desconéctate` y luego `¿Quién ganó el último mundial?` → responde sin internet (con lo que sabe) y no busca. Después `Conéctate`.

## 5. Prueba el celular

1. En NOVA escribe `conecta mi celular`. Te da una dirección tipo `https://192.168.x.x:8191/` y un código de 6 cifras.
2. Windows te preguntará por el Firewall: permite **solo redes privadas**.
3. En el celular (mismo Wi-Fi) abre la dirección, acepta el aviso del certificado (es el de NOVA) y escribe el código.
4. Escríbele algo desde el celular y mira que conteste. Prueba también el 🎙 del celular.

## 6. Mándame

- `%LOCALAPPDATA%\NOVA\estado\diagnostico.txt`
- Qué pasos de la sección 4 y 5 fallaron y qué viste (una captura ayuda).
- Si el motor falló: `%LOCALAPPDATA%\NOVA\estado\llama-server.log` (las últimas 50 líneas).

## Si algo sale mal

| Síntoma | Qué hacer |
|---|---|
| «instalar.ps1 no está firmado digitalmente» | No abras el `.ps1` directo. En PowerShell, dentro de la carpeta: `Get-ChildItem -Recurse \| Unblock-File` y luego `.\instalar.bat`. |
| El instalador dice que no encontró Python y winget falla | Instala Python 3.12 desde python.org marcando «Add python.exe to PATH» y vuelve a correr `instalar.bat`. |
| `llama-server` no arranca con CUDA | Actualiza el controlador de NVIDIA o instala con Vulkan: abre una terminal en la carpeta y ejecuta `instalar.bat -Motor vulkan`. |
| Va muy lento | Revisa en el diagnóstico que la variante sea CUDA. En el Administrador de tareas → Rendimiento → GPU, la memoria dedicada debe subir al usar NOVA. |
| No contesta y el orbe se queda dorado | Mira `llama-server.log`; puede ser falta de memoria de video. Crea `%APPDATA%\NOVA\config.json` con `{"context": 2048}`. |
| El micrófono no funciona | Configuración → Privacidad → Micrófono → permitir a las apps de escritorio. |
| La ventana de NOVA no abre | Ejecuta `iniciar-nova.bat`; si no, abre `http://127.0.0.1:8190/` en el navegador. |
