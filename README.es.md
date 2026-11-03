<img src="app-icon.png" width="96" alt="">

# Nightingale's Hoard

El editor compartido de **bin** en Datos → Receta y Lab → Canalización acepta
**Límites de intervalos** y **Etiquetas** opcionales en JSON. Los límites no vacíos
sustituyen el número de intervalos y crean tramos nativos `[inferior, superior)`;
el último extremo superior, los valores fuera de los tramos y NULL quedan sin
categoría. Las etiquetas que falten usan el texto del tramo; una etiqueta vacía
explícita queda vacía. Límites vacíos o `[]` conservan el modo actual por número
de intervalos. Editar un paso guardado conserva ambos arrays, incluidos cero,
comas, comillas y Unicode. Vista previa no crea versiones: Receta no añade análisis,
mientras Canalización registra su ejecución de prueba nativa. Aplicar usa el
comportamiento nativo de versiones y receta. La previa de Receta muestra hasta
ocho filas. Sin cálculo frontend, nueva validación, API ni herramienta
MCP; el catálogo combinado sigue en43.

En Conjuntos de datos, **Frecuencias por columna** recorre los valores de una
columna de la versión guardada mostrada, con recuentos exactos y porcentajes sobre
todas sus filas. Distingue NULL y cadena vacía y conserva el texto completo.
Cargar más lee la siguiente página nativa; un fallo conserva las filas anteriores
para Reintentar. Cambiar columna/versión o cerrar retira resultados incompatibles.
Consultar no crea versiones, entradas de análisis ni archivos. La API
`GET /api/datasets/{name}/frequencies` y `data_profile(mode="frequencies",
column=..., version=..., offset=..., limit=...)` comparten el cálculo. Sólo este
modo nuevo devuelve páginas completas para que su offset no omita grupos por
recortes; los otros modos conservan su salida. El catálogo combinado sigue en43.
Los valores usan texto VARCHAR de DuckDB, no bytes binarios originales; la UI
redondea porcentajes. El contexto normal de Faustus recibe consulta y metadatos
de la página aplicada cuando la sección está abierta.

El editor compartido del paso **window** en Datos y Lab → Pipeline permite elegir
**Desplazamiento (filas)** para `lag`/`lead` y **Tamaño de ventana** para sumas/medias
móviles. Vacío conserva los valores nativos 1/3; los explícitos, incluido cero,
se conservan al editar un paso guardado o cambiar su nombre de salida. Los campos
se muestran según la función sin descartar ninguno de los dos parámetros.
Vista previa/aplicar usan el motor `data_transform` y API/MCP43 existentes, sin
cálculo frontend. Las ventanas incluyen la fila actual, empiezan parcialmente en
cada partición y cuentan filas, no días de calendario. Las claves de orden todas
empatadas no determinan posiciones estables. Se conservan NULL/errores nativos y
el comportamiento de versiones/receta.

Datos → Receta permite elegir el otro conjunto y añadir pares de columnas para una unión; el mismo editor funciona en Canalización. El JSON manual se conserva, incluidos nombres con coma, comillas o Unicode y nombres de pasos anteriores. Los tipos nativos siguen siendo left, inner, right y full. «Comprobar coincidencias» cuenta filas de la versión actual izquierda que encuentran al menos una coincidencia en la versión actual derecha. No crea versiones ni entradas de análisis; los duplicados derechos pueden multiplicar las filas de la unión, que se consultan con Vista previa. La cobertura no representa nodos pendientes de Canalización. API existente y nueva herramienta MCP `data_join_preview` comparten el cálculo nativo; el catálogo combinado tiene 43 herramientas (21 Datos, 20 Cálculo y dos controles).

Gráficos → **Descargar PNG** obtiene la gráfica guardada seleccionada mediante el
renderizador existente `GET /api/charts/{id}?image=true&lang=es`. Cada tarjeta
identifica su gráfica, dataset e idioma solicitado; un error se puede reintentar
sin cambiar el borrador del constructor. «Descarga iniciada» indica que se entregó
el archivo al navegador, no que se haya confirmado su guardado. El PNG consulta la
versión actual del dataset al pedirlo, respeta los límites de consulta nativos y
puede diferir del visor interactivo Vega. No es una captura histórica ni exporta
todas las filas. Descargar no crea gráficas, versiones ni entradas del registro.

MCP `data_chart` admite también `action="get", chart_id=<id guardado>, image=true,
lang="en"|"es"`. Devuelve el PNG completo como ImageContent `image/png`, con los
metadatos JSON acotados por separado y sin duplicar el base64. `image=false`
devuelve sólo metadatos. Omitir `action` conserva la creación anterior y sus límites
de respuesta. El catálogo conjunto sigue teniendo 43 herramientas.

En Datasets, **Conservar libro original (.xlsx)** muestra origen, hoja/rango,
estructura importada, pestañas y coincidencia de la huella registrada. Descarga una
copia actualizando sólo la hoja del dataset y versión seleccionados mediante el
exportador nativo (`POST /api/export/workbook/download`). La exportación plana
CSV/JSON/Parquet/XLSX sigue disponible por separado. El original y las versiones
guardadas no cambian; se añaden recibo y registro de análisis normales. Requiere un
`.xlsx` importado con estructura compatible. No sobrescribe fórmulas ni recalcula
fórmulas o gráficos; conserva las validaciones sin comprobar sus valores. No es
un backup del proyecto ni una promesa de fidelidad universal de Excel. Actualizar
varias hojas sigue disponible mediante la API/MCP `data_export` existente.

Conjuntos de datos → **Exportar datos** descarga todas las filas de la versión
mostrada a CSV, JSON, Parquet o XLSX con una sola hoja. La petición captura la
versión explícitamente y usa el escritor nativo de `data_export` mediante
`POST /api/export/download`. No modifica la fuente ni las versiones guardadas;
crea archivos de exportación y la entrada habitual del registro de análisis.
El XLSX plano no conserva el libro original y estos archivos no son un backup
del proyecto.

El panel de receta de un conjunto abre cualquier versión guardada mediante las operaciones existentes de deshacer/rehacer. Se puede pulsar un paso anterior o elegir una versión posterior hasta que una transformación nueva sustituya esa rama. Exportar SQL incorpora copia y descarga `.sql`, en castellano e inglés. El script usa tablas locales del conjunto; no es una copia autónoma de los datos. UI, API y MCP `data_undo`/`data_recipe` comparten el cálculo nativo existente; el catálogo combinado sigue en 43 herramientas.

## Módulos nativos de datos y cálculo

Nightingale incorpora todas las funciones de Laplace: aritmética exacta, álgebra, unidades, fechas, estadística, SQL sobre archivos, gráficos e historial de cálculo. Datos y Cálculo son interruptores independientes, ambos OFF en la primera instalación o migración. Arranca la app expresamente desde Hoard Hub y activa lo que necesites. La calculadora funciona sin cargar el workbench ni DuckDB. Las interfaces completas están en `/data/` y `/calculation/`, dentro de un único servidor.

El catálogo MCP estático conserva 40 herramientas nativas y añade dos controles de módulos. Las tres colisiones se publican como `laplace_data_list`, `laplace_data_query` y `laplace_data_chart`. Los datos anteriores se adoptan en su ruta mediante `data/modules.json`, sin copiar ni cambiar formatos. Véase [arquitectura y procedencia](docs/FUSION.md).


El banco de trabajo de datos para un modelo de lenguaje local: ingesta un archivo desordenado,
límpialo con pasos versionados que puedes deshacer, compruébalo contra reglas que tú defines,
represéntalo en gráficos y paneles, entrena un modelo rápido, y pregúntale algo — todo en tu propio
equipo, y todo alcanzable por un asistente mediante MCP exactamente como lo usarías tú mismo.

Todo se queda en local: DuckDB para los datos, SQLite para los metadatos (fuentes, versiones,
reglas de calidad, gráficos, paneles, modelos, el registro de análisis), sin cuentas, sin llamadas
de red salvo a una URL de archivo que tú le hayas dado.

Forma parte de la familia Hoard (ver `faustus-plugin.json`). Cálculo conserva el catálogo de
archivos de solo lectura de Laplace junto al workbench reversible de Nightingale.

## Por qué existe

Limpiar un conjunto de datos real es normalmente un viaje sin vuelta atrás: sobrescribes el
archivo, o guardas cinco copias a medio nombrar "por si acaso". Nightingale hace el viaje
reversible. Cada paso de limpieza — filtrar, renombrar, convertir un tipo, rellenar un nulo,
derivar una columna, unir dos conjuntos de datos, veintiún tipos en total — crea una versión nueva,
real y materializada. Deshacer es instantáneo. Un paso que no te ha gustado nunca toca la versión
anterior. Y si el *archivo de origen* cambia más tarde, "actualizar" lo vuelve a ingerir y reproduce
toda tu receta sobre los datos nuevos automáticamente, deteniéndose limpiamente si un paso ya no
aplica.

## Qué está implementado

| Función | Qué hace | Límites |
| --- | --- | --- |
| **Ingesta** | CSV/TSV (incl. formatos españoles con separador punto y coma y coma decimal), Excel, Parquet, JSON/NDJSON, SQLite, un patrón de carpeta, una URL, o texto pegado. | Una ingesta se ejecuta en un hilo en segundo plano con un límite de 5 minutos; los archivos muy grandes deberían ser Parquet o CSV pre-filtrado en lugar de un libro Excel de 50 hojas. Ingerir con un nombre que ya existe se rechaza (y el conjunto existente no se toca): usa refresh para recargarlo o elige otro nombre. |
| **Desde la familia** | Tipo de fuente `hoard`: los registros que otra app Hoard devuelve con una de sus herramientas, llamada a través de Hoard Hub (`family.call`); la lista de registros se busca en la respuesta (o se indica con `list_path`), se aplana a una fila por registro (los objetos anidados pasan a columnas `a_b`, las listas a texto JSON) y se ingiere como JSON. Preajustes: `ledger_transactions` (`list_entries` de Ledger, 200 por llamada hasta 5000 movimientos, importes en céntimos), `phileas_shipments` (`shipments_list` de Phileas, entregados; las fechas epoch pasan a timestamp y se añade `transit_days`: días naturales de enviado a entregado), `argus_app_time` (`screen_app_time` de Argus: **sin verificar**, no había acceso a esa app para comprobar el nombre de la herramienta ni la forma de la respuesta). En la interfaz: Fuentes, **Desde la familia**. Por MCP: `data_ingest` con `kind="hoard"` y `preset` o `app` + `tool` (+ `args`, `list_path`, opciones `paginate`, `max_rows`, `epoch_columns`, `derive`). | Necesita el hub y la otra app en marcha; si falta alguno, el error dice cuál. Repetir la misma ingesta bajo el mismo nombre añade una VERSIÓN NUEVA (`ingest`) sobre la actual, así que Deshacer vuelve a la captura anterior y los pasos de limpieza no se reaplican; `data_refresh`, en cambio, reconstruye desde la app y reaplica la receta. Otra app o herramienta bajo un nombre ocupado se rechaza como cualquier ingesta. La paginación solo sirve para herramientas con argumentos limit/offset; una importación recortada lo indica con `truncated`. |
| **Transformar** | 22 tipos de paso (filter, select, drop, rename, cast, fill_null, drop_duplicates, derive, split_column, operaciones de texto, replace, bin, date_parts, group, pivot, unpivot, join, union, sort, sample, window, SQL en crudo) — todos previsualizables antes de aplicarse. `GET /api/transforms` documenta los parámetros; añade un dataset para consultar nombres/tipos de columna, versión y filas actuales. | El paso `sql` y la herramienta de consulta SQL aceptan exactamente una sentencia de solo lectura; sin escrituras, sin scripts multi-sentencia. Consulta [los parámetros de transformación](docs/TRANSFORM-OPERATIONS.es.md). |
| **Comparar filas** | La vista Filas compara versiones guardadas mediante columnas seleccionadas de idéntico nombre/tipo. Cuenta repeticiones y NULL; las tablas Sólo A / Sólo B tienen paginación independiente. `data_rows_compare` y `POST /api/datasets/{name}/versions/rows` comparten motor. | Explicita columnas omitidas y motivos; compara filas proyectadas sin adivinar identidades. Las celdas usan el límite existente de vista previa de 500 caracteres; los recuentos usan valores completos. |
| **Comparar versiones** | Desde Datasets o el panel de receta, compara dos versiones guardadas: filas/columnas, columnas añadidas/retiradas/cambio de tipo, nulos, distintos aproximados y mínimos/máximos. También por `data_versions_compare` y `GET /api/datasets/{name}/versions/compare?version_a=0&version_b=1`. | Compara perfiles de columnas por nombre, sin emparejar filas ni celdas. Las diferencias son B menos A; comparar no cambia la versión actual. |
| **Deshacer / rehacer / receta** | Cada versión es una tabla real; un panel lateral muestra la receta completa, la exporta como un script SQL ejecutable, y puede reproducirla contra datos de origen reingeridos. | Ramificar tras un deshacer descarta las versiones rehechas hacia adelante (como el deshacer de cualquier editor) — hay una sola línea temporal, no un árbol. |
| **Reglas de calidad** | 9 tipos de regla (not_null, unique, accepted_values, range, regex, row_count, freshness, integridad referencial, SQL personalizado), ejecutadas bajo demanda, con historial de aprobado/fallido. Una regla `unique` fallida dice cuántos valores se repiten, cuántas filas son copias sobrantes y cuántas quedarían al quitar duplicados. | Las reglas comprueban la versión *actual* del conjunto de datos; no se ejecutan automáticamente al ingerir salvo que las vuelvas a ejecutar tú mismo. |
| **Gráficos y paneles** | 11 tipos de gráfico, agregados en SQL (nunca se trae una tabla completa al cliente), representados de forma interactiva (Vega-Lite) y como PNG (para exportar/uso del asistente); los paneles combinan gráficos guardados con casillas KPI de expresión SQL. | Los gráficos leen la versión actual del conjunto de datos en el momento de representarse — un gráfico no congela datos pasados, refleja la última limpieza. |
| **Ediciones reversibles de paneles** | Cada cambio a un panel por elementos (añadir/quitar/mover un elemento, cambiar la consulta de un KPI o un gráfico, título, nombre, filtro, columnas) es una acción tipada con esquema JSON, aplicada por un único reductor, guardada en un registro por panel e invertible. La página Paneles (botones, deshacer/rehacer, historial) y la herramienta MCP usan el mismo camino; los paneles anteriores reciben ids estables y una instantánea inicial al cargarse. Ver `docs/DASHBOARD-ACTIONS.md`. | Una acción nueva descarta la rama rehacer; las referencias (gráfico, conjunto de datos) se comprueban al añadir o cambiar, no al deshacer. |
| **Paneles como código** | Un panel como un archivo YAML sencillo sobre una capa semántica reutilizable (métricas y dimensiones con nombre por conjunto de datos) — filtros, pestañas, filas de casillas de métrica/gráfico/tabla/texto, bucles `for` sobre los N valores principales de una dimensión, condiciones `if`, y métricas derivadas (p. ej. una métrica desplazada un mes atrás). Validado con una pista (`hint`) por cada problema, representado con el SQL y el error de cada casilla por separado (una casilla rota nunca rompe el resto), con un historial de 20 versiones con diffs, y exportable a los paneles basados en elementos de arriba. Ver `docs/DASHBOARDS_AS_CODE.md`. | Todo se compila a SQL de solo lectura sobre la vista propia de un conjunto de datos, el mismo nivel de confianza que el filtro de un gráfico. |
| **Modelos** | Aprendizaje supervisado (detección automática de tarea, validación cruzada, importancia de variables, matriz de confusión), agrupamiento k-means (codo + silueta), PCA, detección de anomalías por bosque de aislamiento, previsión Holt-Winters con intervalo y una alternativa estacional-ingenua documentada. Un control "Escribir resultados en" (conjunto de datos nuevo por defecto, con una explicación de una línea de la alternativa) elige el destino de las predicciones/etiquetas/marcas: `new_dataset` (por defecto) las pone en su propio conjunto de datos nuevo (`<origen>__model_<id>`, etc.), sin tocar el de origen; `new_version` escribe sobre el propio origen, y aun así solo añade una columna; `none` solo muestra el resultado. Cada columna de origen copiada — en cualquiera de los dos destinos — conserva su tipo exacto de DuckDB (una DATE sigue siendo DATE, un DECIMAL sigue siendo DECIMAL); solo las columnas nuevas de predicción/etiqueta se calculan con pandas. | El entrenamiento reduce la muestra a 200.000 filas; la selección automática de variables excluye columnas de tipo identificador casi únicas (se informa de ello) para evitar una explosión de codificación one-hot — pasa una lista explícita de variables para anularlo. |
| **Eliminar un conjunto de datos** | Elimina un conjunto de datos, todas sus versiones, y en cascada sus reglas de calidad, gráficos y modelos; un panel que usaba uno de esos gráficos sigue funcionando (muestra el elemento como no disponible) en lugar de romperse. Disponible desde la pantalla de Datasets (con un diálogo de confirmación que lista lo que depende de él), `DELETE /api/datasets/{name}` (`?force=true` para saltarse las dependencias), y la acción `action="delete"` de la herramienta MCP `data_ingest`. | Se bloquea por defecto si hay gráficos/paneles/modelos que dependen del conjunto de datos — pasa `force`/`force=true` para eliminarlo de todos modos. |
| **Registro de análisis** | Cada operación (ingesta, transformación, ejecución de calidad, gráfico, modelo, exportación...) recibe un id como `N-000123`, con la entrada, un resumen de una línea, el tiempo empleado, y si vino de ti o del asistente. | El registro es de solo-añadir y tiene un límite de tamaño por campo; es un rastro de auditoría, no una copia de seguridad completa de los datos. |
| **Pregunta a tus datos** | Una pregunta en lenguaje llano se convierte en una consulta SQL (que se muestra, no se oculta) ejecutada a través de un modelo de lenguaje local compartido vía Hoard Link. | Necesita un backend de LLM resuelto (Faustus, un servidor llama.cpp local, Ollama, o un endpoint compatible con OpenAI) — sin ninguno configurado lo dice claramente en lugar de inventar. |
| **Lab** | Ciencia de datos más profunda sobre el workbench, con su propia página (siete pestañas — Explorar, Modelos, Diagnosticar, Optimizar, Comparar y desviación, Canalización, Informe): EDA completo (correlación, patrones de nulos + vista previa de imputación, valores atípicos IQR/z-score, sugerencias de codificación con aplicación de un clic como paso) y una puntuación de calidad de 0 a 100 (solo llega a 100 cuando cada componente es realmente perfecto); un registro de modelos conectable (lineal/ridge/logística, random forest, gradient boosting, extra trees, k-NN, MLP, proceso gaussiano, más XGBoost/LightGBM si están instalados) con modelos versionados y persistidos; diagnóstico de modelos (residuos, predicho-vs-real, error por grupo, sesgo en el tiempo, sobre/subajuste, calibración, y para clasificadores una curva ROC representada — una curva por clase, uno contra el resto, cuando hay más de dos); ajuste de hiperparámetros (Optuna TPE o una alternativa aleatoria); explicaciones (SHAP o importancia por permutación + dependencia parcial); optimización bayesiana de las variables de entrada de un modelo (sustituto GP + EI/UCB) y frentes de Pareto multiobjetivo; detección de deriva y comparación de curvas/series entre conjuntos de datos; un informe en PDF; y un editor de canalización visual (añadir/reordenar/editar/previsualizar pasos como una cadena de nodos, aplicados a través del mismo motor de pasos que la página Datasets). Ver `docs/LAB.md`. Datasets y Modelos enlazan directamente con ella ("Abrir en Lab" / "Abrir en el registro de Lab"). | XGBoost/LightGBM/Optuna/SHAP son opcionales (`requirements-lab.txt`) — cada función degrada a una alternativa documentada sin ellos. |

Para un `.xlsx` importado, la exportación conservando el libro puede escribir las celdas cambiadas
del conjunto de datos en una copia del original y mantener las celdas y funciones del libro que
no cambiaron. Una solicitud puede actualizar varias pestañas importadas en esa copia. Incluye un
inventario del origen y un recibo de trazabilidad; no permite sobrescribir fórmulas y conserva las
reglas de validación sin aplicarlas. Consulta la [guía de exportación](docs/WORKBOOK-EXPORT.es.md)
para ver la API, MCP y los límites admitidos.

La cuadrícula de datos formatea cada celda según el tipo de columna de DuckDB y el idioma de la
interfaz — las fechas y marcas de tiempo se muestran como fechas (una marca de tiempo exactamente a
medianoche omite la hora), y los números llevan separador de miles y una precisión razonable (2
decimales para columnas `DECIMAL`, hasta 4 cifras significativas para el resto de tipos numéricos)
— mientras que el valor exacto sin formatear queda a un pase del ratón (una descripción emergente) y
a un doble clic (lo copia al portapapeles).

### Lab, en la interfaz

<img src="docs/media/lab-explore.png" width="420" alt="Pestaña Explorar de Lab: medidor de puntuación de calidad, mapa de calor de correlación, patrones de nulos, diagramas de caja de valores atípicos"> <img src="docs/media/lab-diagnose.png" width="420" alt="Pestaña Diagnosticar de Lab: métricas, predicho-vs-real, residuos, curva de aprendizaje">

## Casos de uso

- Suelta un CSV desordenado que te mandó un compañero, observa cómo el banco de trabajo adivina
  tipos y codificaciones, límpialo con unos cuantos pasos, y exporta algo que de verdad le
  entregarías a alguien.
- Pide a un asistente que "mire estos datos y te diga qué falla" — puede perfilar, ejecutar
  comprobaciones de calidad, y citar la entrada exacta `N-000123` del registro de lo que encontró.
- Mantén una receta contra un archivo de origen que se actualiza cada semana: actualizar vuelve a
  aplicar todos los pasos a los datos nuevos en una sola llamada, ejecuta las reglas de calidad
  guardadas y muestra los fallos con ejemplos. Puedes omitir esa comprobación con `check_quality=false`.
- Entrena un modelo la misma tarde sobre un conjunto de datos que acabas de terminar de limpiar,
  sin salir de la aplicación ni escribir un cuaderno.

## Puesta en marcha

Requiere Python 3.11+ y Node 22+ (Node solo para construir el cliente).

```bash
git clone <este repositorio> nightingale-hoard
cd nightingale-hoard
python -m venv venv
venv/bin/pip install -r requirements.txt      # Windows: venv\Scripts\pip install -r requirements.txt
venv/bin/pip install -r requirements-lab.txt  # opcional: Optuna/SHAP/XGBoost/LightGBM para el paquete Lab
npm install
npm run build
venv/bin/python -m nightingale --demo         # Windows: venv\Scripts\python -m nightingale --demo
```

Abre http://127.0.0.1:5189 y activa Datos o Cálculo en Funciones. `--demo` siembra al activar Datos tres conjuntos inventados (un CSV de ventas
con formato español desordenado, una tabla de clientes, una serie temporal de sensor estacional con
algunas anomalías inyectadas) en una carpeta `data-demo/` separada, así que nunca toca datos reales.
Quita `--demo` para un banco de trabajo limpio.

- `python scripts/launch.py` arranca la aplicación en un puerto libre y abre el navegador.
- `python scripts/dev.py` ejecuta uvicorn con `--reload` más el servidor de desarrollo de Vite
  (haciendo proxy de `/api`).

## Configuración (variables de entorno)

| Variable | Por defecto | Significado |
| --- | --- | --- |
| `NIGHTINGALE_PORT` / `PORT` | `5189` | Puerto preferido; `PORT_STRICT=1` lo fija, si no se toma el primer puerto libre a partir de ahí. |
| `NIGHTINGALE_DATA_DIR` | `<repo>/data` (`data-demo` con `--demo`) | Archivo DuckDB, SQLite de metadatos, `mcp-token`, exportaciones, imágenes de gráficos. |
| `NIGHTINGALE_ALLOWED_HOSTS` | | Nombres de host adicionales aceptados detrás de un túnel (ver abajo). |

### Acceso desde tu móvil (detrás de un túnel)

El servidor se vincula a `127.0.0.1` y solo responde a peticiones cuyo `Host` sea `localhost`,
`127.0.0.1` o `[::1]`. Para alcanzarlo desde tu móvil a través de un túnel, lista los nombres de
host adicionales en `NIGHTINGALE_ALLOWED_HOSTS`, separados por comas, nombres exactos o `*.sufijo`:
`NIGHTINGALE_ALLOWED_HOSTS=mi-pc.ejemplo,*.ts.net`. El puerto y las mayúsculas se ignoran, y el
`Origin` de las llamadas a la API también debe resolver a uno de esos hosts. Una vez abierto a
través del túnel, el navegador ofrece instalarlo como PWA.

## Conectar con Faustus

Carga `faustus-plugin.json` en Faustus para descubrir el catálogo MCP estático. Arranca la app
expresamente desde Hoard Hub y activa el módulo solicitado. El puente usa la API HTTP de la app,
igual que la interfaz del navegador, y conserva las bases de datos dentro de su módulo nativo.

## API

Todo JSON; los errores son `{ "error": "..." }`.

- `GET /api/health`, `GET /api/status`
- `GET /api/sources`, `POST /api/sources/ingest` (`kind: "hoard"` con `preset` o `app` + `tool` + `args` + `list_path`), `GET /api/sources/hoard-presets`
- `GET /api/datasets`, `POST /api/datasets/{name}/refresh`, `DELETE /api/datasets/{name}`, `GET /api/datasets/{name}/dependents`
- `GET /api/datasets/{name}/{profile,preview,lineage,recipe,correlation}`
- `POST /api/datasets/{name}/{transform,undo,redo,join-preview}`
- `POST /api/query`
- `GET/POST /api/datasets/{name}/quality`, `POST /api/datasets/{name}/quality/run`, `DELETE /api/quality/{id}`
- `GET/POST /api/charts`, `GET/DELETE /api/charts/{id}`
- `GET/POST /api/dashboards`, `GET /api/dashboards/{id}`, `POST /api/dashboards/{id}/items`\n- `GET /api/dashboards/action-schemas`, `POST /api/dashboards/{id}/{actions,undo,redo}`, `GET /api/dashboards/{id}/history` - ediciones tipadas y reversibles (`docs/DASHBOARD-ACTIONS.md`)
- `GET/PUT /api/dac/semantic`, `POST /api/dac/semantic/validate`, `GET /api/dac/semantic/suggest?dataset=`
- `GET/POST /api/dac/dashboards`, `GET/PUT/DELETE /api/dac/dashboards/{slug}`, `POST /api/dac/dashboards/{slug}/{rename,validate,render,export}`, `GET /api/dac/dashboards/{slug}/{history,diff}`, `POST /api/dac/dashboards/import` — ver `docs/DASHBOARDS_AS_CODE.md`
- `POST /api/models/{train,cluster,pca,anomaly,forecast}`, `GET /api/models`
- `POST /api/export`
- `GET /api/log`, `GET /api/log/{id}`
- `POST /api/ask`, `GET /api/ask/available`
- `GET /api/agent/tools` (catálogo + instrucciones), `POST /api/agent/call` (token Bearer desde `<DATA_DIR>/mcp-token`)

## Herramientas MCP

Consulta `docs/MCP.md` para la referencia del módulo Datos (21 herramientas, de `data_ingest` a
`data_ask`) y un recorrido mínimo. Las instrucciones incluidas indican al asistente que previsualice
una transformación antes de aplicarla cuando el efecto no sea obviamente seguro, que nunca asuma el
nombre de un conjunto de datos (`data_list` primero), y que cite el id del registro de análisis
(`N-000123`) al informar de un resultado. Cálculo aporta otras 20 herramientas nativas,
documentadas en [su referencia](fusion/laplace-docs/MCP.md), con las tres colisiones prefijadas
como `laplace_`. `nightingale_modules` y `nightingale_set_module` completan el catálogo de 46 (24 Datos, 20 Cálculo y 2 controles).

## Pruebas

```bash
venv/bin/python -m pytest -q     # Windows: venv\Scripts\python -m pytest -q
```

Las pruebas cubren el motor del banco de trabajo (cada vía de ingesta, cada paso de
transformación, versionado y deshacer/rehacer/reproducción), reglas de calidad, gráficos y paneles,
modelos (incluida la exclusión de columnas de tipo identificador, la corrección de previsión con
marcas de tiempo duplicadas, y que tanto la salida por defecto a un conjunto de datos nuevo como la
opción `write_to="new_version"` conservan el tipo exacto de cada columna copiada), la eliminación de
conjuntos de datos (comprobación de dependencias, cascada, forzado), la API HTTP, las herramientas
del agente a través de `/api/agent/call`, la protección de peticiones, los puntos finales de la PWA,
y una prueba de extremo a extremo en subproceso a través del puente MCP por stdio.

## Licencia

MIT — Luis María Salete Cuartero.

## Panel de Faustus

Un botón «Faustus» abre el panel de asistente común de la familia sin salir de la app. Faustus guarda las conversaciones
(también en Faustus Studio), ejecuta el agente con el modelo elegido globalmente y usa las herramientas de esta app. Cada
turno lleva el dataset, dashboard, dashboard como código, modelo del Lab o entrada del registro que haya en pantalla; el YAML sin guardar de un dashboard como código o de la capa semántica, y los formularios de gráfico y regla nuevos. Al terminar un turno, las listas y la vista abierta se refrescan sin perder lo que estuvieras escribiendo.
El panel lo sirve Hoard Link (`install_spa`) y habla con Faustus a través del relay de la app, así que ningún token llega
al navegador.


## Datos propios y compatibilidad

Los datos nativos pueden trasladarse a `data/native/<módulo>/data`, con configuración privada junto a cada módulo. La ficción de Writer utiliza su perfil de escritorio `writers-hoard/fiction/data`. Traslada árboles completos, incluidos WAL/SHM de SQLite, medios, modelos descargados y sesiones del navegador; actualiza las rutas absolutas guardadas antes de activar el módulo. Los manifiestos de `compatibility/<id>/faustus-plugin.json` conservan identidades, nombres de herramientas y tokens sin necesitar el repositorio retirado. Añade el directorio `compatibility` de cada producto a las raíces del Hub antes de la raíz general. El catálogo y los puentes compatibles nunca arrancan aplicaciones. Todos los módulos permanecen apagados durante el traslado.

Las instrucciones anteriores de adopción in-place describen una importación opcional. Una vez comprobados el traslado y las rutas, ya no es necesario conservar las carpetas de los productos antiguos.

## Reutilizar la configuración de un gráfico

En Gráficos, **Usar como base** carga en el constructor editable el conjunto de datos, tipo, ejes, agregación, color, filtro de filas y título de un gráfico guardado. Identifica el origen y enfoca el título. Cargar los campos no guarda nada; **Guardar gráfico** crea otro ID y conserva el original. Los campos permanecen tras guardar o un error, incluidas las ediciones posteriores al envío. Refrescar la lista no vuelve a aplicar la configuración de origen. Reutiliza las API de lectura/creación y las acciones de `data_chart` en MCP; las43 herramientas siguen intactas. Consulta las filas actuales del conjunto, sin duplicar datos, imágenes históricas, dashboards o historial de análisis.

## Libros de Excel como cálculo

Compila un `.xlsx` en un cálculo ejecutable (parámetros, tablas, resultados), lánzalo con otros valores, traza un resultado hasta sus datos, compáralo con lo que guardó Excel, recalcula casos nuevos con semilla en Excel o LibreOffice y exporta un módulo Python independiente. Las fórmulas no soportadas se congelan con el valor de Excel y se listan. Página **Libros Excel**, API `/api/sheets/*`, herramienta `data_workbook`. Detalle: `docs/WORKBOOK-COMPILER.es.md`.

## Conciliar dos conjuntos

Compara dos conjuntos fila a fila por uno o varios pares de columnas clave (por ejemplo, una exportación del CRM frente al ERP). Nightingale sugiere pares de clave y de valor por parecido de nombres y solapamiento de valores, y clasifica cada fila como emparejada, solo izquierda, solo derecha, ambigua (clave duplicada) o sin clave, y cada celda comparada como igual, igual dentro de la tolerancia, distinta, cero frente a vacío, vacío frente a ausente o presente en un solo lado. Opciones de normalización: recortar espacios, ignorar mayúsculas, comparación numérica, vacío como ausente y tolerancia numérica. Cada recuento lleva ejemplos y una explicación; el resultado fila a fila se puede guardar como conjunto nuevo o descargar en CSV. Página **Conciliar**, API `/api/reconcile/*` (`suggest`, `run`, `export.csv`), herramienta `data_reconcile`. Cada ejecución queda en el registro de análisis.

## Laboratorio de escenarios

Describe una operación como un modelo (llegadas por tasa de Poisson, calendario horario o tiempos entre llegadas medidos; estaciones con servidores, turnos, colas limitadas y distribuciones de tiempo de servicio; costes; y, opcionalmente, un artículo de stock con punto de pedido y plazo de reposición) y simúlalo con un motor de eventos discretos con semilla. Cada ejecución usa varias réplicas y da media e intervalo del 95 % de rendimiento, plazo (media, mediana, p90), espera, utilización y cola por estación, trabajo en curso, trabajos perdidos, roturas de stock y coste, además de una comprobación de la ley de Little y, para una sola estación M/M/c, el tiempo de espera de Erlang-C junto al simulado. Las variantes con nombre se comparan con números aleatorios comunes y cada diferencia se marca como distinguible o no; un barrido varía un parámetro sobre una lista de valores. La calibración lee duraciones o marcas de tiempo de una columna, informa del tamaño de muestra y avisa si es pequeño. El resultado son escenarios bajo supuestos declarados, no una predicción. Los modelos se guardan por nombre y los resultados pueden guardarse como conjuntos. Página **Escenarios**, API `/api/scenarios/*`, herramienta `data_scenario`. Cada ejecución queda en el registro de análisis.
