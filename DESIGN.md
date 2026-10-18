---
name: "Nightingale’s Hoard"
description: "Sistema visual construido del panel Funciones, con las interfaces nativas conservadas."
colors:
  deep: "#1c1416"
  surface: "#241b1d"
  elevated: "#2f2527"
  border: "#433538"
  border-hover: "#6a585b"
  text: "#ece1e4"
  text-muted: "#beb4b6"
  text-dim: "#a69b9d"
  accent: "#de8396"
  accent-strong: "#e7a3b1"
  accent-ink: "#1b1315"
  accent-soft: "rgba(222, 131, 150, 0.14)"
  danger: "#ee8b80"
typography:
  headline:
    fontFamily: "'Cinzel', 'Iowan Old Style', Charter, Georgia, 'Liberation Serif', 'Noto Serif', 'Times New Roman', serif"
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1.15
    letterSpacing: "0.005em"
  brand:
    fontFamily: "'Cinzel', 'Iowan Old Style', Charter, Georgia, 'Liberation Serif', 'Noto Serif', 'Times New Roman', serif"
    fontSize: "16px"
    fontWeight: 700
    lineHeight: 1.5
  title:
    fontFamily: "'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans', 'Liberation Sans', Arial, sans-serif"
    fontSize: "19px"
    fontWeight: 600
  body:
    fontFamily: "'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans', 'Liberation Sans', Arial, sans-serif"
    fontSize: "14px"
    lineHeight: 1.6
  label:
    fontFamily: "'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans', 'Liberation Sans', Arial, sans-serif"
    fontSize: "13px"
  detail:
    fontFamily: "'Source Sans 3', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans', 'Liberation Sans', Arial, sans-serif"
    fontSize: "12px"
rounded:
  control: "6px"
  icon: "8px"
  switch: "14px"
spacing:
  control-gap: "10px"
  row-block: "22px"
  page-gutter: "48px"
  page-gutter-mobile: "22px"
components:
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "10px 16px"
  navigation-active:
    backgroundColor: "{colors.elevated}"
    textColor: "{colors.accent}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  module-row:
    textColor: "{colors.text}"
    padding: "22px 0"
  switch-track:
    backgroundColor: "{colors.border}"
    rounded: "{rounded.switch}"
    width: "42px"
    height: "24px"
---
# Design System: Nightingale’s Hoard

## Overview

**Creative North Star: "La familia Hoard existente"**

La identidad vigente de Nightingale’s Hoard pertenece a la familia Hoard: fondos oscuros teñidos, acento propio, icono existente, títulos Cinzel y texto Source Sans 3. El panel Funciones prolonga esa identidad sin sustituir la presentación de las herramientas nativas. Esta actualización combina el panel construido con los patrones anteriores que siguen aplicando en sus pantallas; los tokens normativos de cabecera describen el panel Funciones, no una recoloración de todas las vistas.

La interfaz operativa prioriza el nombre de la función, una explicación breve y controles con estado escrito. La profundidad procede del contraste entre fondo, lateral y selección. La marca de Faustus conserva su identidad familiar independiente; no se usa para recolorear el producto.

**Key Characteristics:**

- Icono propio conservado y navegación familiar.
- Jerarquía tipográfica serifa/sans, con controles y estados legibles.
- Filas separadas por bordes y profundidad tonal contenida.

## Colors

### Primary

**Rosa de datos**: `accent` identifica enlaces, marca y selección; `accent-strong` responde al hover. `accent-ink` es el texto o pulgar sobre el acento y `accent-soft` el fondo de selección de texto. Sus valores normativos están en la cabecera, extraídos de `hoard-theme.css`.

### Neutral

`deep` es el lienzo; `surface`, el lateral y los campos; `elevated`, la navegación seleccionada y las respuestas de hover. `border` y `border-hover` separan y responden a interacción. `text`, `text-muted` y `text-dim` ordenan contenido, ayudas y procedencia. `danger` comunica errores junto a texto explícito.

**The Product Accent Rule.** El acento propio identifica la aplicación, la navegación activa y el estado encendido; los párrafos permanecen en los tonos de texto.

## Typography

**Display Font:** Cinzel, con los respaldos serif del tema familiar.
**Body Font:** Source Sans 3, con los respaldos sans del tema familiar. Ambas fuentes principales se alojan localmente.

La serifa identifica marca y encabezado de página; la sans organiza explicación, navegación y controles. La escala del panel parte de 15px en la raíz. El encabezado usa la variante `headline`; la marca, `brand`; los nombres de módulo, `title`; las descripciones, `body`; estados y acciones, `label`; las rutas desplegables, `detail`. El cuerpo de los párrafos mantiene interlínea 1.6 y anchura máxima de 72ch. En móvil, encabezado 27px y marca 15px; la descripción de módulo baja a 13px bajo 650px.

## Layout

El escritorio combina un lateral fijo en el flujo (220px), pegajoso al borde superior y de una altura de viewport, con contenido flexible `minmax(0, 1fr)`. El contenido tiene anchura máxima de 1140px y relleno 48px/48px/44px. Bajo 1050px usa 38px/28px. Las filas de módulo distribuyen texto flexible y controles a la derecha; los separadores atraviesan el ancho de contenido.

Bajo 720px el lateral pasa a cabecera horizontal estática, oculta su navegación vertical y conserva marca y enlace al Hub. El contenido usa 30px/22px. Bajo 650px la cabecera del contenido apila el botón, la procedencia pasa bajo el nombre y los controles se ordenan verticalmente. El resto continúa por desplazamiento normal; las rutas largas admiten ruptura de línea. Los controles de interruptor tienen altura mínima de 44px.

## Elevation & Depth

El panel es plano, con superficies tonales, bordes finos y sin sombras en filas ni pulgar del interruptor. El foco de teclado usa un contorno de acento de 3px separado 4px; no depende de profundidad. Los componentes nativos conservan sus excepciones funcionales, como avisos flotantes. La transición de pulgar es breve (0.16s ease-out) y desaparece con `prefers-reduced-motion: reduce`.

## Shapes

Controles y navegación tienen esquinas suavizadas (`control`); el icono propio conserva su archivo y marco (`icon`). El interruptor es una pista de 42×24px con pulgar circular de 18px y desplazamiento de 18px. Bordes de separación de 1px. Los iconos de navegación son SVG de línea; el icono de producto es el raster existente, sin sustituirlo por un glifo.

## Components

### Buttons

Botón secundario transparente con texto principal, borde tonal y relleno 10px/16px. Hover usa la superficie elevada y el borde de hover. El estado deshabilitado reduce opacidad y mantiene su etiqueta. Los enlaces de apertura usan el acento; deshabilitados pasan al tono atenuado y dejan de recibir clics.

### Navigation

Entradas de 14px con icono SVG de 18px, relleno 10px/12px y esquinas de control. La activa usa superficie elevada, acento y peso 600; hover usa texto principal. La marca usa el icono existente de 42px, reducido a 36px en móvil. El enlace al Hub se sitúa al pie del lateral en escritorio y en cabecera en móvil.

### Module rows and switches

Cada función ocupa una fila con nombre, procedencia, descripción, ubicación desplegable y controles. Los títulos usan 19px/600. El interruptor mantiene texto de estado y `aria-checked`; apagado usa borde tonal y pulgar de texto, encendido pista de acento y pulgar de tinta de acento. No se diferencia el estado sólo por el color.

### Data location and status

La ubicación usa `details` nativo y código de 12px con ruptura de rutas. El estado global usa texto de 14px y un indicador circular pequeño. Errores usan tono de peligro y semántica de alerta, conservando el mensaje.

### Taller nativo de datos y cálculo

La vista de datos conserva su tabla, recetas reversibles, gráficos, paneles y laboratorio de modelos. Los números comparados en columnas usan cifras tabulares. Los gráficos mantienen la rampa rosa y el oro como categoría adicional; el lenguaje de números usa la configuración de idioma de la aplicación. La interfaz de cálculo conserva la entrada explícita, resultados exactos y citas. Las variantes claras/oscuras del taller nativo tienen ámbito propio: su antiguo papel cálido y pila Segoe UI no definen el nuevo panel Funciones.

Se conservan a continuación las decisiones técnicas anteriores que explican esos patrones de datos y gráficos; no añaden tokens al panel:

#### Why DuckDB, and why one connection

Laplace's Hoard multiplexes read-only and read-write DuckDB connections because it mostly answers
queries against data it doesn't touch. Nightingale's entire job is mutation, so that split adds
complexity without buying anything: a single persistent connection, guarded by one `RLock`, is
simpler and just as safe for a single-user desktop app.

Each transform step doesn't overwrite data — it **materializes a new table** (`ds_{id}_v{n}`) and
moves a friendly view to point at it. That's what makes undo an O(1) pointer move, redo possible
after an undo (until a new step branches and prunes the redone-away versions), and a full recipe
replayable against refreshed source data. The trade-off is disk space: a long, wide recipe on a
huge dataset keeps every intermediate version until the dataset is deleted. For the sizes this app
targets (a workbench dataset, not a warehouse), that trade is worth the correctness and the undo
story it buys.

#### Why charts are two artifacts

Every chart is one `ChartSpec` (kind, x, y, aggregate, filter, color) compiled to **one aggregating
SQL query** — never a whole table pulled into Python to summarize client-side. From that one query
result, two renderings are produced on request: a **Vega-Lite spec** for the interactive client
(zoom, tooltip, resize) via `vega-embed`, and a **PNG** via `matplotlib` (`Agg` backend, so it never
tries to open a window — this matters most on Windows, where a GUI backend can crash a headless
process) for the MCP agent and for anything that needs a static image (a saved dashboard export).
A date/timestamp axis is encoded as Vega-Lite's `temporal` type rather than `nominal`, so the
library picks readable tick spacing instead of drawing one label per distinct date.

Both renderings share one theme (`VEGA_CONFIG` in `charts.py`, and the matching matplotlib
constants): the rose ramp plus a gold accent (`#e0a048`) as a categorical palette, so a chart with
several series reads as clearly distinct colors rather than a blur of near-identical rose tones. A
number field carries a d3-format token (thousands-grouped, at most 2 decimals); the *characters*
used for grouping and the decimal mark come from the UI's own language, applied client-side via
vega-embed's `formatLocale` for the interactive chart, and by a small locale-swapping formatter for
the matplotlib PNG — both read the same setting Settings uses, not the browser's default locale.

#### Why the model layer excludes "id-like" columns by default

Training with every column as a feature is the obvious first thing to try, but a raw order id or
an unaggregated date column one-hot-encodes into hundreds of near-unique dummy columns, which can
turn a two-second fit into one that never converges. When features aren't given explicitly, any
non-numeric column whose distinct-value count is both above 30 and above 5% of the row count is
left out automatically, and reported back (`excluded_id_like_columns`) so it's never a silent
decision. Passing an explicit feature list always overrides this and is trusted as-is.

#### Why forecasting aggregates duplicate timestamps

Row-level data — one row per transaction, per event — is the normal shape of a dataset in this
workbench, and it usually has several rows per date. A time series needs one point per period, so
`data_forecast` sums duplicate timestamps into one point per date before doing anything else.
Skipping this step means the median gap between (sorted, but often same-day) rows collapses to
zero, and the forecast silently repeats the same date forever — a real bug caught by walking the
UI with a real messy sales export, now covered by a regression test.

#### Recipe as an artifact, not a black box

Every applied step is stored as `{op, params}` plus the exact SQL it compiled to. That list is
what "undo" walks, what "export as SQL" prints as a runnable script, and what "refresh" replays
against freshly re-ingested source data. If a step no longer makes sense against reshaped data
(a column was renamed by an earlier step, say), replay stops there and keeps whatever succeeded,
rather than raising past the point a user could still act on the dataset.

## Do's and Don'ts

### Do:

- **Do** conservar el icono existente y las familias locales Cinzel y Source Sans 3.
- **Do** acompañar cada interruptor con su estado escrito y un nombre accesible.
- **Do** mantener el borde de fila y el foco visibles en el tema oscuro.
- **Do** respetar los patrones nativos conservados al extender sus pantallas.

### Don't:

- **Don’t** sustituir la identidad del Hoard por un tema genérico.
- **Don’t** usar el acento para párrafos largos.
- **Don’t** convertir las filas de Funciones en tarjetas decorativas elevadas.
