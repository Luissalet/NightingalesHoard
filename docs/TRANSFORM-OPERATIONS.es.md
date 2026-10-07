# Parámetros de las operaciones de transformación

`data_transform` recibe un nombre de operación y un objeto JSON en `params`.
Primero usa la vista previa con `preview=true` (valor predeterminado); aplica
con `preview=false` cuando el resultado sea correcto. `GET /api/transforms`
devuelve este inventario en JSON; añade `?op=replace` para consultar una sola
operación. Desde MCP se puede llamar a `data_transform(op="help",
help_for="replace")` sin dataset, u omitir `help_for` para obtener el catálogo
completo de solo lectura. Esta consulta no crea una versión ni una entrada de
análisis.

| Operación | `params` obligatorios | Campos opcionales y reglas | Ejemplo |
| --- | --- | --- | --- |
| `filter` | `expr` | Un predicado SQL. | `{"expr":"amount > 0"}` |
| `select` | `columns` | Lista de nombres de columnas existentes. | `{"columns":["region","amount"]}` |
| `drop` | `columns` | Lista de columnas existentes; no se pueden eliminar todas. | `{"columns":["temporary_note"]}` |
| `rename` | `mapping` | Objeto de nombres actuales a nombres nuevos. | `{"mapping":{"old_name":"new_name"}}` |
| `cast` | `column`, `to` | `to`: `integer`, `double`, `varchar`, `text`, `date`, `timestamp`, `boolean`, `decimal`. Opcionales: `spanish_number`, `date_format`. | `{"column":"amount_text","to":"decimal"}` |
| `fill_null` | `column` | `strategy` vale `value` por defecto; entonces también hace falta `value`. Estrategias: `value`, `mean`, `median`, `mode`, `forward`. | `{"column":"region","strategy":"mode"}` |
| `drop_duplicates` | Ninguno | `subset` es opcional; si falta, se usan todas las columnas. | `{"subset":["id"]}` |
| `derive` | `name`, `expr` | `expr` es una expresión SQL. Si el nombre ya existe, se sustituye. | `{"name":"total","expr":"quantity * price"}` |
| `split_column` | `column`, `into` | `into` es una lista de nombres de salida; `delimiter` vale coma por defecto. | `{"column":"place","delimiter":",","into":["city","country"]}` |
| `text` | `column`, `op` | `op`: `trim`, `upper`, `lower`, `title`, `strip_accents`, `collapse_spaces`. `new_column` es opcional. `title` convierte en mayúscula la primera letra Unicode de cada secuencia continua de letras/marcas, pasa el resto a minúsculas y conserva los delimitadores exactos (espacios, apóstrofos y guiones incluidos); los nulos siguen siendo nulos. | `{"column":"name","op":"trim"}` |
| `replace` | `column`, `pattern` | `replacement` vale `""` y `regex` vale `false` por defecto. `pattern` es el texto o regex que se busca; no se llama `find` ni `old_value`. | `{"column":"status","pattern":"old","replacement":"new"}` |
| `bin` | `column` | `new_column` es opcional; `edges` y `labels` opcionales definen rangos explícitos; si no, `bins` vale 5. | `{"column":"score","bins":4}` |
| `date_parts` | `column`, `parts` | Partes: `year`, `month`, `day`, `dow`, `quarter`, `week`, `hour`, `minute`. | `{"column":"created_at","parts":["year","month"]}` |
| `group` | `group_by`, `aggregations` | `group_by` es `string[]`. `aggregations` es una lista de `{fn?, column?, alias?}`; `fn` vale `sum` por defecto y `column` es obligatorio excepto en `count`. Funciones: `sum`, `avg`, `min`, `max`, `count`, `count_distinct` exacto (ignora nulos), `median`, `stddev`. | `{"group_by":["region"],"aggregations":[{"column":"amount","fn":"sum","alias":"total"}]}` |
| `pivot` | `on`, `value` | `fn` es opcional y vale `sum`; `group_by` es una lista opcional. | `{"on":"quarter","value":"amount","fn":"sum"}` |
| `unpivot` | `on` | Lista de columnas; los nombres de salida son `key` y `value` por defecto. Se pueden cambiar con `name_col` y `value_col`. | `{"on":["q1","q2"]}` |
| `join` | `other_dataset`, `on` | `on` es `array<{left: string, right: string}>`. `how` vale `left` por defecto; opciones: `inner`, `left`, `right`, `full`. | `{"other_dataset":"regions","on":[{"left":"region_id","right":"id"}],"how":"left"}` |
| `union` | `other_dataset` | `distinct` vale `false` por defecto; las columnas se emparejan por nombre. | `{"other_dataset":"archive"}` |
| `sort` | `by` | `by` es `array<string | {column: string, desc?: boolean}>`; `desc` vale `false` por defecto. | `{"by":[{"column":"amount","desc":true}]}` |
| `sample` | `n` o `frac` | `seed` es opcional y vale 42. `n` indica filas; `frac` se pasa como fracción de muestreo Bernoulli. | `{"n":100,"seed":42}` |
| `window` | `fn`, `order_by` | `order_by` y `partition_by` son `string[]`. Funciones: `lag`, `lead`, `rolling_mean`, `rolling_sum`, `row_number`, `rank`. Las cuatro primeras también necesitan `column`; `offset` vale 1, `window_size` vale 3 y `partition_by` vale `[]` por defecto; `new_column` es opcional. | `{"fn":"lag","column":"amount","order_by":["date"]}` |
| `sql` | `sql` | Una única sentencia de solo lectura; `__prev__` representa la tabla de la versión actual. | `{"sql":"SELECT * FROM __prev__ WHERE amount > 0"}` |

El inventario describe los constructores implementados en
`nightingale/workbench/steps.py`; no sustituye las comprobaciones según el
dataset, como verificar que exista una columna. Las expresiones SQL pasan por
el control de seguridad de Nightingale. Las operaciones desconocidas y los
campos obligatorios ausentes producen errores claros antes de crear una versión.
Consulta la [referencia en inglés](TRANSFORM-OPERATIONS.md).
