---
version: 1
slug: "nightingale-sheets-web-app-py"
primary_target: "nightingale/sheets/web_app.py"
related_targets: ["client/src/pages/WorkbooksPage.jsx"]
---

# Exportación de calculadora de un libro

Modo: Operate. Extensión precisa de Libros Excel; hereda la identidad de Nightingale. Un cliente de Automo modifica parámetros de un encargo y obtiene resultados sin necesitar un Hoard. Un único archivo Python, navegador local, sin recursos de red.

## Direction contract

THESIS: convertir los parámetros y resultados del libro en un formulario legible; no recrear una hoja de cálculo ni una portada.

OWN-WORLD: paleta rosa oscura, Cinzel y Source Sans3 existentes; controles familiares, cifras tabulares y separación por líneas.

STORY: leer el nombre del cálculo, editar valores y calcular; conservar tipos, opciones, fechas, errores y advertencias de valores congelados.

FIRST VIEWPORT: encabezado de30px; formulario a la izquierda y tabla de resultados a la derecha. En móvil, formulario seguido de resultados. Calcular junto al final de los campos.

FORM: composición Parámetros/Resultados existente; extensión especificada, sin sorteo de conceptos ni cambio de identidad.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

Documentación de la extensión construida: [DESIGN.md](calculator-export/DESIGN.md) y [design-system.json](calculator-export/design-system.json). Alcance exclusivo del exportador; el diseño global permanece en `DESIGN.md` raíz.
