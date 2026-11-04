"""Tools exposed to the assistant. One list drives /api/agent/* and mcp_server.py.

Descriptions start with a plain-language summary and end with EN/ES keyword
hints, as required by the family contract. Every tool proxies straight to a
`Services` method: this file only validates input and shapes the call.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from .hoard_link import agentkit
from .hoard_link.agentkit import Empty, Tool, ann as _ann, cap_result
from .services import Services
from .texts import use_lang

AGENT_INSTRUCTIONS = (
    "Nightingale's Hoard is the user's own data workbench: ingest files/folders/URLs/pasted text into a local "
    "DuckDB workbench, clean data with versioned steps (undo/redo, lineage, recipe export/replay), define and run "
    "quality rules, build charts and dashboards, train quick models (supervised/clustering/PCA/anomaly/forecast), "
    "and query everything read-only with SQL. Analysis operations are recorded in the log with an id like "
    "'N-000123'; read-only help lookups do not create log entries. Cite an analysis id when reporting its result. Always preview a transform "
    "(data_transform with preview=true) before applying it when the change is not obviously safe. Never assume a "
    "dataset name: call data_list first if unsure. This app never reads files or the database directly outside "
    "these tools. Join coverage (data_join_preview) reads current saved versions without changing versions or creating an analysis record."
)


class IngestArgs(BaseModel):
    action: Literal["ingest", "delete"] = Field(
        "ingest", description="'ingest' (default) loads a new dataset. 'delete' permanently removes an existing "
                               "one (its versions, quality rules, charts and models) — pass `name` as the dataset "
                               "to delete; other ingest-only fields are ignored.")
    kind: Literal["file", "folder", "url", "paste", "hoard"] = Field(
        "file", description="What to ingest (action=ingest). 'hoard' = the records another Hoard app returns from one of "
                             "its tools, called through the hub: give `preset` (ledger_transactions, phileas_shipments, "
                             "argus_app_time [tool name unverified]) or `app` + `tool` (+ `args`, `list_path`).")
    path: Optional[str] = Field(None, description="Absolute path to a file or folder (kind=file/folder).")
    url: Optional[str] = Field(None, description="http(s) URL to a CSV/JSON resource (kind=url).")
    text: Optional[str] = Field(None, description="Pasted CSV/JSON text (kind=paste).")
    name: Optional[str] = Field(None, max_length=200, description=(
        "Dataset name. For action=ingest, derived from the source if omitted; for action=delete, the dataset to "
        "delete (required)."))
    fmt: Literal["csv", "json"] = Field("csv", description="Format for kind=url/paste.")
    glob: str = Field("*.csv", description="File pattern for kind=folder, e.g. '*.csv' or '*.parquet'.")
    app: Optional[str] = Field(None, max_length=60, description="kind=hoard: the app id, e.g. 'ledger', 'phileas'.")
    tool: Optional[str] = Field(None, max_length=100, description="kind=hoard: the tool of that app, e.g. 'list_entries'.")
    args: Optional[dict[str, Any]] = Field(None, description="kind=hoard: arguments for that tool (a preset brings its own).")
    list_path: Optional[str] = Field(None, max_length=200, description=(
        "kind=hoard: where the list of records is in the answer, e.g. 'items' or 'data.rows'. Omitted: it is found "
        "by itself. A path to an object of objects gives one row per key."))
    preset: Optional[str] = Field(None, max_length=60, description=(
        "kind=hoard: ledger_transactions (every entry, amounts in cents), phileas_shipments (delivered parcels with "
        "transit_days), argus_app_time (screen time per app; unverified tool name). Re-running a hoard ingest of the "
        "same dataset adds a new version (undo goes back); data_refresh rebuilds it and replays the recipe."))
    options: dict[str, Any] = Field(default_factory=dict, description=(
        "delimiter, header, encoding (utf-8/utf-16/latin-1), date_format, decimal_separator, thousands_separator, "
        "sheet/sheets/skip_rows (Excel), tables (SQLite), flatten (JSON); kind=hoard: paginate {size, max_rows}, "
        "max_rows, epoch_columns (suffix of columns holding epoch seconds), derive [{name, from, to}] (days between two epoch columns)."))
    force: bool = Field(False, description="action=delete only: delete even if charts/dashboards/models depend on "
                                             "this dataset (otherwise the call fails and lists them).")


class RefreshArgs(BaseModel):
    dataset: str = Field(..., description="Dataset name.")
    check_quality: bool = Field(True, description="Run saved quality rules after refresh and include any failures. Set false to skip.")


class DatasetArgs(BaseModel):
    dataset: str


class ProfileArgs(BaseModel):
    dataset: str
    version: Optional[int] = None
    mode: Literal["profile", "eda", "quality_score", "drift", "frequencies"] = Field(
        "profile", description="'profile' (default): the original per-column profile. 'eda': correlation "
                                 "(Pearson+Spearman), null co-occurrence, outliers (IQR+z-score), and encoding "
                                 "suggestions, plus the quality score. 'quality_score': just the 0-100 score and its "
                                 "components. 'drift': compare numeric-column distributions against `other_dataset` "
                                 "(KS test, PSI, Wasserstein distance, mean/sd shift). 'frequencies': exact counts "
                                 "for column in the selected saved version, including NULL, paged by offset/limit. "
                                 "Values are full DuckDB VARCHAR strings, not original binary values; percent uses all rows.")
    column: Optional[str] = Field(None, description="mode='frequencies': exact column name.")
    offset: int = Field(0, ge=0, description="mode='frequencies': group offset in count-descending, value-ascending order.")
    limit: int = Field(50, ge=1, le=1000, description="mode='frequencies': groups per page, using the existing preview page range.")
    other_dataset: Optional[str] = Field(None, description="mode='drift' only: the dataset (or slice, as its own "
                                          "dataset) to compare distributions against.")
    columns: Optional[list[str]] = Field(None, description="mode='drift' only: restrict to these shared columns.")


class PreviewArgs(BaseModel):
    dataset: str
    version: Optional[int] = None
    limit: int = Field(50, ge=1, le=1000)


class CompareVersionsArgs(BaseModel):
    dataset: str
    version_a: int = Field(0, ge=0, description="Version A, default original import (0).")
    version_b: Optional[int] = Field(None, ge=0, description="Version B, default current version.")


class CompareRowsArgs(CompareVersionsArgs):
    columns: Optional[list[str]] = Field(None, description="Same-name, same-type columns to compare; omitted means all eligible columns. An empty list compares none.")
    offset_a: int = Field(0, ge=0, description="Page offset for rows only in A.")
    offset_b: int = Field(0, ge=0, description="Page offset for rows only in B.")
    limit: int = Field(50, ge=1, le=1000, description="Rows per page, using the existing dataset preview range.")


class TransformArgs(BaseModel):
    dataset: Optional[str] = Field(None, description="Required for a transform. With op='help', optionally name a dataset to include its current column names/types, version, and row count (no cell values).")
    op: str = Field(..., description="filter/select/drop/rename/cast/fill_null/drop_duplicates/derive/split_column/"
                                        "text/replace/bin/date_parts/group/pivot/unpivot/join/union/sort/sample/window/sql. "
                                        "Use op='help' with help_for='<operation>' for its exact read-only contract, or omit help_for for the full catalog.")
    help_for: Optional[str] = Field(None, description="With op='help', filter the read-only contract lookup to one transform operation. Add dataset when choosing fields so its current schema is included.")
    params: dict[str, Any] = Field(default_factory=dict, description="Operation-specific JSON object; use op='help' to look up its exact fields and nested shapes.")
    preview: bool = Field(True, description="true: show the effect without applying; false: apply and create a new version.")


class JoinKeyPair(BaseModel):
    left: str = Field(..., min_length=1, description="Exact column name in the left dataset.")
    right: str = Field(..., min_length=1, description="Exact column name in the right dataset.")


class JoinPreviewArgs(BaseModel):
    dataset: str = Field(..., min_length=1, description="Existing left dataset name; reads its current saved version.")
    other_dataset: str = Field(..., min_length=1, description="Existing right dataset name; reads its current saved version.")
    on: list[JoinKeyPair] = Field(..., min_length=1, description="Equality key pairs combined with AND, preserving exact names. NULL does not match NULL.")


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
    action: Literal["create", "get"] = Field("create", description="create saves a new chart; get reads an existing chart without creating one.")
    chart_id: Optional[int] = Field(None, description="Saved chart ID for action=get.")
    dataset: Optional[str] = Field(None, description="Required for action=create.")
    kind: Optional[str] = Field(None, description="Required for create: bar/grouped_bar/stacked_bar/line/area/scatter/histogram/box/heatmap/pie/donut")
    x: Optional[str] = None
    y: Optional[str] = None
    agg: str = Field("sum", description="sum/avg/count/min/max/median")
    color: Optional[str] = Field(None, description="series/group column, or the value column for heatmap.")
    filter: Optional[str] = Field(None, description="A SQL WHERE fragment.")
    title: str = ""
    name: Optional[str] = None
    image: bool = Field(False, description="true also returns a base64 PNG; keep false unless an image was asked for.")
    lang: str = Field("en", description="action=get: PNG number locale (es or en). Reads the dataset's current version, not historical pixels.")


class DashboardArgs(BaseModel):
    action: Literal["create", "add", "list", "get",
                     "semantic_get", "semantic_put", "semantic_suggest",
                     "code_list", "code_get", "code_put", "code_validate", "code_render", "code_export",
                     "apply_action", "undo", "redo", "history", "action_schemas"] = Field(
        "list", description="Item-based dashboards (original): create/add/list/get. Typed reversible edits: "
                              "'apply_action' (one `change` object: add_item/remove_item/move_item/update_item/set_name/"
                              "set_filter/set_layout; see 'action_schemas' for the JSON schemas), 'undo'/'redo' (step "
                              "through the dashboard's action log), 'history' (the log, its initial state, can_undo/can_redo). "
                              "Dashboards as code: "
                              "'semantic_get'/'semantic_put' (read/write the semantic layer YAML; put also "
                              "validates), 'semantic_suggest' (propose a semantic model from a dataset's columns), "
                              "'code_list'/'code_get' (list/read a code dashboard YAML), 'code_put' (save one, "
                              "returns validation issues with a hint per issue — fix and retry), 'code_validate' "
                              "(check a dashboard's YAML, or `text` before saving it, without saving), "
                              "'code_render' (run it and return each widget's data/SQL/errors), 'code_export' "
                              "(best-effort copy into an item-based dashboard so the plain Dashboards page can "
                              "show it too).")
    dashboard_id: Optional[int] = None
    name: Optional[str] = None
    item: Optional[dict[str, Any]] = Field(None, description='{"type":"chart","chart_id":N} or {"type":"kpi","dataset":...,"expr":"AVG(x)","label":...}')
    items: Optional[list[dict[str, Any]]] = None
    filter: Optional[dict[str, Any]] = None
    change: Optional[dict[str, Any]] = Field(None, description='apply_action: the typed action, e.g. {"type":"move_item",'
                                              '"item_id":"i2","to_index":0} or {"type":"update_item","item_id":"i1",'
                                              '"set":{"expr":"AVG(x)"}} (ids come from get/history; schemas from action_schemas).')
    limit: Optional[int] = Field(None, ge=1, le=500, description="history: how many of the latest actions to return.")
    slug: Optional[str] = Field(None, description="code_get/code_put/code_validate/code_render/code_export: the "
                                  "code dashboard's slug (from code_list). Omit on code_put to create a new one "
                                  "from `name`.")
    text: Optional[str] = Field(None, description="semantic_put: the semantic layer YAML. code_put/code_validate: "
                                  "the dashboard YAML.")
    dataset: Optional[str] = Field(None, description="semantic_suggest: the dataset to propose a semantic model for.")
    filters: Optional[dict[str, Any]] = Field(None, description="code_render/code_export: filter values keyed by "
                                                "the dashboard's declared filter names (defaults are used for the rest).")


class ModelTrainArgs(BaseModel):
    action: Literal["train", "list", "backends", "registry", "evaluate", "tune", "explain", "optimize",
                     "pareto", "compare", "report"] = Field(
        "train", description="'train': fit a model. 'list': the original quick-model list. 'backends': list Lab "
                               "registry backends (and whether optional ones like xgboost/lightgbm are installed). "
                               "'registry': list/get/delete a saved Lab model (see registry_action). 'evaluate': "
                               "diagnostics for a saved model on an eval dataset (or in-sample). 'tune': "
                               "hyperparameter search, saves the tuned model. 'explain': global + per-row feature "
                               "importance. 'optimize': suggest input values that maximize/minimize the target. "
                               "'pareto': non-dominated front across 2-3 models' predictions. 'compare': curve/series "
                               "comparison (with x/y/group), or model_ids to compare saved models' metrics side by "
                               "side. 'report': render a PDF report (profile + quality + model + diagnostics); "
                               "with only model_id it covers the dataset the model was trained on.")
    dataset: Optional[str] = None
    target: Optional[str] = None
    features: Optional[list[str]] = None
    task: Optional[Literal["regression", "classification"]] = None
    algorithm: Optional[str] = Field(None, description="train (lab=false): linear/logistic/random_forest/"
                                       "gradient_boosting. train (lab=true) / tune: any Lab registry backend — see "
                                       "action='backends' (ridge/random_forest/gradient_boosting/extra_trees/knn/"
                                       "mlp/gaussian_process/linear/logistic, plus xgboost/lightgbm if installed).")
    lab: bool = Field(False, description="action='train' only: true trains through the Lab registry (more "
                                           "backends, and — unlike the original path — saves the model artifact so "
                                           "it can later be evaluate/tune/explain/optimize'd); false (default) keeps "
                                           "the original data_model behaviour completely unchanged.")
    test_size: float = Field(0.2, gt=0, lt=0.9)
    seed: int = 42
    write_to: Literal["new_dataset", "new_version", "none"] = Field(
        "new_dataset", description="'new_dataset' (default) puts predictions in their own new dataset "
                                     "(source untouched); 'new_version' adds a predicted_<target> column to a new "
                                     "version of the source itself (never changes an existing column's type); "
                                     "'none' skips writing them anywhere. optimize/pareto also accept 'new_dataset'/"
                                     "'none' for their suggested points / Pareto front.")
    name: Optional[str] = None
    model_id: Optional[int] = Field(None, description="registry get/delete, evaluate, explain, optimize, report: "
                                      "the saved Lab model to act on.")
    model_ids: Optional[list[int]] = Field(None, description="compare (registry side-by-side) / pareto: several "
                                             "saved Lab models at once.")
    eval_dataset: Optional[str] = Field(None, description="evaluate/report: dataset to score against; default is "
                                          "the model's own training dataset (an in-sample check, clearly labeled).")
    date_col: Optional[str] = Field(None, description="evaluate: rolling-mean bias-over-time by this date column.")
    group_col: Optional[str] = Field(None, description="evaluate: error broken down by this categorical column.")
    x: Optional[str] = Field(None, description="compare (curve comparison): the series' x column.")
    y: Optional[str] = Field(None, description="compare (curve comparison): the series' y column.")
    group: Optional[str] = Field(None, description="compare (curve comparison): optional grouping column.")
    row_index: Optional[int] = Field(None, description="explain: also explain this one row individually.")
    registry_action: Optional[Literal["list", "get", "delete"]] = Field(
        None, description="action='registry': which operation; default 'list'.")
    params: dict[str, Any] = Field(default_factory=dict, description=(
        "Action-specific extras. tune: backend goes in `algorithm`; params={param_space, n_trials, timeout, cv}. "
        "explain: {sample_size, seed}. optimize: {direction, bounds, fixed, integer_features, categorical_features, "
        "constraints, acquisition, n_candidates, batch_size}. pareto: {directions (required, one per model_id), "
        "bounds, fixed, n_candidates}. report: {optimize: true/false, optimize_params}."))


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


class WorkbookUpdateArgs(BaseModel):
    dataset: str
    version: Optional[int] = Field(None, ge=0)


class ExportArgs(BaseModel):
    dataset: str
    format: Literal["csv", "xlsx", "parquet", "json"] = "csv"
    path: Optional[str] = Field(None, description="Filename inside the app's exports folder; derived if omitted.")
    mode: Literal["flat", "preserve_workbook", "inventory"] = Field(
        "flat", description="'flat' makes the usual single-table export. 'inventory' reads the linked workbook snapshot. 'preserve_workbook' is .xlsx-only: patch the selected imported sheet's tabular cells in a copy of its original workbook, preserving untouched package parts.")
    version: Optional[int] = Field(None, ge=0, description="Dataset version to export; defaults to the current version.")
    workbook_updates: Optional[list[WorkbookUpdateArgs]] = Field(None, description="Additional dataset/version pairs from the same source workbook; all selected sheets are preflighted and written into the same .xlsx copy.")


class LogArgs(BaseModel):
    q: Optional[str] = Field(None, description="Free text, or an id like 'N-000123'.")
    source: Optional[Literal["ui", "agent"]] = None
    dataset: Optional[str] = None
    limit: int = Field(50, ge=1, le=200)


class AskArgs(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    datasets: Optional[list[str]] = Field(None, description="Restrict to these datasets; default is every registered one.")


def _run_ingest(s: Services, a: IngestArgs) -> dict:
    if a.action == "delete":
        if not a.name:
            raise ValueError("name is required for action=delete")
        return s.dataset_delete(a.name, a.force, source="agent")
    if a.kind == "hoard":
        return s.ingest_hoard(a.app, a.tool, a.args, a.list_path, a.name, a.preset, a.options, source="agent")
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
    return s.refresh(a.dataset, source="agent", check_quality=a.check_quality)


def _run_list(s: Services, _: Empty) -> dict:
    return s.list_datasets()


def _run_profile(s: Services, a: ProfileArgs) -> dict:
    if a.mode == "frequencies":
        if a.column is None:
            raise ValueError("mode='frequencies' needs column")
        return s.column_frequencies(a.dataset, a.column, a.version, a.offset, a.limit)
    if a.mode == "profile":
        return s.profile(a.dataset, a.version)
    if a.mode == "eda":
        return s.lab_eda_profile(a.dataset, source="agent")
    if a.mode == "quality_score":
        return s.lab_quality_score(a.dataset, source="agent")
    if a.mode == "drift":
        if not a.other_dataset:
            raise ValueError("mode='drift' needs other_dataset")
        return s.lab_drift(a.dataset, a.other_dataset, a.columns, source="agent")
    raise ValueError(f"unknown mode: {a.mode}")


def _run_preview(s: Services, a: PreviewArgs) -> dict:
    return s.preview(a.dataset, a.version, a.limit)


def _run_transform(s: Services, a: TransformArgs) -> dict:
    if a.op == "help":
        if a.params:
            raise ValueError("op='help' uses help_for and does not accept transform params")
        return s.transform_operations(a.help_for, a.dataset)
    if a.help_for is not None:
        raise ValueError("help_for is only valid with op='help'")
    if not a.dataset:
        raise ValueError("dataset is required for a transform; omit it only with op='help'")
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
    if a.action == "get":
        if a.chart_id is None:
            raise ValueError("get needs chart_id")
        return s.chart_get(a.chart_id, a.image, lang=a.lang)
    if not a.dataset or not a.kind:
        raise ValueError("create needs dataset and kind")
    return s.chart_create(a.dataset, a.kind, a.x, a.y, a.agg, a.color, a.filter, a.title, a.name, a.image, source="agent")


def _run_dashboard(s: Services, a: DashboardArgs) -> dict:  # noqa: C901 - one dispatcher keeps the 18-tool cap
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
    if a.action in ("apply_action", "undo", "redo", "history"):
        if not a.dashboard_id:
            raise ValueError(f"{a.action} needs dashboard_id")
        if a.action == "apply_action":
            if not a.change:
                raise ValueError("apply_action needs `change` (call action_schemas for the shapes)")
            return s.dashboard_apply(a.dashboard_id, a.change, source="agent")
        if a.action == "undo":
            return s.dashboard_undo(a.dashboard_id, source="agent")
        if a.action == "redo":
            return s.dashboard_redo(a.dashboard_id, source="agent")
        return s.dashboard_history(a.dashboard_id, a.limit or 100)
    if a.action == "action_schemas":
        return s.dashboard_action_schemas()
    if a.action == "semantic_get":
        return s.dac_semantic_get()
    if a.action == "semantic_put":
        if not a.text:
            raise ValueError("semantic_put needs text")
        return s.dac_semantic_put(a.text, source="agent")
    if a.action == "semantic_suggest":
        if not a.dataset:
            raise ValueError("semantic_suggest needs dataset")
        return s.dac_semantic_suggest(a.dataset, source="agent")
    if a.action == "code_list":
        return s.dac_list()
    if a.action == "code_get":
        if not a.slug:
            raise ValueError("code_get needs slug")
        return s.dac_get(a.slug)
    if a.action == "code_put":
        if not a.text:
            raise ValueError("code_put needs text")
        return s.dac_put(a.text, a.slug, source="agent")
    if a.action == "code_validate":
        return s.dac_validate(a.slug, a.text)
    if a.action == "code_render":
        if not a.slug:
            raise ValueError("code_render needs slug")
        return s.dac_render(a.slug, a.filters)
    if a.action == "code_export":
        if not a.slug:
            raise ValueError("code_export needs slug")
        return s.dac_export(a.slug, a.filters, source="agent")
    return s.dashboard_list()


def _run_model(s: Services, a: ModelTrainArgs) -> dict:  # noqa: C901 - one dispatcher keeps the 18-tool cap
    if a.action == "list":
        return s.model_list(a.dataset)
    if a.action == "backends":
        return s.lab_backends(a.task)
    if a.action == "registry":
        sub = a.registry_action or "list"
        if sub == "list":
            return s.lab_registry_list(a.dataset)
        if sub == "get":
            if not a.model_id:
                raise ValueError("registry_action='get' needs model_id")
            return s.lab_registry_get(a.model_id)
        if sub == "delete":
            if not a.model_id:
                raise ValueError("registry_action='delete' needs model_id")
            return s.lab_registry_delete(a.model_id, source="agent")
        raise ValueError(f"unknown registry_action: {sub}")
    if a.action == "compare":
        if a.model_ids:
            return s.lab_registry_compare(a.model_ids)
        if not (a.dataset and a.x and a.y):
            raise ValueError("compare needs either model_ids (registry comparison) or dataset/x/y (curve comparison)")
        return s.lab_compare_curves(a.dataset, a.x, a.y, a.group, source="agent")
    if a.action == "evaluate":
        if not a.model_id:
            raise ValueError("evaluate needs model_id")
        return s.lab_model_evaluate(a.model_id, a.eval_dataset, a.date_col, a.group_col, source="agent")
    if a.action == "tune":
        if not (a.dataset and a.target):
            raise ValueError("tune needs dataset and target")
        p = a.params
        return s.lab_model_tune(a.dataset, a.target, a.features, a.task, a.algorithm or "random_forest",
                                 p.get("param_space"), int(p.get("n_trials", 20)), p.get("timeout"),
                                 int(p.get("cv", 5)), a.seed, a.test_size, a.name, source="agent")
    if a.action == "explain":
        if not a.model_id:
            raise ValueError("explain needs model_id")
        p = a.params
        return s.lab_model_explain(a.model_id, a.dataset, int(p.get("sample_size", 200)), a.row_index,
                                    int(p.get("seed", a.seed)), source="agent")
    if a.action == "optimize":
        if not a.model_id:
            raise ValueError("optimize needs model_id")
        p = a.params
        return s.lab_model_optimize(a.model_id, p.get("direction", "maximize"), p.get("bounds"), p.get("fixed"),
                                     p.get("integer_features"), p.get("categorical_features"), p.get("constraints"),
                                     p.get("acquisition", "ei"), int(p.get("n_candidates", 3000)),
                                     int(p.get("batch_size", 5)), a.seed, p.get("write_to", a.write_to),
                                     source="agent")
    if a.action == "pareto":
        if not a.model_ids:
            raise ValueError("pareto needs model_ids")
        p = a.params
        directions = p.get("directions")
        if not directions:
            raise ValueError("pareto needs params.directions (one 'maximize'/'minimize' per model_id)")
        return s.lab_pareto(a.model_ids, directions, p.get("bounds"), p.get("fixed"),
                             int(p.get("n_candidates", 1000)), a.seed, p.get("write_to", a.write_to), source="agent")
    if a.action == "report":
        dataset = a.dataset
        if not dataset and a.model_id:
            # A report about a model defaults to the dataset it was trained on.
            dataset = str(s.lab_registry_get(a.model_id)["dataset_id"])
        if not dataset:
            raise ValueError("report needs dataset or model_id")
        p = a.params
        return s.lab_report(dataset, a.model_id, a.eval_dataset, bool(p.get("optimize")),
                             p.get("optimize_params"), source="agent")
    # action == "train"
    if not (a.dataset and a.target):
        raise ValueError("train needs dataset and target")
    if a.lab:
        return s.lab_model_train(a.dataset, a.target, a.features, a.task, a.algorithm or "random_forest",
                                  a.test_size, a.seed, a.write_to, a.name, source="agent")
    return s.model_train(a.dataset, a.target, a.features, a.task, a.algorithm, a.test_size, a.seed,
                          a.write_to, a.name, source="agent")


def _run_cluster(s: Services, a: ClusterArgs) -> dict:
    return s.model_cluster(a.dataset, a.features, a.k, a.seed, a.write_to, a.name, source="agent")


def _run_forecast(s: Services, a: ForecastArgs) -> dict:
    return s.model_forecast(a.dataset, a.date_col, a.value_col, a.horizon, a.seasonal_period, a.name,
                             a.write_to, source="agent", freq=a.freq)


def _run_export(s: Services, a: ExportArgs) -> dict:
    updates = [item.model_dump(exclude_none=True) for item in a.workbook_updates] if a.workbook_updates is not None else None
    return s.export(a.dataset, a.format, a.path, source="agent", mode=a.mode, version=a.version,
                    workbook_updates=updates)


def _run_log(s: Services, a: LogArgs) -> dict:
    return s.log_search(a.q, a.source, a.dataset, a.limit)


def _run_ask(s: Services, a: AskArgs):
    return s.ask(a.question, a.datasets, source="agent")


class WorkbookArgs(BaseModel):
    action: Literal["compile", "list", "get", "evaluate", "check", "verify", "trace", "export", "delete",
                    "sensitivity", "scenarios", "units"] = Field(
        "list", description="compile a .xlsx/.xlsm (path); list/get compiled workbooks; evaluate with new parameter "
        "values (inputs); check = recompute and compare every formula with the value Excel saved; verify = new seeded "
        "cases recalculated by Excel (COM) or LibreOffice and compared result by result; trace a result back to its "
        "parameters (cell); export a standalone Python module; delete; sensitivity = rank the numeric parameters by how much "
        "they move an output (tornado); scenarios = named sets of parameter values saved with the workbook (op); "
        "units = declare units of input cells and get the inferred unit of every formula plus unit errors.")
    path: Optional[str] = Field(None, description="compile: workbook path (absolute, or relative to the data folder)")
    name: Optional[str] = Field(None, description="compile: display name")
    workbook_id: Optional[str] = Field(None, description="id returned by compile/list")
    inputs: Optional[dict[str, Any]] = Field(None, description="evaluate: {cell address like 'Hoja!B3', label or defined name: value}. "
                                             "Errors as {\"error\": \"#N/A\"}; dates as Excel serial numbers.")
    as_of: Optional[str] = Field(None, description="evaluate: ISO date-time used by TODAY()/NOW()")
    cell: Optional[str] = Field(None, description="trace: cell address or label")
    engine: Optional[Literal["excel", "libreoffice"]] = Field(None, description="verify: reference engine (default: Excel if installed)")
    cases: Optional[list[dict[str, Any]]] = Field(None, description="verify: explicit cases ({address: value}); default seeded random cases")
    n: int = Field(12, ge=1, le=200, description="verify: number of generated cases")
    seed: int = Field(7, description="verify: seed of the generated cases")
    output: Optional[str] = Field(None, description="sensitivity: result cell address or label (default: every labelled result)")
    delta: float = Field(0.1, gt=0, description="sensitivity: relative change applied to each parameter, 0.1 = ±10%")
    steps: Optional[dict[str, float]] = Field(None, description="sensitivity: absolute step per parameter {cell or label: step}; "
                                              "overrides delta for that parameter")
    op: Literal["list", "save", "delete", "compare"] = Field("list", description="scenarios: list, save (scenario + inputs), "
                                                             "delete (scenario), compare (scenario_names, default all)")
    scenario: Optional[str] = Field(None, description="scenarios save/delete: scenario name (inputs = its parameter values)")
    scenario_names: Optional[list[str]] = Field(None, description="scenarios compare: which scenarios (default all)")
    note: Optional[str] = Field(None, description="scenarios save: free text")
    units: Optional[dict[str, Optional[str]]] = Field(None, description="units: {input cell or label: unit, e.g. 'EUR/h', 'h', 'm', '%'}; "
                                                      "null removes. Omit to only report. Currency symbols €/$ are read from number formats.")
    format: Literal["python", "app"] = Field("python", description="export: python is the existing calculation module; app is one self-contained Python web calculator with forms, fonts and results, standard library only")


def _run_workbook(s: Services, a: WorkbookArgs) -> dict:
    from .sheets.store import WorkbookStore
    st = WorkbookStore(s.config.data_dir)

    def need() -> str:
        if not a.workbook_id:
            raise ValueError("workbook_id is required")
        return a.workbook_id

    if a.action == "compile":
        if not a.path:
            raise ValueError("path is required to compile")
        path = st.user_path(s.config.data_dir, a.path)
        r = st.compile(path, a.name, original=str(path))
        r.pop("cached_check", None)
        return r
    if a.action == "list":
        from .sheets.reference import engines
        return {"workbooks": st.list(), "engines": engines()}
    if a.action == "get":
        return st.get(need())
    if a.action == "evaluate":
        return st.evaluate(need(), a.inputs, a.as_of)
    if a.action == "check":
        return st.check(need())
    if a.action == "verify":
        return st.verify(need(), engine=a.engine, cases=a.cases, n=a.n, seed=a.seed)
    if a.action == "trace":
        if not a.cell:
            raise ValueError("cell is required to trace")
        return st.trace(need(), a.cell)
    if a.action == "export":
        return st.export(need(), format=a.format)
    if a.action in ("sensitivity", "scenarios", "units"):
        from .sheets import explain_ops
        return explain_ops.run(s, st, need(), a, source="agent")
    return st.delete(need())


class ReconcilePair(BaseModel):
    left: str
    right: str


class ReconcileArgs(BaseModel):
    action: Literal["suggest", "run"] = Field(
        "suggest", description="suggest = profile both datasets and propose column correspondences and key candidates "
        "with their evidence (nothing applied); run = reconcile on the keys you choose.")
    left: str = Field(..., description="left dataset name")
    right: str = Field(..., description="right dataset name")
    keys: Optional[list[ReconcilePair]] = Field(None, description="run: key column pairs; keys are never guessed")
    compare: Optional[list[ReconcilePair]] = Field(None, description="run: column pairs to compare cell by cell "
                                                   "(default: same-named non-key columns)")
    options: Optional[dict[str, Any]] = Field(None, description="run: trim, case_insensitive, numeric, empty_as_missing "
                                              "(key normalisation, all off by default) and tolerance (numbers)")
    sample: int = Field(10, ge=0, le=200, description="run: example rows per bucket")
    save_as: Optional[str] = Field(None, description="run: save every row with its bucket as a new dataset")


def _run_reconcile(s: Services, a: ReconcileArgs) -> dict:
    from . import reconcile as rec
    if a.action == "suggest":
        return rec.suggest_datasets(s, a.left, a.right, source="agent")
    if not a.keys:
        raise ValueError("keys are required to reconcile; use action='suggest' to see candidates")
    compare = [p.model_dump() for p in a.compare] if a.compare is not None else None
    return rec.reconcile_datasets(s, a.left, a.right, [p.model_dump() for p in a.keys], compare, a.options,
                                  a.sample, a.save_as, source="agent")


class ScenarioArgs(BaseModel):
    action: Literal["save", "list", "get", "delete", "run", "compare", "sweep", "calibrate"] = Field(
        "list", description="save/list/get/delete = stored models by name; run = simulate; compare = named variants "
        "against the base with common random numbers; sweep = one parameter over a list of values; calibrate = "
        "fit a distribution from a dataset column.")
    name: Optional[str] = Field(None, description="saved model name (save/get/delete; run/compare/sweep may use it "
                                "instead of an inline model)")
    model: Optional[dict[str, Any]] = Field(None, description="model JSON: horizon_hours, warmup_hours, replications, "
                                            "seed, arrivals {type: poisson|schedule|empirical, rate|rates|values}, "
                                            "job_types [{name, mix, service_scale}], stations [{name, servers, "
                                            "service {dist: fixed|exponential|uniform|triangular|lognormal|empirical}, "
                                            "shifts, buffer, on_full: lose|block}], costs {server_hour, waiting_hour, "
                                            "lost_job, holding_unit_hour, order, stockout_event}, inventory {station, "
                                            "initial, reorder_point, order_quantity, per_job, lead_time}. Times in "
                                            "hours. An empirical distribution may be {dist:'empirical', dataset, "
                                            "column, unit}.")
    variants: Optional[list[dict[str, Any]]] = Field(None, description="compare: [{name, overrides: {path: value}}], "
                                                     "paths like stations.Packing.servers or arrivals.rate")
    param: Optional[str] = Field(None, description="sweep: override path to vary")
    values: Optional[list[Any]] = Field(None, description="sweep: values for param (max 40)")
    replications: Optional[int] = Field(None, ge=1, le=200)
    seed: Optional[int] = None
    horizon_hours: Optional[float] = Field(None, gt=0, description="run: override the model horizon")
    dataset: Optional[str] = Field(None, description="calibrate: dataset name")
    column: Optional[str] = Field(None, description="calibrate: column")
    kind: Literal["service", "interarrival", "timestamps"] = Field("service", description="calibrate: column holds "
                                                                  "service durations, inter-arrival durations or "
                                                                  "arrival timestamps")
    unit: str = Field("hours", description="calibrate: unit of duration columns (hours, minutes, seconds, days)")
    save_as: Optional[str] = Field(None, description="run/compare/sweep: also save the results as a dataset")


def _run_scenario(s: Services, a: ScenarioArgs) -> dict:
    from . import scenarios as sc
    need = lambda v, what: v if v else (_ for _ in ()).throw(ValueError(f"{what} is required for {a.action}"))  # noqa: E731
    if a.action == "list":
        return sc.list_models(s)
    if a.action == "get":
        return sc.get_model(s, need(a.name, "name"))
    if a.action == "save":
        return sc.save_model(s, need(a.name, "name"), need(a.model, "model"), source="agent")
    if a.action == "delete":
        return sc.delete_model(s, need(a.name, "name"), source="agent")
    if a.action == "run":
        return sc.run_scenario(s, a.name, a.model, a.replications, a.seed, a.horizon_hours, a.save_as, source="agent")
    if a.action == "compare":
        return sc.compare_scenarios(s, a.name, a.model, need(a.variants, "variants"), a.replications, a.seed,
                                    a.save_as, source="agent")
    if a.action == "sweep":
        return sc.sweep_scenario(s, a.name, a.model, need(a.param, "param"), need(a.values, "values"),
                                 a.replications, a.seed, a.save_as, source="agent")
    return sc.calibrate_scenario(s, need(a.dataset, "dataset"), need(a.column, "column"), a.kind, a.unit, source="agent")
class FabQuoteArgs(BaseModel):
    action: Literal["templates", "vulcan", "create", "list", "get", "delete", "set", "options", "scenario_save",
                    "scenario_delete", "compare", "sensitivity", "actual", "actuals", "actual_delete", "export"] = Field(
        "list", description="templates = processes with their parameters; vulcan = fetch measurements of a library model "
        "(preview, nothing saved); create = new quote (process + measurements/parameters/rate_card/vulcan_model_id); "
        "list/get/delete = stored quotes by name; set = change base parameters (reports the items that changed and why); "
        "options = volume_only policy; scenario_save/scenario_delete = named assumption variations; compare = scenarios "
        "against the base with diffs; sensitivity = one-at-a-time +-pct on parameters; actual = record what a line item "
        "really cost or took; actuals = actual versus quote per item; actual_delete; export = json, csv or markdown.")
    name: Optional[str] = Field(None, description="quote name")
    process: Optional[Literal["fdm", "cnc_laser", "concrete"]] = Field(None, description="create: manufacturing process template")
    measurements: Optional[dict[str, Any]] = Field(None, description="create: measured values {parameter: number | "
                                                   "{value, unit}}, e.g. volume_cm3, surface_cm2, part_height_mm, cut_length_mm. "
                                                   "Missing ones stay missing and block the items that need them")
    parameters: Optional[dict[str, Any]] = Field(None, description="create/set: {parameter: number | {value, unit, source, note}}; "
                                                 "source is user, assumed, measured or rate card")
    rate_card: Optional[dict[str, Any]] = Field(None, description="create: rates and prices {parameter: value}; source = rate card")
    vulcan_model_id: Optional[str] = Field(None, description="create/vulcan: id of a Vulcan library model whose measurements "
                                           "are fetched (optional; provenance recorded)")
    vulcan_url: Optional[str] = Field(None, description="Vulcan base URL (default http://127.0.0.1:5186)")
    target: Optional[str] = Field(None, description="vulcan: destination of the inspection report (default fdm-print)")
    volume_only: Optional[Literal["coarse", "block"]] = Field(None, description="create/options: what to do when FDM time can "
                                                              "only be estimated from volume: coarse (labelled estimate) or block")
    currency: Literal["EUR", "USD"] = "EUR"
    spec: Optional[dict[str, Any]] = Field(None, description="create: a complete custom definition instead of a template")
    replace: bool = Field(False, description="create: overwrite an existing quote")
    scenario: Optional[str] = Field(None, description="scenario name (scenario_save/scenario_delete; sensitivity, actuals, export on it)")
    overrides: Optional[dict[str, Any]] = Field(None, description="scenario_save: {parameter: number | {value, unit, source}}")
    description: str = Field("", description="scenario_save: free text")
    scenario_names: Optional[list[str]] = Field(None, description="compare: which scenarios (default all)")
    params: Optional[list[str]] = Field(None, description="sensitivity: parameters to vary (default every numeric one in use)")
    pct: float = Field(10.0, gt=0, lt=100, description="sensitivity: variation in percent")
    item: Optional[str] = Field(None, description="actual: line item or derived quantity id")
    value: Optional[float] = Field(None, description="actual: what it really cost or took")
    unit: Optional[str] = Field(None, description="actual: unit of value (default the item's own)")
    ledger_ref: Optional[str] = Field(None, description="actual: id of the related movement in the books (reference only)")
    note: str = ""
    actual_id: Optional[int] = Field(None, description="actual_delete: record id")
    format: Literal["json", "csv", "markdown"] = "json"


def _run_fabquote(s: Services, a: FabQuoteArgs) -> dict:
    from . import fabquote as fq
    need = lambda v, what: v if v not in (None, "") else (_ for _ in ()).throw(ValueError(f"{what} is required for {a.action}"))  # noqa: E731
    if a.action == "templates":
        return fq.templates()
    if a.action == "list":
        return fq.list_quotes(s)
    if a.action == "vulcan":
        return fq.fetch_vulcan(need(a.vulcan_model_id, "vulcan_model_id"), a.vulcan_url, a.target or "fdm-print")
    n = lambda: need(a.name, "name")  # noqa: E731
    if a.action == "create":
        vul = {"model_id": a.vulcan_model_id, "base_url": a.vulcan_url, "target": a.target} if a.vulcan_model_id else None
        return fq.create_quote(s, n(), a.process, a.measurements, a.parameters, a.rate_card, vul, a.volume_only,
                               a.currency, a.spec, a.replace, source="agent")
    if a.action == "get":
        return fq.get_quote(s, n())
    if a.action == "delete":
        return fq.delete_quote(s, n(), source="agent")
    if a.action == "set":
        return fq.set_parameters(s, n(), need(a.parameters, "parameters"), source="agent")
    if a.action == "options":
        return fq.set_options(s, n(), need(a.volume_only, "volume_only"), source="agent")
    if a.action == "scenario_save":
        return fq.save_scenario(s, n(), need(a.scenario, "scenario"), need(a.overrides, "overrides"), a.description, source="agent")
    if a.action == "scenario_delete":
        return fq.delete_scenario(s, n(), need(a.scenario, "scenario"), source="agent")
    if a.action == "compare":
        return fq.compare_scenarios(s, n(), a.scenario_names, source="agent")
    if a.action == "sensitivity":
        return fq.run_sensitivity(s, n(), a.params, a.pct, a.scenario, source="agent")
    if a.action == "actual":
        return fq.record_actual(s, n(), need(a.item, "item"), need(a.value, "value"), a.unit, a.ledger_ref, a.note, source="agent")
    if a.action == "actuals":
        return fq.compare_actuals(s, n(), a.scenario)
    if a.action == "actual_delete":
        return fq.delete_actual(s, n(), need(a.actual_id, "actual_id"), source="agent")
    return fq.export_quote(s, n(), a.format, a.scenario)


class MigrateArgs(BaseModel):
    action: Literal["suggest", "save", "list", "get", "delete", "rehearse", "ddl"] = Field(
        "list", description="suggest = draft a target table from a dataset (a labelled suggestion, not applied); "
        "save/get/delete/list = persisted specs by name; rehearse = dress-rehearsal load into a fresh SQLite file with a full "
        "report; ddl = the target schema as SQL.")
    name: Optional[str] = Field(None, description="spec / migration name (letters, digits, _ and -)")
    spec: Optional[dict[str, Any]] = Field(None, description="inline spec: {name, tables:[{name, source|source_sql, columns:"
                                           "[{name,type,required,expr,kind,source_column}], primary_key, unique, foreign_keys:"
                                           "[{columns,ref_table,ref_columns}]}], checks:{totals:[{table,source_expr,target_column,"
                                           "tolerance}], queries:[{name,source_sql,target_sql}]}}")
    dataset: Optional[str] = Field(None, description="suggest: dataset to draft from")
    table: Optional[str] = Field(None, description="suggest: target table name")
    save_rejected: Optional[str] = Field(None, description="rehearse: save every rejected row with its reason as this dataset")
    sample: int = Field(10, ge=0, le=200, description="rehearse: examples per reason")


def _run_migrate(s: Services, a: MigrateArgs) -> dict:
    from . import migrate as mg
    if a.action == "suggest":
        if not a.dataset:
            raise ValueError("suggest needs a dataset")
        return mg.suggest_spec(s, a.dataset, a.table, a.name, source="agent")
    if a.action == "list":
        return mg.list_specs(s)
    if a.action == "get":
        return mg.get_spec(s, a.name or "")
    if a.action == "delete":
        return mg.delete_spec(s, a.name or "", source="agent")
    if a.action == "save":
        if not a.name or not a.spec:
            raise ValueError("save needs name and spec")
        return mg.save_spec(s, a.name, a.spec, source="agent")
    if a.action == "ddl":
        return mg.ddl(s, a.spec, a.name)
    return mg.rehearse_spec(s, a.spec, a.name, a.save_rejected, a.sample, source="agent")


class ProcessArgs(BaseModel):
    action: Literal["suggest", "discover", "conform", "rehearse", "profile", "calibrate", "export"] = Field(
        "discover", description="suggest = propose the column mapping; discover = process map, variants, rework, "
        "throughput times, bottlenecks, resources; conform = check cases against a reference process; rehearse = "
        "simulate the discovered process and compare changes with the baseline; profile = checked event log "
        "(duplicates, ties, invalid timestamps, zones, open cases, missing steps) with variants, graph coverage, "
        "times with n and percentiles, service vs waiting, concurrency, bottlenecks and deviations; calibrate = "
        "fit a scenario model on the cases before a temporal split, check it against the held-out cases "
        "(pass/fail) and compare variants with the same seeds; export = the profile and recipe as JSON with a "
        "reference to the dataset (no rows).")
    dataset: Optional[str] = Field(None, description="event-log dataset: one row per event (or per start/complete "
                                   "event with a lifecycle column); optional when a recipe names it")
    mapping: Optional[dict[str, Optional[str]]] = Field(
        None, description="columns: {case, activity, timestamp (end of the step, or of the event), start?, "
        "lifecycle? (start/complete values; not together with start), resource?, cost?}. Required except for "
        "suggest; start times enable waiting vs working time and capacity.")
    log_options: Optional[dict[str, Any]] = Field(
        None, description="profile/calibrate/export: {timezone (IANA name or +01:00, for timestamps without an "
        "offset; default UTC), timestamp_format? (strptime), end_activities? [..] (cases not ending in one are open), "
        "expected? [ordered steps] (missing steps; its last step also closes a case), lifecycle_values? {start: [..], "
        "complete: [..]}}")
    holdout: float = Field(0.3, ge=0.05, le=0.8, description="calibrate/export: share of the latest cases (by start) "
                           "held out to check the model")
    split_at: Optional[str] = Field(None, description="calibrate/export: split instant instead of holdout (ISO)")
    tolerances: Optional[dict[str, float]] = Field(
        None, description="calibrate: relative tolerances {cycle_time_mean_hours, cycle_time_p50_hours, "
        "cycle_time_p90_hours, cases_per_hour, bottleneck_wait_mean_hours}")
    model_edits: Optional[dict[str, Any]] = Field(
        None, description="calibrate/export: edits of the fitted model, validated: {arrival_rate: per hour, "
        "stations: {activity: {service: {dist: fixed|exponential|uniform|triangular|lognormal, ...params in hours}, "
        "servers: int>=1}}, routing: {start: {activity: share}, next: {activity: {activity|END: share}}}}. Edited "
        "parameters are marked as edited by the user with their original value; validation and alternatives are "
        "re-run with the edited model (same seeds) and the diff against the fitted model is returned")
    with_calibration: bool = Field(False, description="export: include the holdout calibration")
    recipe: Optional[dict[str, Any]] = Field(None, description="profile/calibrate: an exported recipe to re-run "
                                             "(mapping, options and settings; warns if the data changed)")
    threshold: float = Field(0.5, ge=0, le=1, description="discover: heuristic-miner dependency threshold for kept edges")
    min_count: int = Field(1, ge=1, description="discover: minimum count of an edge to keep it")
    max_variants: int = Field(20, ge=1, le=500, description="discover: variants to list")
    reference: Optional[dict[str, Any]] = Field(
        None, description="conform: {steps: [\"A\", {activity: \"B\", optional: true}, {parallel: [\"C\", \"D\"]}]} "
        "or {transitions: [[from, to], ...], start?: [...], end?: [...]}")
    max_examples: int = Field(5, ge=1, le=50, description="conform: example case ids per deviation")
    variants: Optional[list[dict[str, Any]]] = Field(
        None, description="rehearse: [{name, changes: [{type: add_resource, activity, count} | {type: remove_step, "
        "activity} | {type: set_routing, from, to, share} | {type: scale_duration, activity, factor}]}]; empty = "
        "validate the baseline only")
    replications: int = Field(20, ge=1, le=100, description="rehearse/calibrate: simulation replications")
    seed: int = Field(1, description="rehearse/calibrate: random seed (shared by baseline and variants)")
    tolerance: float = Field(0.15, gt=0, lt=1, description="rehearse: relative tolerance of the baseline validation")
    model_name: Optional[str] = Field(None, description="rehearse/calibrate: also store the model in the scenario lab")
    save_as: Optional[str] = Field(None, description="discover: prefix for the edges/variants/cases datasets; "
                                   "conform/rehearse: dataset name for the result")


def _run_process(s: Services, a: ProcessArgs) -> dict:
    from . import process_mining as pm
    from . import event_log as el
    if a.action == "profile":
        return el.profile_process(s, a.dataset, a.mapping, a.log_options, a.threshold, a.min_count, a.max_variants,
                                  a.recipe, source="agent")
    if a.action == "calibrate":
        return el.calibrate_process(s, a.dataset, a.mapping, a.log_options, a.holdout, a.split_at, a.replications,
                                    a.seed, a.tolerances, a.variants, a.model_name, a.recipe, source="agent",
                                    model_edits=a.model_edits)
    if a.action == "export":
        return el.export_process(s, a.dataset, a.mapping, a.log_options, a.with_calibration, a.holdout, a.split_at,
                                 a.replications, a.seed, a.tolerances, a.variants, source="agent",
                                 model_edits=a.model_edits)
    if not a.dataset:
        raise ValueError(f"dataset is required for {a.action}")
    if a.action == "suggest":
        return pm.suggest_mapping(s, a.dataset)
    if not a.mapping:
        raise ValueError(f"mapping is required for {a.action} (run action='suggest' to get one)")
    if a.action == "discover":
        return pm.discover_process(s, a.dataset, a.mapping, a.threshold, a.min_count, a.max_variants, a.save_as,
                                   source="agent")
    if a.action == "conform":
        if not a.reference:
            raise ValueError("reference is required for conform")
        return pm.conform_process(s, a.dataset, a.mapping, a.reference, a.max_examples, a.save_as, source="agent")
    return pm.rehearse_process(s, a.dataset, a.mapping, a.variants, a.replications, a.seed, a.tolerance, a.save_as,
                               a.model_name, source="agent")
class TwinArgs(BaseModel):
    action: Literal["fit", "simulate", "list", "get", "delete"] = Field(
        "list", description="fit = learn a dynamic model from a time series (saved when name is given); simulate = what-if "
        "with an input profile on a saved twin; list/get/delete = stored twins.")
    name: Optional[str] = Field(None, description="twin name (fit saves under it; simulate/get/delete need it)")
    dataset: Optional[str] = Field(None, description="fit: dataset name")
    time: Optional[str] = Field(None, description="fit: time column (numeric or date); rows must be regularly spaced")
    outputs: Optional[list[str]] = Field(None, description="fit: output (measured response) columns")
    inputs: Optional[list[str]] = Field(None, description="fit: input (control) columns")
    model: Literal["arx", "fopdt", "auto"] = Field("arx", description="fit: arx = multi-output linear ARX with lag chosen on a "
                                                   "selection block; fopdt = first order plus dead time (one input, one output); "
                                                   "auto = pick the better on the held-out block")
    max_lag: Optional[int] = Field(None, ge=1, le=12, description="fit: largest lag order tried (default 8)")
    validation_fraction: float = Field(0.25, description="fit: share of the record held out at the end (0.1 to 0.5)")
    input_lag_start: int = Field(1, description="fit: first input lag in samples (0 allows an instantaneous effect)")
    n_boot: int = Field(200, ge=20, le=500, description="fit: bootstrap refits for parameter uncertainty")
    seed: int = 0
    profile: Optional[dict[str, Any]] = Field(None, description="simulate: per input a number (held), a list of values per step, "
                                              "{step:{at,from,to}} or {ramp:{from,to}}; inputs not listed hold their last value")
    steps: Optional[int] = Field(None, ge=1, le=5000, description="simulate: number of steps ahead")
    initial: Optional[dict[str, Any]] = Field(None, description="simulate: {outputs:{name:value}, inputs:{name:value}} to start "
                                              "from a steady state instead of the end of the record")
    level: Optional[float] = Field(None, gt=0.5, lt=1, description="simulate: interval level (default 0.95)")
    allow_extrapolation: bool = Field(False, description="simulate: run even when an input lies far outside the training range")
    max_extrapolation: float = Field(0.25, ge=0, le=5, description="simulate: refuse beyond this excess, as a fraction of the training range")
    save_as: Optional[str] = Field(None, description="simulate: also save the simulated series as a dataset")


def _run_twin(s: Services, a: TwinArgs) -> dict:
    from . import twin as tw
    need = lambda v, what: v if v else (_ for _ in ()).throw(ValueError(f"{what} is required for {a.action}"))  # noqa: E731
    if a.action == "list":
        return tw.list_twins(s)
    if a.action == "get":
        return tw.get_twin(s, need(a.name, "name"))
    if a.action == "delete":
        return tw.delete_twin(s, need(a.name, "name"), source="agent")
    if a.action == "simulate":
        return tw.simulate_twin(s, need(a.name, "name"), a.profile, a.steps, a.initial, a.level, 300, a.seed,
                                a.allow_extrapolation, a.max_extrapolation, a.save_as, source="agent")
    return tw.fit_twin(s, need(a.dataset, "dataset"), a.time, need(a.outputs, "outputs"), need(a.inputs, "inputs"), a.model,
                       a.max_lag, a.validation_fraction, a.input_lag_start, a.n_boot, a.seed, a.name, source="agent")


class ExperimentArgs(BaseModel):
    action: Literal["design", "analyze", "next", "choose_test"] = Field(
        "design", description="design = plan runs; analyze = effects and response surface from results; next = the run(s) to "
        "measure next; choose_test = cheapest informative test among discrete hypotheses.")
    factors: Optional[list[dict[str, Any]]] = Field(None, description="design/analyze/next: [{name, low, high, integer?}]; "
                                                    "for analyze/next low/high may be omitted (taken from the data)")
    kind: Literal["full", "fractional", "ccd", "lhs"] = Field("fractional", description="design: 2-level full or fractional "
                                                              "factorial, central composite, or Latin hypercube")
    fraction: Optional[int] = Field(None, description="design: fractional p in 2^(k-p) (default: largest meeting min_resolution)")
    min_resolution: int = Field(4, description="design: minimum resolution for an automatic fraction (3 to 5)")
    max_runs: Optional[int] = Field(None, description="design: prefer designs with at most this many runs")
    center_points: Optional[int] = Field(None, description="design: centre replicates (default 3 factorial, 4 ccd)")
    variant: Literal["face", "inscribed", "circumscribed"] = Field("face", description="design ccd: axial placement")
    runs: Optional[int] = Field(None, description="design lhs: number of runs")
    seed: int = 0
    randomize: bool = True
    forbidden: Optional[list[Any]] = Field(None, description="forbidden combinations: expression strings like 'A + B > 15 and C < 2' "
                                           "or boxes {factor:[low,high]}")
    save_as: Optional[str] = Field(None, description="design: also save the plan as a dataset")
    dataset: Optional[str] = Field(None, description="analyze/next: dataset with factor columns and the response")
    response: Optional[str] = Field(None, description="analyze/next: response column")
    model: Literal["auto", "main", "interactions", "quadratic"] = "auto"
    level: float = Field(0.95, gt=0.5, lt=1)
    objective: Literal["explore", "maximize", "minimize"] = Field("explore", description="next: explore = largest prediction "
                                                                  "variance; maximize/minimize = expected improvement")
    n: int = Field(1, ge=1, le=20, description="next: how many runs to recommend")
    candidates: Optional[list[dict[str, Any]]] = Field(None, description="next: restrict to these settings (default: sampled feasible points)")
    hypotheses: Optional[list[Any]] = Field(None, description="choose_test: names or [{name, prior}]")
    tests: Optional[list[dict[str, Any]]] = Field(None, description="choose_test: [{name, cost, outcomes:{hypothesis: label or "
                                                  "{label: probability}}}]")
    threshold: float = Field(0.95, gt=0.5, le=1, description="choose_test: stop planning when one hypothesis reaches this posterior")


def _run_experiment(s: Services, a: ExperimentArgs) -> dict:
    from . import experiments as ex
    need = lambda v, what: v if v else (_ for _ in ()).throw(ValueError(f"{what} is required for {a.action}"))  # noqa: E731
    if a.action == "design":
        return ex.design(s, need(a.factors, "factors"), a.kind, a.save_as, "agent", fraction=a.fraction, min_resolution=a.min_resolution,
                         max_runs=a.max_runs, center_points=a.center_points, variant=a.variant, runs=a.runs, seed=a.seed,
                         randomize=a.randomize, forbidden=a.forbidden)
    if a.action == "analyze":
        return ex.analyze(s, need(a.dataset, "dataset"), need(a.factors, "factors"), need(a.response, "response"), a.model, a.level, source="agent")
    if a.action == "next":
        return ex.next_run(s, need(a.dataset, "dataset"), need(a.factors, "factors"), need(a.response, "response"), a.objective, a.n,
                           a.model, a.forbidden, a.candidates, a.seed, source="agent")
    return ex.choose(s, need(a.hypotheses, "hypotheses"), need(a.tests, "tests"), a.threshold, source="agent")


class EffectArgs(BaseModel):
    method: Literal["did", "its", "adjust"] = Field(
        description="did = difference-in-differences with a control group; its = interrupted time series (level and slope "
        "change); adjust = regression adjustment for listed confounders.")
    dataset: str
    outcome: str = Field(description="outcome column")
    time: Optional[str] = Field(None, description="did/its: time column (numeric or date)")
    intervention: Optional[Any] = Field(None, description="did/its: the time the change happened (number or date, e.g. 2026-03-01)")
    group: Optional[str] = Field(None, description="did: group column")
    treated_value: Optional[Any] = Field(None, description="did/adjust: the value marking the treated group (default 1/true)")
    unit: Optional[str] = Field(None, description="did: unit id column for cluster-robust errors")
    confounders: Optional[list[str]] = Field(None, description="did/adjust: covariate columns to adjust for (text columns become dummies)")
    treatment: Optional[str] = Field(None, description="adjust: treatment column (binary or numeric dose)")
    placebo_time: Optional[Any] = Field(None, description="did: fake change time inside the pre-period (default: its middle)")
    placebo_outcome: Optional[str] = Field(None, description="adjust: an outcome the change cannot affect, to test for hidden bias")
    season_period: Optional[int] = Field(None, ge=2, le=366, description="its: seasonal period in steps (adds seasonal dummies)")
    level: float = Field(0.95, gt=0.5, lt=1)
    graph: Optional[Any] = Field(None, description="adjust: causal hypothesis as edges 'a -> b' (lines, commas or a list of "
                                 "pairs); nodes that are not columns are unobserved. Finds the back-door adjustment set "
                                 "(used when confounders is empty), names variables that must not be adjusted for, and "
                                 "says what is missing when the effect is not identified.")
    refute: bool = Field(False, description="adjust: re-estimate with a placebo (shuffled) treatment, a random common cause "
                         "and random 80% subsets")


def _run_effect(s: Services, a: EffectArgs) -> dict:
    from . import effects as ef
    return ef.estimate(s, a.method, a.dataset, a.outcome, a.time, a.intervention, a.group, a.treated_value, a.unit, a.confounders,
                       a.treatment, a.placebo_time, a.placebo_outcome, a.season_period, a.level, source="agent",
                       graph=a.graph, refute=a.refute)


TOOLS: list[Tool] = [
    Tool("data_ingest", "Load a file, folder, URL, text or another app's data as a dataset; or delete one. Cargar/borrar datos.\n"
         "Writes a new dataset; action='delete' permanently removes a dataset and everything derived from it "
         "(destructive; force=true skips the dependency warning).\nSinónimos: importar datos, cargar archivo, cargar csv, cargar datos, carga el csv, "
         "ingerir csv, subir excel, cargar excel, leer carpeta, importar url, load csv, import data, "
         "datos de otra app, movimientos de Ledger, envíos de Phileas, tiempo de pantalla, desde la familia, "
         "borrar dataset, eliminar tabla.",
         IngestArgs, _ann(False, True, False), _run_ingest),
    Tool("data_refresh", "Re-ingest a source, replay its recipe, and check saved quality rules (write).\n"
         "Sinónimos: actualizar datos, releer archivo, refrescar fuente.",
         RefreshArgs, _ann(False, False, False), _run_refresh),
    Tool("data_list", "List every registered dataset with row/column counts and last update time.\n"
         "Sinónimos: listar datasets, qué datos hay, ver tablas.",
         Empty, _ann(True), _run_list),
    Tool("data_profile", "Column profile, exact paged value frequencies, deep EDA, quality score or drift.\n"
         "mode='frequencies' reads column in a saved version without changing data or creating an analysis record. "
         "Includes NULL groups, full VARCHAR value text and percentages over all rows; use next_offset for more.\n"
         "Sinónimos: perfil de datos, describir columnas, estadísticas de columna, análisis exploratorio, eda, "
         "puntuación de calidad, calidad de datos, deriva de datos, drift, correlación, valores atípicos, outliers.",
         ProfileArgs, _ann(True), _run_profile),
    Tool("data_versions_compare", "Compare row counts, schema and column profiles of two saved dataset versions.\n"
         "Reports added/removed/type-changed columns, null counts and approximate distinct counts with B minus A deltas. "
         "Does not align rows or identify individual changed cells. Reads saved versions without changing the current one.\n"
         "Sinónimos: comparar versiones, antes y después, efecto de la limpieza, diferencias de columnas.",
         CompareVersionsArgs, _ann(True), lambda s, a: s.compare_versions(a.dataset, a.version_a, a.version_b), capped=False),
    Tool("data_rows_compare", "Compare saved-version rows and duplicate counts. Comparar filas añadidas y retiradas.\n"
         "Uses EXCEPT ALL on chosen same-name/same-type columns. Reports omitted columns, complete counts and paged text previews. "
         "No guessed keys or paired edits; a changed row is one removal plus one addition. Preview cells use the existing 500-character display limit.\n"
         "Sinónimos: comparar filas, filas añadidas, filas retiradas, duplicados entre versiones, row difference.",
         CompareRowsArgs, _ann(True), lambda s, a: s.compare_rows(a.dataset, a.version_a, a.version_b, a.columns, a.offset_a, a.offset_b, a.limit), capped=False),
    Tool("data_preview", "First rows of a dataset version, with total row count.\n"
         "Sinónimos: ver datos, muestra de filas, primeras filas.",
         PreviewArgs, _ann(True), _run_preview),
    Tool("data_transform", "Preview/apply dataset transformations, or inspect operation parameters (op='help').\n"
         "For MCP-only clients, use op='help'; add dataset to include current column names/types, version, and row count without values.\n"
         "Sinónimos: transformar datos, limpiar columna, filtrar filas, renombrar columna, agrupar datos.",
         TransformArgs, _ann(False, False, False), _run_transform),
    Tool("data_join_preview", "Inspect left-row match coverage without applying a join. Cobertura de unión.\n"
         "Reads both current saved dataset versions. Counts left rows with at least one matching right row, "
         "not matching pairs or final joined rows; duplicates on the right count only once per left row. "
         "Independent of INNER/LEFT/RIGHT/FULL choice. Equality does not match NULLs. "
         "Returns left_rows, matched, unmatched and match_rate (0–1; zero for an empty left side). "
         "No new version, pending pipeline evaluation or analysis record.\n"
         "Sinónimos: cobertura de unión, filas con coincidencia, claves de tablas, join coverage.",
         JoinPreviewArgs, _ann(True), lambda s, a: s.join_preview(a.dataset, a.other_dataset, [pair.model_dump() for pair in a.on])),
    Tool("data_undo", "Move a dataset to an earlier (undo) or later (redo) version (write, non-destructive).\n"
         "Sinónimos: deshacer, rehacer, volver a la versión anterior.",
         UndoArgs, _ann(False, False, True), _run_undo),
    Tool("data_recipe", "Show, export as SQL, or replay a dataset's recorded step history (a pipeline).\n"
         "Sinónimos: receta de limpieza, exportar sql, volver a ejecutar el pipeline, linaje.",
         RecipeArgs, _ann(False, False, False), _run_recipe),
    Tool("data_query", "Run one read-only SQL SELECT over registered dataset views (DuckDB dialect).\n"
         "Sinónimos: consulta sql, ejecutar select, preguntar con sql.",
         QueryArgs, _ann(True), _run_query),
    Tool("data_quality", "Define, run or report data-quality rules on a dataset. Reglas de calidad, validar datos.\n"
         "Rule kinds: not_null/unique/range/regex/row_count/freshness/referential/custom_sql (write on define/delete).\nSinónimos: reglas de calidad, validar datos, comprobar datos.",
         QualityArgs, _ann(False, False, False), _run_quality),
    Tool("data_chart", "Create a chart, or read a saved chart by ID; image=true returns its native PNG.\n"
         "action=create (default) saves a new SQL-aggregated chart. action=get needs chart_id, creates nothing and uses the dataset's current version. With get/image=true, MCP returns complete PNG ImageContent and JSON metadata; lang=es/en selects PNG number formatting. The native PNG may differ from the interactive view and is not a historical screenshot or every dataset row.\n"
         "Sinónimos: crear gráfico, descargar PNG, leer gráfico guardado, gráfica de barras, histograma, dispersión, mapa de calor.",
         ChartArgs, _ann(False, False, False), _run_chart),
    Tool("data_dashboard", "Dashboards: create, add charts/KPIs, list; or dashboards as code (YAML + metrics). Panel.\n"
         "Create a dashboard, add a chart/KPI item to it, list/get dashboards (write on "
         "create/add/apply_action/undo/redo) — every edit of a dashboard (add/remove/move/retitle an item, change a KPI "
         "query or chart, rename, filter, layout) is a typed action that is logged per dashboard and reversible: "
         "apply_action + undo/redo/history, action_schemas lists the JSON schemas — or work with dashboards as code: a semantic layer of named metrics/dimensions over a "
         "dataset (semantic_get/semantic_put/semantic_suggest) and a YAML dashboard spec compiled against it "
         "(code_list/code_get/code_put/code_validate/code_render/code_export). A failed code_put/code_validate "
         "comes back with one issue per problem, each carrying a `hint` — fix the YAML and call again; "
         "code_render never fails the whole dashboard, a broken widget just carries its own `error`.\n"
         "Sinónimos: panel de control, cuadro de mando, dashboard, dashboards como código, capa semántica, "
         "métricas y dimensiones, validar dashboard, renderizar dashboard, exportar dashboard, deshacer cambio de panel, rehacer, historial del panel, mover gráfico.",
         DashboardArgs, _ann(False, False, False), _run_dashboard),
    Tool("data_model", "Train/tune/explain/evaluate/optimize a model, or list/compare the Lab registry (write on "
         "most actions).\nSinónimos: entrenar modelo, predecir, clasificación, regresión, importancia de variables, "
         "ajustar hiperparámetros, tuning, explicar modelo, shap, optimizar, optimización bayesiana, frontera de "
         "pareto, comparar curvas, deriva del modelo, informe pdf, reporte lab.",
         ModelTrainArgs, _ann(False, False, False), _run_model),
    Tool("data_cluster", "K-means clustering with an elbow/silhouette scan; writes cluster labels back (write).\n"
         "Sinónimos: agrupar datos, clustering, segmentación, k-means.",
         ClusterArgs, _ann(False, False, False), _run_cluster),
    Tool("data_forecast", "Time-series forecast with a confidence interval. Pronóstico, previsión, serie temporal.\n"
         "Writes the forecast. Resamples onto a regular "
         "calendar grain first (freq: auto/day/week/month/quarter — auto picks by span/density so gappy, "
         "irregular daily data aggregates to a coarser series that Holt-Winters/ETS can actually fit), and "
         "prefers Holt-Winters/ETS whenever statsmodels is available and the resampled series is long enough, "
         "falling back to a seasonal-naive method otherwise — the result always reports resampled_to, method "
         "and why (set on a fallback).\n"
         "Sinónimos: pronóstico, previsión, serie temporal, predecir el futuro.",
         ForecastArgs, _ann(False, False, False), _run_forecast),
    Tool("data_export", "Export a dataset as CSV, XLSX, Parquet or JSON. (write)\n"
         "mode='flat' creates the usual single-sheet export. mode='inventory' reads the linked source workbook snapshot (sheet names, table ranges, package-part hashes). mode='preserve_workbook' patches same-sized data cells in a copy of an imported .xlsx, retains untouched OOXML parts, and writes a lineage receipt. Select version to export a specific dataset version. Optional workbook_updates adds other dataset/version pairs from the same original workbook to this one copy; every sheet is validated before publishing. Overwriting formula cells, changed row/column shapes and .xlsm are refused.\n"
         "Sinónimos: exportar datos, descargar csv, guardar excel.",
         ExportArgs, _ann(False, False, False), _run_export),
    Tool("data_workbook", "Compile an Excel workbook into an executable calculation; run it with new values, verify it and export it.\n"
         "Reads formulas and cached values (.xlsx/.xlsm; macros are never run), separates parameters, lookup tables and "
         "results with their labels, and executes the supported formula subset with Excel's semantics. Anything outside it "
         "is frozen with Excel's saved value and listed with the reason; results that depend on it are flagged uses_frozen. "
         "check compares every formula with Excel's saved value; verify recalculates new seeded cases in Excel (or LibreOffice) "
         "and compares each result; export writes a standalone Python module (standard library only).\n"
         "Sinónimos: compilar excel, convertir hoja de cálculo en aplicación, hoja de cotización, ejecutar fórmulas, "
         "recalcular excel, motor de cálculo del excel, comparar con excel, exportar cálculo a python, trazar fórmula.",
         WorkbookArgs, _ann(False, True, False), _run_workbook, capped=False),
    Tool("data_reconcile", "Reconcile two datasets that should describe the same things, row by row and cell by cell.\n"
         "suggest profiles both and proposes column correspondences and key candidates with evidence (shared values, "
         "name similarity, uniqueness); nothing is applied. run pairs rows only on the keys you choose: every row ends "
         "paired, only on one side, ambiguous (key repeated) or with a missing key, and every compared cell is equal, "
         "within tolerance, different, zero versus missing, empty versus missing, value on one side only or missing on "
         "both. Nothing is dropped or multiplied; save_as keeps every row with its bucket as a new dataset.\n"
         "Sinónimos: conciliar tablas, cuadrar dos excel, comparar clientes de dos sistemas, filas que faltan, "
         "duplicados entre tablas, correspondencia de columnas, reconcile, match records.",
         ReconcileArgs, _ann(False, False, False), _run_reconcile, capped=False),
    Tool("data_scenario", "Simulate an operation (demand, capacity, times, stock, cost) and compare scenarios.\n"
         "Scenario lab: discrete events, seeded, replications with 95% intervals. "
         "Gives scenarios under stated assumptions, not predictions. Results include throughput, lead time "
         "(mean/p50/p90), waiting, utilisation and queue per station, WIP, lost jobs, stockouts and total cost, a "
         "Little's-law check and, for a single M/M/c station, the Erlang-C waiting time. compare runs named "
         "overrides with common random numbers and says which differences are distinguishable; sweep varies one "
         "parameter; calibrate fits arrivals or service times from a dataset column and reports the sample size and "
         "warns when it is small. Models are saved by name.\n"
         "Sinónimos: simulación, escenarios, qué pasaría si, colas, capacidad, cuello de botella, plantilla, turnos, "
         "inventario, punto de pedido, rotura de stock, simulación de eventos discretos, Erlang, what-if.",
         ScenarioArgs, _ann(False, False, False), _run_scenario, capped=False),
    Tool("data_fabquote", "Fabrication quote by scenario: parameters with units and sources, formula line items, actuals.\n"
         "Vulcan measures and prepares, this tool computes with explicit rates, density and settings; the books record "
         "what happened. Line items are split into geometric material, process material, setup, labour, machine, waste "
         "and margin, each a formula over named parameters (Pint units, dependency graph). Templates: fdm, cnc_laser, "
         "concrete. FDM time is never taken from solid volume alone without a labelled coarse-estimate warning (or a "
         "block); concrete printing refuses volume-only time and needs toolpath length, speed and layers. Missing "
         "measurements are never invented: they block the items that need them. scenario_save/compare recompute only the "
         "dependent items and give a before/after diff with formulas and sources; sensitivity varies one parameter at a "
         "time; actual/actuals compare real costs and times with the quote per item (optional reference to a ledger "
         "movement, nothing is written there); export gives json, csv or markdown. Geometry may come from Vulcan "
         "(vulcan_model_id, optional, with provenance).\n"
         "Sinónimos: presupuesto de fabricación, impresión 3D, FDM, hormigón, corte láser, CNC, coste de pieza, tarifa, "
         "escenarios de coste, sensibilidad, desperdicio, tiempo de máquina, calibración con costes reales, quote.",
         FabQuoteArgs, _ann(False, False, False), _run_fabquote, capped=False),
    Tool("data_migrate", "Rehearse a data migration before the real one: load datasets into a target schema in a fresh SQLite file.\n"
         "The spec names target tables, typed columns (expression over source columns, representation or correction), primary, "
         "unique and foreign keys, total checks and functional query checks. rehearse loads parents first and reports per table "
         "source = loaded + rejected, every rejected row with its reason (cast failure with column and value, missing required, "
         "duplicate key, foreign key not found), a second upsert load proving idempotency, per-column changes, a round-trip test "
         "for representation columns, and the checks evaluated on both sides. Sources are never modified; mappings are never "
         "invented (suggest only drafts, labelled). Also save/list/get/delete specs and export the DDL.\n"
         "Sinónimos: ensayo de migración, simulacro de migración, migrar datos, cargar en SQLite, filas rechazadas, claves "
         "foráneas, idempotencia, cuadre de totales, migration dry run.",
         MigrateArgs, _ann(False, False, False), _run_migrate, capped=False),
    Tool("data_process", "Process mining: discover a process from an event log, check conformance, rehearse changes.\n"
         "From case id, activity and timestamp (optional start, resource, cost): directly-follows graph with "
         "frequencies and waiting times, variants with shares, rework loops, throughput times, bottlenecks and "
         "resource workload. conform checks every case against a reference (ordered steps with optional/parallel "
         "markers, or allowed transitions) and lists skipped, inserted, repeated and reordered steps with counts and "
         "example cases. rehearse builds a simulation model of the process, validates the baseline against the "
         "observed throughput time (and says when it is not representative), then compares changes (add a resource, "
         "remove a step, change a routing share, shorten a duration) with common random numbers and 95% intervals. "
         "profile checks the event log itself (start/complete lifecycle or start column, zone, duplicates, ties, "
         "invalid timestamps, open cases, missing steps) and separates service from waiting where it can; calibrate "
         "fits a model with the provenance of every parameter on the cases before a temporal split and checks it "
         "on the held-out cases (it can fail and says so); export returns the profile and recipe with a dataset "
         "reference. The dataset is never modified. Durations are in hours, instants in UTC. Logged.\n"
         "Sinónimos: minería de procesos, descubrimiento de procesos, registro de eventos, cuello de botella, "
         "variantes, conformidad, desviaciones, retrabajo, tiempo de ciclo, simulación de cambios, process mining, "
         "perfil del registro de eventos, inicio y fin, espera y servicio, calibración con periodo reservado, receta.",
         ProcessArgs, _ann(False, False, False), _run_process, capped=False),
    Tool("data_twin", "Learn a dynamic twin from a time series and simulate what-ifs with prediction intervals.\n"
         "fit: interpretable linear ARX (lag chosen on a selection block) or first-order-plus-dead-time model; the last part of "
         "the record is held out and scored (one-step and free-run NRMSE, residual autocorrelation, interval coverage); parameters, "
         "steady-state gains and poles carry bootstrap uncertainty. simulate: an input profile on a saved twin gives the response "
         "with an interval and a baseline; it refuses to extrapolate far outside the input range seen in training and flags "
         "mild excursions. Twins are saved by name.\n"
         "Sinónimos: gemelo digital, modelo dinámico, identificación de sistemas, respuesta a escalón, ARX, qué pasa si cambio, "
         "simular proceso, predicción con intervalo, system identification, digital twin.",
         TwinArgs, _ann(False, False, False), _run_twin, capped=False),
    Tool("data_experiment", "Design experiments, analyse the results and decide what to measure next.\n"
         "design: 2-level full or fractional factorial (resolution, generators, aliases), central composite or seeded Latin "
         "hypercube, with bounds and forbidden combinations. analyze: main effects, interactions and a response surface with "
         "confidence intervals (Lenth when saturated), curvature and lack-of-fit checks. next: the run(s) with the largest "
         "prediction variance or best expected improvement, with the reason. choose_test: among hypotheses with predicted "
         "outcomes per test, the best information gain per cost.\n"
         "Sinónimos: diseño de experimentos, DOE, factorial fraccionado, superficie de respuesta, hipercubo latino, "
         "próximo experimento, qué medir ahora, ganancia de información, optimización experimental.",
         ExperimentArgs, _ann(False, False, False), _run_experiment, capped=False),
    Tool("data_effect", "Estimate the effect of a change from data: difference-in-differences, interrupted time series, adjustment.\n"
         "Gives the effect with a confidence interval, the assumptions the design needs with their status (parallel pre-trends, "
         "placebo dates, pre-trend linearity, balance and overlap, robustness to an unmeasured confounder) and explicit warnings "
         "when a testable assumption fails. Never claims causation: untestable assumptions are named. With a causal graph "
         "(adjust): identification by the back-door criterion, variables not to adjust for, what is missing when not "
         "identified; refute=true adds placebo-treatment, random-common-cause and subset checks.\n"
         "Sinónimos: efecto de una intervención, diferencias en diferencias, serie temporal interrumpida, antes y después, "
         "grupo de control, ajuste por confusores, causal, impacto de un cambio, did, its, grafo causal, "
         "puerta trasera, identificación, refutar, placebo.",
         EffectArgs, _ann(False, False, False), _run_effect, capped=False),
    Tool("data_log", "Search the analysis log (every operation, with its N-000123 id, input and result).\n"
         "Sinónimos: historial, registro de análisis, qué se ha hecho.",
         LogArgs, _ann(True), _run_log),
    Tool("data_ask", "Ask your data a question in plain language; one SQL query, shown. Pregunta a mis datos.\n"
         "A shared language model writes one SQL query, shown before running (needs a resolved language model).\nSinónimos: pregunta a mis datos, ask my data, analiza esto por mí.",
         AskArgs, _ann(True), _run_ask),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}

#: tools whose handler is a coroutine (they await the language model); every other one is plain blocking code
ASYNC_TOOLS = frozenset({"data_ask"})


def tool_catalog() -> list[dict]:
    return agentkit.tool_catalog(TOOLS)


#: tools whose human-readable messages can be asked for in Spanish with an extra `lang` argument
LANG_TOOLS = frozenset({"data_workbook", "data_reconcile", "data_scenario", "data_migrate", "data_process", "data_twin",
                        "data_experiment", "data_effect", "data_fabquote"})


def call_tool_sync(services: Services, name: str, arguments: dict | None) -> Any:
    """Run a blocking tool: validate the arguments, call it and cap the result for the assistant's context
    (the shared kit). Raises ``UnknownTool`` (a ``KeyError``) for an unknown name and pydantic's ``ValidationError``
    for bad arguments."""
    if name in ASYNC_TOOLS:
        raise ValueError(f"{name} is async: use call_tool")
    if name in LANG_TOOLS and arguments and "lang" in arguments:
        # optional message language (en/es, default English) for the analysis tools; not part of their own arguments
        arguments = dict(arguments)
        with use_lang(arguments.pop("lang")):
            return call_tool_sync(services, name, arguments)
    if name == "data_profile" and (arguments or {}).get("mode") == "frequencies":
        # A page must keep its complete values and rows so next_offset does not
        # skip groups. Other profile modes keep their existing preview cap.
        return agentkit.call_tool(TOOLS, services, name, arguments, cap=False)
    if name == "data_chart":
        args = ChartArgs.model_validate(arguments or {})
        if args.action == "get" and args.image:
            # Image bytes are an artifact, not a preview string. Keep the
            # existing metadata cap and leave every other call unchanged.
            with agentkit.uncapped():
                result = agentkit.call_tool(TOOLS, services, name, args.model_dump())
            png = result.pop("image_base64")
            return {**cap_result(result), "image_base64": png}
    return agentkit.call_tool(TOOLS, services, name, arguments)


async def call_tool(services: Services, name: str, arguments: dict | None) -> Any:
    """Run any tool from async code (the blocking ones run in place; use ``call_tool_sync`` in a worker thread)."""
    if name not in ASYNC_TOOLS:
        return call_tool_sync(services, name, arguments)
    if name in LANG_TOOLS and arguments and "lang" in arguments:
        arguments = dict(arguments)
        with use_lang(arguments.pop("lang")):
            return await call_tool(services, name, arguments)
    tool = TOOLS_BY_NAME[name]
    args = tool.input_model.model_validate(arguments or {})
    result = await tool.run(services, args)
    if not isinstance(result, dict):
        result = {"result": result}
    return cap_result(result)
