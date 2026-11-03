# Libros de Excel como cálculo

Nightingale compila un libro `.xlsx`/`.xlsm` en un cálculo ejecutable: lee las fórmulas y los valores que guardó Excel, separa **parámetros** (celdas que una fórmula lee directamente), **tablas** (celdas que solo se leen a través de rangos) y **resultados** (fórmulas que ninguna otra lee), y ejecuta cada fórmula con la semántica de Excel. Página **Libros Excel**, API `/api/sheets/*` y herramienta de agente `data_workbook`.

## Qué hace

- **Compilar** (`compile`): ruta (absoluta o relativa a la carpeta de datos, con las reglas de seguridad de ficheros de la familia) o subida desde la página. Las macros nunca se ejecutan.
- **Etiquetas**: el texto más cercano a la izquierda o encima de cada celda, o su nombre definido. Las listas de validación (también las que apuntan a otra hoja, que Excel guarda en una extensión x14) dan las opciones de cada parámetro.
- **Calcular** (`evaluate`): valores nuevos por dirección (`Cotización!B5`), etiqueta o nombre definido. Fechas como número de serie de Excel; en la página, como fecha. `as_of` fija el momento de `HOY()`/`AHORA()`.
- **Trazar** (`trace`): las fórmulas por las que pasa un resultado y los parámetros de los que depende al final.
- **Comprobar** (`check`): recalcula el libro tal como está y compara cada fórmula con el valor que guardó Excel.
- **Verificar** (`verify`): genera casos nuevos con semilla (opciones de validación, booleanos, fechas ±60 días, números ×0,3–2,5), los recalcula con **Excel** (COM, si está instalado) o **LibreOffice**, y compara cada resultado. LibreOffice no es Excel: trata VERDADERO/FALSO como números y algunos códigos de error difieren.
- **Exportar** (`export`): un módulo Python autónomo (solo biblioteca estándar) con `compute({celda o etiqueta: valor})`, ejecutable también desde consola.
- **Fechas**: conserva el sistema1900/1904 del libro en el resumen y lo usa al cargar fechas, calcular fórmulas, mostrar resultados, preparar casos de referencia y exportarPython. Cada cálculo tiene su propio contexto de fechas, también en concurrencia.
- **Texto literal**: un texto que empieza por `=` conserva su tipo; solo el tipo de celda fórmula se compila. Al escribir los casos de referencia se distinguen textos literales y valores de error.
- **Verificación vacía**: cero casos o cero resultados comparables producen `inconclusive` y cero comparaciones. No se arranca el motor de referencia ni se declara acuerdo.

## Lo que no se ejecuta

Se **congela** con el valor que guardó Excel y se lista con el motivo: funciones fuera del subconjunto, volátiles (`DESREF`, `INDIRECTO`, `ALEATORIO`…), referencias estructuradas o a otros libros, operador de intersección, fórmulas matriciales de varias celdas y referencias circulares (no hay cálculo iterativo). Los resultados que dependen de algo congelado salen marcados `uses_frozen` y no entran en la verificación.

Funciones soportadas: aritmética y comparación con las reglas de tipos de Excel; SUMA, PRODUCTO, PROMEDIO, MIN, MAX, CONTAR(A/BLANCO), SUMAPRODUCTO, SI, SI.CONJUNTO, SI.ERROR, SI.ND, Y, O, XO, NO, REDONDEAR(.MAS/.MENOS), TRUNCAR, ENTERO, ABS, SIGNO, RESIDUO, POTENCIA, RAIZ, EXP, LN, LOG, LOG10, MULTIPLO.SUPERIOR/INFERIOR, REDOND.MULT, ELEGIR, COINCIDIR, INDICE, BUSCARV, BUSCARH, BUSCARX (exacta), SUMAR.SI(.CONJUNTO), CONTAR.SI(.CONJUNTO), PROMEDIO.SI(.CONJUNTO), MAX/MIN.SI.CONJUNTO, CONCATENAR, CONCAT, UNIRCADENAS, LARGO, MAYUSC, MINUSC, ESPACIOS, IZQUIERDA, DERECHA, EXTRAE, VALOR, TEXTO (formatos `0`, `0.00`, `#,##0.00`, `0%`), ESBLANCO, ESNUMERO, ESTEXTO, ESLOGICO, ESERROR, ESERR, ESNOD, NOD, FECHA, AÑO, MES, DIA, FECHA.MES, FIN.MES, DIAS, DIASEM, DIA.LAB, DIAS.LAB, HOY, AHORA.

## Evidencia

- Fuzz con fórmulas aleatorias anidadas (blancos, textos, booleanos, errores, búsquedas aproximadas sobre datos desordenados) recalculadas por **Excel**: 3.073 de 3.093 fórmulas iguales (99,35 %). Lo que difiere son combinaciones raras de códigos de error y booleanos en funciones de redondeo. `tests/sheets_fuzz.py`, `tests/run_fuzz_excel.py`.
- Búsqueda aproximada: algoritmo ajustado con 400 observaciones de Excel sobre datos desordenados y mixtos (`tests/match_probe.py`): descendente 200/200, ascendente 198/200.
- `tests/test_sheets.py`: semántica, modelo, congelación, ciclos, exportación ejecutada en un proceso aparte, verificación contra el motor instalado, API y herramienta.
- Correcciones2006:38pruebas positivas, con recálculo independiente porExcel instalado de ambos sistemas de fechas y cambios de fechas/texto literal; cálculo concurrente y exportación autónoma. MCPstdio y navegador comprobados con un libro inventado. Candidato pendiente de revisión e integración.

## Límites

`TEXTO` usa separadores en inglés (el Excel de Luis en español mostraría `1.234,57`). Las funciones de búsqueda sobre datos sin ordenar imitan a Excel pero Excel no documenta ese caso. Sin cálculo iterativo, sin tablas de datos de hipótesis ni matrices dinámicas.

La revisión2 de2006 corrige también el selector de fecha de la página: toma el sistema del libro al mostrar y editar parámetros. Los resúmenes antiguos sin sistema de fechas se recalculan al leer desde el original retenido, sin reescribir su metadata histórica. Prueba de UI1904:2026-11-03 se edita a2026-11-10 y el resultado del día siguiente es2026-11-11.

## Entregar una calculadora a un cliente

**Exportar calculadora** descarga un único archivo Python con el cálculo compilado, un formulario web local, resultados, fuentes y licencias. El destinatario necesita Python 3; no necesita Nightingale, Excel, un LLM ni paquetes adicionales. Ejecuta `python presupuesto_app.py --port 8080` y abre la dirección que aparece. Escucha en `127.0.0.1` y funciona sin conexión a internet.

Conserva números, textos, booleanos, opciones de las listas de validación y fechas/horas. Usa el sistema 1900/1904 del libro. Los errores de Excel se muestran como errores y los resultados congelados se identifican. Permite fijar la fecha de HOY/AHORA y restablecer los parámetros originales. Comparte el cálculo y los límites del módulo Python: no reproduce una hoja de cálculo completa ni ejecuta macros.

API: `GET /api/sheets/{id}/export.app.py`. MCP: `data_workbook` con `action="export"`, `workbook_id` y `format="app"`. Omitir `format`, o usar `"python"`, mantiene la exportación del módulo de cálculo. El archivo guardado por MCP coincide en bytes UTF-8 con la descarga HTTP, también en Windows.

Caso inventado probado: presupuesto de mecanizado con unidades, precio, material, IVA, urgencia y fecha del pedido. Para 24 unidades de acero sin urgencia, entrega 617,83 € y fecha prevista 2026-11-10; la aplicación exportada y el cálculo nativo coinciden. Siete pruebas del exportador cubren ambos sistemas de fechas, tipos, congelados, errores, conversión de entradas JavaScript, API/herramienta y servidor real con `python -I -S`. La capacidad 2011 está pendiente de revisión e integración y depende de la corrección 2006.
