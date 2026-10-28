---
name: revisar-codigo
description: Revisar código o un archivo de un proyecto buscando errores, fallas de seguridad y mejoras antes de subirlo.
---

# Revisar código

1. Si no tienes el código, léelo con leer_archivo (o analizar_carpeta si es un proyecto entero).
2. Busca en este orden y solo reporta lo que de verdad encuentres:
   - Errores que hacen que falle o dé un resultado equivocado (nulos, índices, casos vacíos, async sin await).
   - Seguridad: contraseñas o claves en el código, entradas del usuario sin validar, rutas o comandos armados con texto.
   - Cosas que se repiten o se pueden simplificar.
3. Para cada hallazgo di: archivo y línea, qué pasa, y cómo arreglarlo en una o dos líneas de código.
4. Ordena de lo más grave a lo menos grave. Si no encuentras nada importante, dilo claro.
5. No reescribas el archivo completo; si el usuario quiere el arreglo, propón el cambio con escribir_archivo.
