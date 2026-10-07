# Exportar conservando el libro de Excel

Nightingale mantiene la exportación plana habitual a un XLSX de una hoja,
CSV, Parquet o JSON. Para un `.xlsx` importado también puede crear una copia
conservando el libro:

1. Importa el archivo con `data_ingest(kind="file", path=..., name=...)`.
   Cada hoja seleccionada pasa a ser un dataset. El origen guarda una huella
   SHA-256, el inventario de hojas/tablas y las huellas de las partes OOXML.
   Consulta `GET /api/workbooks/{dataset}` o `data_export(mode="inventory")`
   para ver el inventario y comprobar si el origen sigue intacto.
2. Usa el flujo habitual de previsualización, transformación, controles y
   deshacer. Exporta una versión con `data_export(dataset=..., format="xlsx",
   mode="preserve_workbook", version=...)` o `POST /api/export` con esos campos.
   Nightingale obtiene la hoja y la fila de encabezados del registro de
   importación de la versión cero.
3. Se crea una copia en `data/exports/` y un recibo
   `.workbook-export.json` con hashes de origen y salida, dataset/versión,
   identificador de origen, hoja/rango, partes OOXML sin cambios y modificadas,
   avisos y el id del registro de análisis.

Para modificar varias pestañas en esa misma copia, usa `dataset` y `version`
para la primera y añade `workbook_updates`, por ejemplo:

```json
{"dataset":"ventas__Ventas","format":"xlsx","mode":"preserve_workbook","version":2,
 "workbook_updates":[{"dataset":"ventas__Inventario","version":1}]}
```

La API REST y la herramienta MCP `data_export` aceptan el mismo formato. Todas
las actualizaciones deben apuntar a la misma ruta del libro original (sin
distinguir mayúsculas en Windows) y a la misma huella SHA-256. Antes de publicar
la copia se comprueba cada versión frente a su base de versión cero, las
columnas importadas, la hoja, el rango de tabla/filtro y las fórmulas. Se
rechazan destinos de hoja repetidos. Se escribe un solo libro y el recibo enumera
cada conjunto de datos, versión y cantidad de celdas modificadas. Si falla esta
validación, un libro y recibo ya existentes quedan intactos. Esta garantía cubre
la validación y sustitución del archivo del libro; el registro de análisis, el
recibo y el libro no forman una transacción única entre archivos y base de datos.

El modo conservador modifica solo las celdas cuyo valor cambió respecto a la
versión importada y marca el libro para recalcular al abrirlo. Las celdas sin
cambios conservan su XML y tipo originales, incluidos identificadores de texto
como `00123`, fórmulas y texto que parece una fecha. También conserva estilos,
formatos numéricos, tablas, filtros, comentarios, hipervínculos, gráficos,
validaciones y las demás partes OOXML. Mantiene las reglas de validación, pero
Nightingale no comprueba los valores editados contra esas reglas. No calcula
fórmulas ni actualiza la caché de gráficos; los resultados pueden quedar
obsoletos hasta que Excel o LibreOffice recalcule la copia. Nunca se sobrescribe
el original. Las fechas/valores fecha-hora tipados que sí cambian usan la época
1900 o 1904 del libro; los booleanos siguen siendo booleanos, el texto que
empieza por `=` sigue siendo texto y los decimales se escriben como números.

Se rechaza la exportación si cambió el hash del origen, si se intenta modificar
una celda con fórmula, si la tabla/filtro no cubre exactamente el rectángulo
importado, si cambia la cantidad de filas o columnas, si se renombran o eliminan
columnas, si faltan filas originales o si el archivo es `.xlsm`. Esta ruta
conserva rangos de igual tamaño; no amplía tablas ni recoloca objetos. La
exportación plana sigue disponible para generar un archivo de datos nuevo.

Es una ruta de preservación acotada, no un editor general de Excel ni un
sustituto de GridCraft. No afirma soporte de macros, recálculo ni fidelidad
completa de Excel. Consulta [la guía en inglés](WORKBOOK-EXPORT.md).
