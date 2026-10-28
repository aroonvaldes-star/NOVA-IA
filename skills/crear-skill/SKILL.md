---
name: crear-skill
description: Crear o escribir una skill nueva para NOVA (un SKILL.md) cuando el usuario quiere enseñarte a hacer algo de cierta forma siempre.
---

# Crear una skill

1. Pregunta lo que falte, en una sola pregunta: qué debe hacer la skill y cuándo se usa.
2. Elige un nombre corto en minúsculas con guiones (por ejemplo `resumen-reunion`).
3. Escribe el SKILL.md así, corto (menos de 2,500 caracteres):

```
---
name: el-nombre
description: Qué hace y cuándo usarla, con las palabras que diría el usuario al pedirlo.
---

# Título

1. Paso concreto.
2. Paso concreto.
```

4. Propón guardarlo con escribir_archivo en `~/Documents/NOVA skills/el-nombre/SKILL.md`.
5. Dile al usuario que, para activarla, mueva la carpeta `el-nombre` a la carpeta `skills` de NOVA
   o a `%APPDATA%\NOVA\skills`. Tú no puedes activarla sola: así nadie más puede meterte instrucciones.
