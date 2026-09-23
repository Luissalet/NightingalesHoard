"""Tools exposed to the assistant. One list drives /api/agent/* and mcp_server.py.

Descriptions start with a plain-language summary and end with EN/ES keyword
hints, as required by the family contract. Every tool proxies straight to a
`Services` method: this file only validates input and shapes the call.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional

from pydantic import BaseModel, Field

from .services import Services

AGENT_INSTRUCTIONS = (
    "Nightingale's Hoard is the user's own data workbench: ingest files/folders/URLs/pasted text into a local "
    "DuckDB workbench, clean data with versioned steps (undo/redo, lineage, recipe export/replay), define and run "
    "quality rules, build charts and dashboards, train quick models (supervised/clustering/PCA/anomaly/forecast), "
    "and query everything read-only with SQL. Every call is recorded in the analysis log with an id like "
    "'N-000123' — cite that id when you report a result back to the user. Always preview a transform "
    "(data_transform with preview=true) before applying it when the change is not obviously safe. Never assume a "
    "dataset name: call data_list first if unsure. This app never reads files or the database directly outside "
    "these tools."
)


def _ann(read_only: bool, destructive: bool = False, idempotent: bool | None = None) -> dict[str, bool]:
    return {"readOnlyHint": read_only, "destructiveHint": destructive,
            "idempotentHint": read_only if idempotent is None else idempotent, "openWorldHint": False}


class Empty(BaseModel):
    pass


class IngestArgs(BaseModel):
    action: Literal["ingest", "delete"] = Field(
        "ingest", description="'ingest' (default) loads a new dataset. 'delete' permanently removes an existing "
                               "one (its versions, quality rules, charts and models) — pass `name` as the dataset "
                               "to delete; other ingest-only fields are ignored.")
    kind: Literal["file", "folder", "url", "paste"] = Field("file", description="What to ingest (action=ingest).")
    path: Optional[str] = Field(None, description="Absolute path to a file or folder (kind=file/folder).")
    url: Optional[str] = Field(None, description="http(s) URL to a CSV/JSON resource (kind=url).")
    text: Optional[str] = Field(None, description="Pasted CSV/JSON text (kind=paste).")
    name: Optional[str] = Field(None, max_length=200, description=(
        "Dataset name. For action=ingest, derived from the source if omitted; for action=delete, the dataset to "
        "delete (required)."))
    fmt: Literal["csv", "json"] = Field("csv", description="Format for kind=url/paste.")
    glob: str = Field("*.csv", description="File pattern for kind=folder, e.g. '*.csv' or '*.parquet'.")
    options: dict[str, Any] = Field(default_factory=dict, description=(
        "delimiter, header, encoding (utf-8/utf-16/latin-1), date_format, decimal_separator, thousands_separator, "
        "sheet/sheets/skip_rows (Excel), tables (SQLite), flatten (JSON)."))
    force: bool = Field(False, description="action=delete only: delete even if charts/dashboards/models depend on "
                                             "this dataset (otherwise the call fails and lists them).")


class RefreshArgs(BaseModel):
    dataset: str = Field(..., description="Dataset name.")


class DatasetArgs(BaseModel):
    dataset: str


class ProfileArgs(BaseModel):
    dataset: str
    version: Optional[int] = None


class PreviewArgs(BaseModel):
    dataset: str
    version: Optional[int] = None
    limit: int = Field(50, ge=1, le=1000)


class TransformArgs(BaseModel):
    dataset: str
    op: str = Field(..., description="filter/select/drop/rename/cast/fill_null/drop_duplicates/derive/split_column/"
                                        "text/replace/bin/date_parts/group/pivot/unpivot/join/union/sort/sample/window/sql")
    params: dict[str, Any] = Field(default_factory=dict)
    preview: bool = Field(True, description="true: show the effect without applying; false: apply and create a new version.")


class UndoArgs(BaseModel):
    dataset: str
    direction: Literal["undo", "redo"] = "undo"
    steps: int = Field(1, ge=1, le=100)


class RecipeArgs(BaseModel):
    dataset: str
    action: Literal["show", "export", "replay"] = "show"


class QueryArgs(BaseModel):
    sql: str = Field(..., description="One read-only SELECT/WITH/DESCRIBE/SUMMARIZE statement over dataset names.")
    limit: int = Field(200, ge=1, le=5000)


class QualityArgs(BaseModel):
    action: Literal["define", "run", "report", "delete"] = "run"
    dataset: Optional[str] = None
    name: Optional[str] = Field(None, description="Rule name (define).")
    kind: Optional[str] = Field(None, description="not_null/unique/accepted_values/range/regex/row_count/"
                                                     "freshness/referential/custom_sql (define).")
    params: dict[str, Any] = Field(default_factory=dict, description="Every rule kind names the column(s) it "
                                     "checks with 'columns' (canonical: a single name or a list) — 'column' is "
                                     "accepted the same way as an alias, so unique's {\"columns\": [\"a\", \"b\"]} "
                                     "and not_null's {\"column\": \"a\"} both work regardless of which key is used.")
    rule_id: Optional[int] = Field(None, description="Run one rule, or delete it.")


class ChartArgs(BaseModel):
    dataset: str
    kind: str = Field(..., description="bar/grouped_bar/stacked_bar/line/area/scatter/histogram/box/heatmap/pie/donut")
    x: Optional[str] = None
    y: Optional[str] = None
    agg: str = Field("sum", description="sum/avg/count/min/max/median")
    color: Optional[str] = Field(None, description="series/group column, or the value column for heatmap.")
    filter: Optional[str] = Field(None, description="A SQL WHERE fragment.")
    title: str = ""
    name: Optional[str] = None
    image: bool = Field(False, description="true also returns a base64 PNG; keep false unless an image was asked for.")


class DashboardArgs(BaseModel):
    action: Literal["create", "add", "list", "get"] = "list"
    dashboard_id: Optional[int] = None
    name: Optional[str] = None
    item: Optional[dict[str, Any]] = Field(None, description='{"type":"chart","chart_id":N} or {"type":"kpi","dataset":...,"expr":"AVG(x)","label":...}')
    items: Optional[list[dict[str, Any]]] = None
    filter: Optional[dict[str, Any]] = None


class ModelTrainArgs(BaseModel):
    action: Literal["train", "list"] = "train"
    dataset: Optional[str] = None
    target: Optional[str] = None
    features: Optional[list[str]] = None
    task: Optional[Literal["regression", "classification"]] = None
    algorithm: Optional[str] = Field(None, description="linear/logistic/random_forest/gradient_boosting")
    test_size: float = Field(0.2, gt=0, lt=0.9)
    seed: int = 42
    write_to: Literal["new_dataset", "new_version", "none"] = Field(
        "new_dataset", description="'new_dataset' (default) puts predictions in their own new dataset "
                                     "(source untouched); 'new_version' adds a predicted_<target> column to a new "
                                     "version of the source itself (never changes an existing column's type); "
                                     "'none' skips writing them anywhere.")
    name: Optional[str] = None


class ClusterArgs(BaseModel):
    dataset: str
    features: list[str] = Field(..., min_length=1)
    k: Optional[int] = Field(None, ge=2, le=20)
    seed: int = 42
    write_to: Literal["new_dataset", "new_version", "none"] = Field(
        "new_dataset", description="'new_dataset' (default) puts cluster labels in their own new dataset (source "
                                     "untouched); 'new_version' adds a cluster column to a new version of the "
                                     "source itself; 'none' skips writing them anywhere.")
    name: Optional[str] = None


class ForecastArgs(BaseModel):
    dataset: str
    date_col: str
    value_col: str
    horizon: int = Field(12, ge=1, le=365)
    seasonal_period: Optional[int] = Field(None, ge=2, le=366)
    name: Optional[str] = None
    write_to: Literal["new_dataset", "none"] = Field(
        "new_dataset", description="'new_dataset' (default) puts the forecast in its own new dataset; 'none' "
                                     "skips writing it anywhere. The forecast has different rows than the source, "
                                     "so there's no 'new_version' option.")
    freq: Literal["auto", "day", "week", "month", "quarter"] = Field(
        "auto", description="Calendar grain to resample the series onto before forecasting. 'auto' (default) "
                              "picks by how far the dates span and how densely they fill that span, so irregular "
                              "daily transactions with gaps aggregate to a coarser, fully-regular series (summing "
                              "values per period, 0 for an empty one) instead of tripping up Holt-Winters.")


class ExportArgs(BaseModel):
    dataset: str
    format: Literal["csv", "xlsx", "parquet", "json"] = "csv"
    path: Optional[str] = Field(None, description="Filename inside the app's exports folder; derived if omitted.")


class LogArgs(BaseModel):
    q: Optional[str] = Field(None, description="Free text, or an id like 'N-000123'.")
    source: Optional[Literal["ui", "agent"]] = None
    dataset: Optional[str] = None
    limit: int = Field(50, ge=1, le=200)


class AskArgs(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    datasets: Optional[list[str]] = Field(None, description="Restrict to these datasets; default is every registered one.")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    annotations: dict[str, bool]
    run: Callable[[Services, Any], Any]


def _run_ingest(s: Services, a: IngestArgs) -> dict:
    if a.action == "delete":
        if not a.name:
            raise ValueError("name is required for action=delete")
        return s.dataset_delete(a.name, a.force, source="agent")
    if a.kind == "file":
        if not a.path:
            raise ValueError("path is required for kind=file")
        return s.ingest_file(a.path, a.name, a.options, source="agent")
    if a.kind == "folder":
        if not a.path:
            raise ValueError("path is required for kind=folder")
        return s.ingest_folder(a.path, a.name, a.glob, source="agent")
    if a.kind == "url":
        if not a.url:
            raise ValueError("url is required for kind=url")
        return s.ingest_url(a.url, a.name, a.fmt, a.options, source="agent")
    if not a.text:
        raise ValueError("text is required for kind=paste")
    if not a.name:
        raise ValueError("name is required for kind=paste")
    return s.ingest_text(a.text, a.name, a.fmt, a.options, source="agent")


def _run_refresh(s: Services, a: RefreshArgs) -> dict:
    return s.refresh(a.dataset, source="agent")


def _run_list(s: Services, _: Empty) -> dict:
    return s.list_datasets()


def _run_profile(s: Services, a: ProfileArgs) -> dict:
    return s.profile(a.dataset, a.version)


def _run_preview(s: Services, a: PreviewArgs) -> dict:
    return s.preview(a.dataset, a.version, a.limit)


def _run_transform(s: Services, a: TransformArgs) -> dict:
    if a.preview:
        return s.transform_preview(a.dataset, a.op, a.params)
    return s.transform_apply(a.dataset, a.op, a.params, source="agent")


def _run_undo(s: Services, a: UndoArgs) -> dict:
    if a.direction == "redo":
        return s.redo(a.dataset, a.steps, source="agent")
    return s.undo(a.dataset, a.steps, source="agent")


def _run_recipe(s: Services, a: RecipeArgs) -> dict:
    return s.recipe(a.dataset, a.action)


def _run_query(s: Services, a: QueryArgs) -> dict:
    return s.query(a.sql, a.limit, source="agent")


def _run_quality(s: Services, a: QualityArgs) -> dict:
    if a.action == "define":
        if not (a.dataset and a.name and a.kind):
            raise ValueError("define needs dataset, name and kind")
        return s.quality_define(a.dataset, a.name, a.kind, a.params, source="agent")
    if a.action == "run":
        if not a.dataset:
            raise ValueError("run needs dataset")
        return s.quality_run(a.dataset, a.rule_id, source="agent")
    if a.action == "report":
        if not a.dataset:
            raise ValueError("report needs dataset")
        return s.quality_report(a.dataset)
    if a.action == "delete":
        if not a.rule_id:
            raise ValueError("delete needs rule_id")
        return s.quality_delete(a.rule_id, source="agent")
    raise ValueError(f"unknown action: {a.action}")


def _run_chart(s: Services, a: ChartArgs) -> dict:
    return s.chart_create(a.dataset, a.kind, a.x, a.y, a.agg, a.color, a.filter, a.title, a.name, a.image, source="agent")


def _run_dashboard(s: Services, a: DashboardArgs) -> dict:
    if a.action == "create":
        if not a.name:
            raise ValueError("create needs name")
        return s.dashboard_create(a.name, a.items, a.filter, source="agent")
    if a.action == "add":
        if not (a.dashboard_id and a.item):
            raise ValueError("add needs dashboard_id and item")
        return s.dashboard_add(a.dashboard_id, a.item, source="agent")
    if a.action == "get":
        if not a.dashboard_id:
            raise ValueError("get needs dashboard_id")
        return s.dashboard_get(a.dashboard_id)
    return s.dashboard_list()


def _run_model(s: Services, a: ModelTrainArgs) -> dict:
    if a.action == "list":
        return s.model_list(a.dataset)
    if not (a.dataset and a.target):
        raise ValueError("train needs dataset and target")
    return s.model_train(a.dataset, a.target, a.features, a.task, a.algorithm, a.test_size, a.seed,
                          a.write_to, a.name, source="agent")


def _run_cluster(s: Services, a: ClusterArgs) -> dict:
    return s.model_cluster(a.dataset, a.features, a.k, a.seed, a.write_to, a.name, source="agent")


def _run_forecast(s: Services, a: ForecastArgs) -> dict:
    return s.model_forecast(a.dataset, a.date_col, a.value_col, a.horizon, a.seasonal_period, a.name,
                             a.write_to, source="agent", freq=a.freq)


def _run_export(s: Services, a: ExportArgs) -> dict:
    return s.export(a.dataset, a.format, a.path, source="agent")


def _run_log(s: Services, a: LogArgs) -> dict:
    return s.log_search(a.q, a.source, a.dataset, a.limit)


def _run_ask(s: Services, a: AskArgs):
    return s.ask(a.question, a.datasets, source="agent")


TOOLS: list[Tool] = [
    Tool("data_ingest", "Load a file/folder/URL/pasted text into the workbench as a new dataset (write); "
         "action='delete' permanently removes a dataset and everything derived from it instead (write, "
         "destructive).\nSinónimos: importar datos, cargar archivo, ingerir csv, subir excel, leer carpeta, "
         "importar url, borrar dataset, eliminar tabla.",
         IngestArgs, _ann(False, True, False), _run_ingest),
    Tool("data_refresh", "Re-ingest a dataset's source and replay its recorded recipe on the fresh data (write).\n"
         "Sinónimos: actualizar datos, releer archivo, refrescar fuente.",
         RefreshArgs, _ann(False, False, False), _run_refresh),
    Tool("data_list", "List every registered dataset with row/column counts and last update time.\n"
         "Sinónimos: listar datasets, qué datos hay, ver tablas.",
         Empty, _ann(True), _run_list),
    Tool("data_profile", "Column profile of a dataset version: types, nulls, distinct, min/max/mean/sd, histogram, "
         "top values, IQR outliers.\nSinónimos: perfil de datos, describir columnas, estadísticas de columna.",
         ProfileArgs, _ann(True), _run_profile),
    Tool("data_preview", "First rows of a dataset version, with total row count.\n"
         "Sinónimos: ver datos, muestra de filas, primeras filas.",
         PreviewArgs, _ann(True), _run_preview),
    Tool("data_transform", "Apply or preview one cleaning/reshaping step on a dataset (write when preview=false).\n"
         "Sinónimos: transformar datos, limpiar columna, filtrar filas, renombrar columna, agrupar datos.",
         TransformArgs, _ann(False, False, False), _run_transform),
    Tool("data_undo", "Move a dataset to an earlier (undo) or later (redo) version (write, non-destructive).\n"
         "Sinónimos: deshacer, rehacer, volver a la versión anterior.",
         UndoArgs, _ann(False, False, True), _run_undo),
    Tool("data_recipe", "Show, export as SQL, or replay a dataset's recorded step history (a pipeline).\n"
         "Sinónimos: receta de limpieza, exportar sql, volver a ejecutar el pipeline, linaje.",
         RecipeArgs, _ann(False, False, False), _run_recipe),
    Tool("data_query", "Run one read-only SQL SELECT over registered dataset views (DuckDB dialect).\n"
         "Sinónimos: consulta sql, ejecutar select, preguntar con sql.",
         QueryArgs, _ann(True), _run_query),
    Tool("data_quality", "Define, run or report data-quality rules (not_null/unique/range/regex/row_count/"
         "freshness/referential/custom_sql) (write on define/delete).\nSinónimos: reglas de calidad, validar datos, comprobar datos.",
         QualityArgs, _ann(False, False, False), _run_quality),
    Tool("data_chart", "Build and save a chart from a dataset (aggregated in SQL); image=true also returns a PNG (write).\n"
         "Sinónimos: crear gráfico, gráfica de barras, histograma, dispersión, mapa de calor.",
         ChartArgs, _ann(False, False, False), _run_chart),
    Tool("data_dashboard", "Create a dashboard, add a chart/KPI item to it, or list/get dashboards (write on create/add).\n"
         "Sinónimos: panel de control, cuadro de mando, dashboard.",
         DashboardArgs, _ann(False, False, False), _run_dashboard),
    Tool("data_model", "Train/evaluate a quick supervised model for a target column, or list saved models (write on train).\n"
         "Sinónimos: entrenar modelo, predecir, clasificación, regresión, importancia de variables.",
         ModelTrainArgs, _ann(False, False, False), _run_model),
    Tool("data_cluster", "K-means clustering with an elbow/silhouette scan; writes cluster labels back (write).\n"
         "Sinónimos: agrupar datos, clustering, segmentación, k-means.",
         ClusterArgs, _ann(False, False, False), _run_cluster),
    Tool("data_forecast", "Time-series forecast with a confidence interval (write). Resamples onto a regular "
         "calendar grain first (freq: auto/day/week/month/quarter — auto picks by span/density so gappy, "
         "irregular daily data aggregates to a coarser series that Holt-Winters/ETS can actually fit), and "
         "prefers Holt-Winters/ETS whenever statsmodels is available and the resampled series is long enough, "
         "falling back to a seasonal-naive method otherwise — the result always reports resampled_to, method "
         "and why (set on a fallback).\n"
         "Sinónimos: pronóstico, previsión, serie temporal, predecir el futuro.",
         ForecastArgs, _ann(False, False, False), _run_forecast),
    Tool("data_export", "Export a dataset to CSV/XLSX/Parquet/JSON inside the app's exports folder (write).\n"
         "Sinónimos: exportar datos, descargar csv, guardar excel.",
         ExportArgs, _ann(False, False, False), _run_export),
    Tool("data_log", "Search the analysis log (every operation, with its N-000123 id, input and result).\n"
         "Sinónimos: historial, registro de análisis, qué se ha hecho.",
         LogArgs, _ann(True), _run_log),
    Tool("data_ask", "Ask a question in plain language; a shared model writes one SQL query, shown before running "
         "(needs a resolved language model).\nSinónimos: pregunta a mis datos, ask my data, analiza esto por mí.",
         AskArgs, _ann(True), _run_ask),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}
assert len(TOOLS) <= 18, "keep the MCP surface at or under 18 tools"


def tool_catalog() -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "annotations": t.annotations,
         "inputSchema": t.input_model.model_json_schema(by_alias=True)}
        for t in TOOLS
    ]


async def call_tool(services: Services, name: str, arguments: dict | None) -> Any:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool: {name}")
    args = tool.input_model.model_validate(arguments or {})
    result = tool.run(services, args)
    if inspect.isawaitable(result):
        result = await result
    return result
