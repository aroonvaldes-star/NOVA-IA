# NOVA: lo que llevamos

Resumen al 27 de octubre de 2026. Versión actual: **0.1.1**.

## 1. Qué es NOVA y qué decidimos

NOVA (antes ATLAS) es una asistente de escritorio para Windows que vive en tu PC. Pediste que fuera **totalmente independiente** de NOVORA OS y de tus otros proyectos:

- Motor propio: corre un modelo de lenguaje local con llama.cpp, sin depender de servicios en la nube.
- Animaciones propias: un orbe holográfico, no el cerebro de ATLAS.
- Personalidad propia, memoria de sí misma y de ti, y aprendizaje propio.
- Voz propia: habla y escucha.
- Si no sabe algo, lo busca en internet en el momento.
- Funciona sin internet; internet es solo una herramienta más.

Otras decisiones:

- **Todo local.** No hay repositorio en GitHub por ahora. El código vive en la carpeta del proyecto (`NOVA/`) con su propio historial de git local, y se entrega como `NOVA-0.1.1.zip`.
- **Escrita desde cero.** No comparte código con NOVORA OS; ATLAS solo sirvió de referencia.
- La especificación base es tu documento **NOVA-WINDOWS.md v1.3**: laptop con RTX 3050 de 4 GB, Windows 10/11, nunca como administrador, política de órdenes en tres niveles, palabra de activación «Oye, NOVA», plática espontánea máximo 3 veces al día, entre otras cosas.

## 2. Cómo está hecha

| Pieza | Qué hace |
|---|---|
| Núcleo (Python) | Conversa, decide qué herramienta usar y recuerda. Solo usa la biblioteca estándar de Python, más dos paquetes opcionales: `trafilatura` (leer páginas web) y `cryptography` (conexión cifrada con el celular). |
| Motor (llama.cpp) | `llama-server` corre en tu PC (solo 127.0.0.1). NOVA lo prende cuando hace falta, lo apaga si no se usa y lo cierra si NOVA se cierra. Modelo: Qwen3 4B Instruct (Q4_K_M, unos 2.5 GB), contexto de 4096 para que quepa en 4 GB de video. |
| Memoria (SQLite) | Recuerdos, perfil tuyo, conversaciones, notas, tareas y documentos, con búsqueda de texto completo. Filtra secretos (tarjetas, contraseñas, códigos, claves de BitLocker) para no guardarlos nunca. Tiene modo privado y «olvida esto». |
| Aprendizaje | Detecta cosas como «me llamo…», «me gusta…», «estoy trabajando en…», «mañana tengo…» y las guarda. |
| Internet | Busca en DuckDuckGo (o SearXNG si lo configuras) y usa Wikipedia de respaldo. Bloquea direcciones privadas y locales para que nada se filtre. Si el modelo responde «no sé», busca solo. |
| Seguridad de órdenes | Cada orden se clasifica en SOLO LECTURA (la hace), CONFIRMAR (te pide un código de 4 cifras) o PROHIBIDO (nunca: formatear, particiones, arranque, BitLocker, etc.). También revisa rutas protegidas, archivos ocultos o del sistema, enlaces raros y archivos de OneDrive que no están en tu disco. |
| Voz | Oído con whisper.cpp (modelo small) y voz con Piper (español de México). Órdenes rápidas por voz: «para», «toma nota», «qué tengo hoy», «desconéctate», etc. |
| Interfaz | Servidor local en `http://127.0.0.1:8190/` protegido con token. En Windows abre en su propia ventana (Edge en modo app). |
| Escritorio (Tauri) | Ventana transparente donde el orbe flota libre sobre el escritorio, deja pasar los clics y se aparta del cursor. Atajos Ctrl+Alt+A (abrir) y Ctrl+Alt+Espacio (hablar), icono en la bandeja. Compila para Windows, pero el `.exe` hay que generarlo en tu PC. |
| Diagnóstico | `nova diagnostico` revisa todo pieza por pieza y guarda un reporte. |
| Pruebas | 196 pruebas automáticas, todas pasan (en Linux). |

## 3. El orbe

Red holográfica de puntos y líneas que flota por la pantalla y esquiva el cursor.

- **Estados:** reposo (azul tenue), activa (azul brillante), procesando (dorado), hablando (púrpura, late con la voz).
- **Colores por tema:** azul general, cian tecnología, púrpura creatividad, dorado análisis, verde investigación, rojo prioridad, rosa apoyo, blanco neutral.

Hay capturas en `docs/capturas/`.

## 4. Conexión con el celular

Lo pediste para tener conexión directa celular–PC:

- Le dices a NOVA «conecta mi celular» y te da una dirección (`https://192.168.x.x:8191/`) y un código de 6 cifras que dura 5 minutos.
- Solo acepta celulares de tu misma red Wi-Fi. La conexión va cifrada con un certificado propio de NOVA.
- Desde el celular puedes escribirle, hablarle y confirmar acciones. Las acciones marcadas como «solo con clic» no se pueden confirmar desde el celular ni por voz.

## 5. Instalación en Windows

Hice un instalador de doble clic (`instalar.bat`, que llama a `instalar.ps1`). Instala Python si falta, crea el entorno en `%LOCALAPPDATA%\NOVA\venv`, baja llama.cpp con CUDA para tu RTX 3050, el modelo (verificando su huella SHA256), el oído y la voz. Después crea el acceso NOVA (Ctrl+Alt+N), corre el diagnóstico y abre NOVA. La guía paso a paso está en `PRUEBA-WINDOWS.md`.

### Intentos en tu PC y qué arreglé

| # | Qué pasó | Causa | Arreglo |
|---|---|---|---|
| 1 | «instalar.ps1 no está firmado digitalmente» | Windows bloquea scripts descargados de internet. | Correr `Get-ChildItem -Recurse \| Unblock-File` y usar `instalar.bat`. Lo agregué a la guía. |
| 2 | «No encontré la variante cuda de llama.cpp» (versión v0.7.0) | llama.cpp marca como «última versión» una que no trae programas para Windows; sus compilaciones con CUDA salen como «preliminares». | El instalador busca la compilación más reciente que sí trae el paquete CUDA. |
| 2b | Decía que no había Python aunque tenías 3.12 | Windows PowerShell 5.1 le quita las comillas a lo que se le pasa a Python. | Cambié la forma de preguntar la versión. |
| 3 | Error en «x64» y en «Motor» en la línea 1 | El archivo suelto que te mandé por el chat llegó con un carácter invisible al inicio. | El instalador ahora va solo en caracteres básicos y siempre te lo mando dentro del zip. |
| 4 | «Se recibió un EOF inesperado» al consultar GitHub | La respuesta de GitHub era muy grande y se cortó. | Pide menos versiones, reintenta 3 veces y usa `curl` de respaldo. |

En el intento 4 ya pasaron bien Python, el entorno de NOVA y la detección de tu tarjeta (RTX 3050 Laptop, controlador 616.92, 4096 MiB).

## 6. Dónde estamos

**Pendiente de ti:** bajar otra vez `NOVA-0.1.1.zip`, reemplazar `instalar.ps1` en `N:\NOVORA_WORKSPACE\NOVA IA\NOVA-0.1.1\NOVA`, correr `Get-ChildItem -Recurse | Unblock-File` y luego `.\instalar.bat`. Me pegas lo que salga.

### Lo que todavía no se ha comprobado

- El instalador completo en tu PC (aquí no hay Windows ni pude ejecutar PowerShell; avanza paso por paso con tus pruebas).
- NOVA con el modelo real: velocidad, si sigue el formato de herramientas y cuánta memoria de video usa.
- La búsqueda en DuckDuckGo en vivo (desde aquí está bloqueada).
- La voz y el oído en tu equipo.
- El orbe flotante de escritorio (Tauri) funcionando de verdad.
- La conexión con el celular en tu red.

## 7. Siguientes pasos

1. Terminar la instalación y mandarme `%LOCALAPPDATA%\NOVA\estado\diagnostico.txt`.
2. Hacer las 9 pruebas dentro de NOVA y la del celular (`PRUEBA-WINDOWS.md`, secciones 4 y 5).
3. Ajustar según lo que salga: modelo, contexto o lo que falle.
4. Compilar en tu PC el orbe flotante de escritorio (`.exe` de Tauri).
5. Después: palabra de activación «Oye, NOVA», pulir la personalidad y la plática espontánea.
