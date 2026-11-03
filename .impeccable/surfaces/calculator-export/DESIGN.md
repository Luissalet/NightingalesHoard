---
name: "Nightingale · calculadora exportada"
description: "Extensión visual del formulario y los resultados del exportador autónomo de libros."
colors:
  bg: "#1c1416"
  surface: "#241b1d"
  line: "#433538"
  control-line: "#8b7379"
  text: "#ece1e4"
  muted: "#beb4b6"
  accent: "#de8396"
  accent-hover: "#e7a3b1"
  accent-ink: "#1b1315"
  secondary-hover: "#2f2527"
  selection: "#de83964d"
  danger: "#ee8b80"
typography:
  headline:
    fontFamily: "Cinzel, serif"
    fontSize: "30px"
    fontWeight: 600
    lineHeight: 1.2
  headline-mobile:
    fontFamily: "Cinzel, serif"
    fontSize: "26px"
    fontWeight: 600
    lineHeight: 1.2
  title:
    fontFamily: "'Source Sans 3', sans-serif"
    fontSize: "20px"
    fontWeight: 700
    lineHeight: 1.55
  body:
    fontFamily: "'Source Sans 3', sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.55
  label:
    fontFamily: "'Source Sans 3', sans-serif"
    fontSize: "16px"
    fontWeight: 600
    lineHeight: 1.55
  help:
    fontFamily: "'Source Sans 3', sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.55
  table-label:
    fontFamily: "'Source Sans 3', sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.55
  result:
    fontFamily: "'Source Sans 3', sans-serif"
    fontSize: "19px"
    fontWeight: 400
    lineHeight: 1.55
  detail:
    fontFamily: "'Source Sans 3', sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  control: "6px"
spacing:
  label-gap: "6px"
  action-gap: "12px"
  field-gap: "22px"
  actions-top: "28px"
  column-gap: "48px"
  mobile-column-gap: "36px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-ink}"
    rounded: "{rounded.control}"
    padding: "9px 18px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "9px 18px"
  button-secondary-hover:
    backgroundColor: "{colors.secondary-hover}"
  field:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
    width: "100%"
  checkbox:
    width: "20px"
    height: "20px"
  result-table:
    textColor: "{colors.text}"
    width: "100%"
---

# Design System: Nightingale · calculadora exportada

## Overview

**Creative North Star: "La familia Hoard existente"**

Esta documentación describe únicamente el HTML que genera `nightingale/sheets/web_app.py`. Prolonga el mundo visual definido en el `DESIGN.md` raíz: fondo oscuro teñido, rosa propio de Nightingale, títulos Cinzel y texto Source Sans 3. Los tokens de esta cabecera corresponden al exportador; no sustituyen los del panel Funciones ni modifican otras vistas.

La superficie opera como una calculadora local: nombre del libro, parámetros editables y resultados en tabla. El modo Operate y la composición pertenecen al [contrato de superficie](../nightingale-sheets-web-app-py.md). El archivo Python exportado incluye cálculo, HTML, fuentes y licencias; no necesita descargar recursos. La identidad se expresa mediante tipografía y color, sin añadir logotipo ni imágenes.

**Key Characteristics:**

- Campos nativos con etiquetas visibles y tipos conservados.
- Parámetros y resultados separados por una línea y una jerarquía clara.
- Resultados tabulares, estados escritos y errores anunciados.
- Fuentes integradas y comportamiento adaptable a escritorio y móvil.

## Colors

### Primary

El rosa `accent` identifica Calcular, el foco, el cursor de texto y la casilla seleccionada. `accent-hover` responde al puntero sobre Calcular; `accent-ink` mantiene la lectura de su etiqueta. `selection` señala la selección de texto.

### Neutral

`bg` es el lienzo y `surface` rellena campos. `line` separa columnas, filas y pie, además de perfilar Restablecer. `control-line` delimita entradas y selectores; es una adaptación propia de esta superficie para hacer reconocibles los controles. Su contraste calculado es aproximadamente 4,16:1 contra `bg` y 3,86:1 contra `surface`.

`text` lleva etiquetas y resultados; `muted`, ayuda, cabeceras de tabla y licencias. `secondary-hover` responde al puntero sobre Restablecer. `danger` acompaña errores explícitos del cálculo.

**The Control Boundary Rule.** Los campos usan `control-line`; los separadores de contenido usan `line`.

## Typography

**Display Font:** Cinzel, con respaldo serif.

**Body Font:** Source Sans 3, con respaldo sans-serif.

El título del libro usa `headline`, se equilibra entre líneas y admite ruptura de palabras largas. En móvil pasa a `headline-mobile`. Los títulos de sección usan `title`; campos y acciones parten de `body`, con peso `label` para etiquetas y Calcular. Las ayudas usan `help`; la tabla distingue `table-label`, cuerpo y `result`; los avisos de valores guardados y las licencias usan `detail`.

La tabla aplica cifras tabulares. Los números se presentan con formato `es-ES` y hasta doce cifras significativas; fechas y horas mantienen su representación específica. Los párrafos admiten hasta 72ch y el título hasta 32ch.

Cinzel se incorpora como TTF y Source Sans 3 como WOFF2 mediante URL de datos. Ambas declaraciones admiten pesos 100–900 y `font-display: swap`. Las licencias OFL acompañan al archivo autónomo y se consultan desde el enlace del pie.

## Layout

El contenido se centra con anchura máxima de 1140px y relleno de 48px arriba y 32px en los laterales y abajo. El encabezado deja 38px antes de las secciones. La rejilla tiene una columna de parámetros entre 260px y 380px, otra flexible y una separación `column-gap`. Los resultados añaden borde izquierdo y 40px de separación interior.

A anchuras de 720px o menos, el contenido pasa a una sola columna, con parámetros primero y resultados después. El relleno es 28px vertical y 22px horizontal; la separación entre secciones usa `mobile-column-gap`. La línea de resultados pasa al borde superior, con 28px de relleno encima, y el encabezado deja 30px. El resto continúa por desplazamiento normal.

Las acciones permanecen al final del formulario, permiten salto de línea y conservan su separación. Entradas, selectores y botones tienen altura mínima de 44px; la casilla de 20px se incluye en una etiqueta interactiva de al menos 44px de alto. La tabla ocupa su columna, admite contenido largo y alinea los valores a la derecha.

## Elevation & Depth

La interfaz es plana y no utiliza sombras. Campo, lienzo y separadores construyen la profundidad mediante tono y borde. El foco visible aplica un contorno de acento de 3px, separado 3px del elemento. Los botones transicionan sólo el fondo durante 0,15s con `ease-out`; con `prefers-reduced-motion: reduce` la transición desaparece.

## Shapes

Campos y botones comparten el radio `control`. Los separadores y bordes de control tienen 1px. Se mantienen los selectores de fecha, hora y opciones del navegador, junto a su casilla nativa. No hay tarjetas elevadas, ilustraciones ni un nuevo sistema de iconos.

## Components

### Buttons

Calcular es la acción primaria; Restablecer es transparente y recupera los valores originales antes de volver a calcular. Ambos comparten tipografía, radio y altura mínima. Durante cada petición los dos botones quedan deshabilitados con opacidad 0,6 y cursor de espera; Calcular muestra «Calculando…». Sólo la petición vigente puede restaurar sus estados.

### Inputs / Fields

Cada parámetro tiene una etiqueta asociada a su control. Los tipos son número, texto, lista de opciones, fecha, hora, fecha y hora o casilla, según el libro. Las etiquetas de casilla incluyen control y texto en una fila. La fecha opcional del cálculo aparece sólo cuando el modelo la requiere; un libro sin parámetros presenta una explicación escrita.

### Results and calculation status

La tabla tiene cabeceras semánticas Resultado y Valor, separadores horizontales y dos columnas. La primera ocupa el 60%; la segunda alinea valores a la derecha y deja 20px de separación. Las celdas tienen 14px de relleno vertical. Cada resultado dependiente de celdas congeladas incluye «Usa valores guardados» y se conserva el aviso general del libro.

Al iniciar una petición se vacían las filas anteriores y se anuncia «Calculando…» mediante una región de estado de lectura cortés. El contador de peticiones descarta respuestas tardías. Un fallo escribe el mensaje y la acción de recuperación en una alerta; el estado indica que no hay resultados actualizados. Un resultado individual con error conserva su texto y usa `danger`. La carga inicial calcula los valores originales; una respuesta sin filas informa de que el libro no contiene resultados de cálculo.

**The Current Result Rule.** Sólo la petición vigente puede publicar resultados, errores y estado; al recalcular no se conservan cifras anteriores como si siguieran actualizadas.

### Licences

El pie se separa mediante borde superior, 44px de margen y 18px de relleno. El enlace subrayado abre las licencias del cálculo y las fuentes en una pestaña nueva. Es contenido local del mismo exportador.

## Do's and Don'ts

### Do:

- **Do** conservar las familias integradas, sus licencias y los controles nativos.
- **Do** mantener el borde específico de campo, el foco visible y los estados escritos.
- **Do** conservar la disposición de parámetros seguida de resultados en móvil.
- **Do** vaciar resultados anteriores al recalcular y descartar respuestas tardías.

### Don't:

- **Don't** aplicar estos tokens a todo Nightingale ni sustituir su `DESIGN.md` raíz.
- **Don't** añadir un logotipo, raster o dependencia de red a esta calculadora autónoma.
- **Don't** comunicar error o procedencia de valores guardados sólo mediante color.
