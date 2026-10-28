# Skills de NOVA

Cada carpeta de aquí es una **skill**: instrucciones que le enseñan a NOVA a hacer algo,
en el mismo formato que Agent Skills (una carpeta con un `SKILL.md`).

```
mi-skill/
  SKILL.md
  plantilla.md     (opcional: archivos que la skill menciona)
```

El `SKILL.md` empieza con dos datos y luego las instrucciones:

```markdown
---
name: mi-skill
description: Qué hace y cuándo usarla, con las palabras que tú dirías al pedirlo.
---

# Mi skill

1. Primer paso…
```

## Dónde ponerlas

- **Aquí** (`skills/` dentro de la carpeta de NOVA). `actualizar.bat` no borra las carpetas que agregues tú,
  pero si un día llega una skill incluida con el mismo nombre, la tuya se reemplaza.
- **`%APPDATA%\NOVA\skills`**: nada la toca nunca; si una skill se llama igual que una de aquí, gana esa.
- Las carpetas de `skills_carpetas` en `config.json` (por defecto `~/.claude/skills`, por si ya tienes skills de Claude Code).

NOVA las ve sin reiniciar. Dile «qué skills tienes» para ver la lista, o «usa la skill nombre …» para pedir una.
Si tu mensaje se parece mucho a la descripción de una skill, NOVA la usa sola.

## Las skills de Anthropic (github.com/anthropics/skills)

Dile a NOVA **«baja las skills de anthropic»** (o en la terminal `nova skills bajar`). Las guarda en
`%LOCALAPPDATA%\NOVA\skills-anthropic`, cada una con una descripción en español para que NOVA las
elija bien. Para tener la versión más nueva, pídeselo otra vez: reemplaza esa carpeta.

- **No se instalan** `docx`, `pdf`, `pptx` y `xlsx`: su licencia solo permite usarlas dentro de los
  servicios de Anthropic, no con otro modelo.
- Están en inglés y varias son largas: NOVA lee los primeros 2,500 caracteres y abre lo demás si lo necesita.
- Algunas traen scripts de Python que piden paquetes (por ejemplo Playwright en `webapp-testing`).
  NOVA nunca los corre sola; te propone la orden y tú confirmas.
- Algunas están pensadas para Claude (`claude-api`, `academy-guide`); con el modelo local sirven de referencia.

## Consejos para el modelo local

El modelo de tu laptop es chico, así que:

- Escribe la descripción en español y con las palabras que usarías al pedirlo.
- Mantén el `SKILL.md` corto (menos de 2,500 caracteres entra completo; lo demás se recorta).
  Lo largo va en archivos aparte que NOVA abre solo si los necesita.
- Pasos numerados y concretos funcionan mejor que explicaciones largas.

Las skills son instrucciones que NOVA sigue, como `personalidad.md`. Instala solo las que entiendas.
Los scripts que traiga una skill nunca se ejecutan solos: pasan por la confirmación de siempre.
