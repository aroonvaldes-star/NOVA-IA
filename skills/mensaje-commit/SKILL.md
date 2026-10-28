---
name: mensaje-commit
description: Escribir el mensaje de commit de git a partir de los cambios del proyecto.
---

# Mensaje de commit

1. Mira los cambios con la herramienta orden: `git status` y luego `git diff` (o `git diff --staged` si ya hay algo agregado),
   con la carpeta del proyecto.
2. Escribe el mensaje:
   - Primera línea: qué cambia, en español, en imperativo y en menos de 60 caracteres (ej. «Agrega login con Google»).
   - Línea en blanco y, si hace falta, 1 a 3 líneas con el porqué.
3. Muestra el mensaje listo para copiar. Si el usuario lo pide, propón `git commit -m "…"` con la herramienta orden
   (necesita su confirmación).
