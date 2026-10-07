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

AGENT_INSTRUCTIONS = (
    "Nightingale's Hoard is the user's own data workbench: ingest files/folders/URLs/pasted text into a local "
    "DuckDB workbench, clean data with versioned steps (undo/redo, lineage, recipe export/replay), define and run "
    "quality rules, build charts and dashboards, train quick models (supervised/clustering/PCA/anomaly/forecast), "
    "and query everything read-only with SQL. Analysis operations are recorded in the log with an id like "
    "'N-000123'; read-only help lookups do not create log entries. Cite an analysis id when reporting its result. Always preview a transform "
    "(data_transform with preview=true) before applying it when the change is not obviously safe. Never assume a "
    "dataset name: call data_list first if unsure. This app never reads files or the database directly outside "
    "these tools."
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
    mode: Literal["profile", "eda", "quality_score", "drift"] = Field(
        "profile", description="'profile' (default): the original per-column profile. 'eda': correlation "
                                 "(Pearson+Spearman), null co-occurrence, outliers (IQR+z-score), and encoding "
                                 "suggestions, plus the quality score. 'quality_score': just the 0-100 score and its "
                                 "components. 'drift': compare numeric-column distributions against `other_dataset` "
                                 "(KS test, PSI, Wasserstein distance, mean/sd shift).")
    other_dataset: Optional[str] = Field(None, description="mode='drift' only: the dataset (or slice, as its own "
                                          "dataset) to compare distributions against.")
    columns: Optional[list[str]] = Field(None, description="mode='drift' only: restrict to these shared columns.")


class PreviewArgs(BaseModel):
    dataset: str
    version: Optional[int] = None
    limit: int = Field(50, ge=1, le=1000)


class TransformArgs(BaseModel):
    dataset: Optional[str] = Field(None, description="Required for a transform. With op='help', optionally name a dataset to include its current column names/types, version, and row count (no cell values).")
    op: str = Field(..., description="filter/select/drop/rename/cast/fill_null/drop_duplicates/derive/split_column/"
                                        "text/replace/bin/date_parts/group/pivot/unpivot/join/union/sort/sample/window/sql. "
                                        "Use op='help' with help_for='<operation>' for its exact read-only contract, or omit help_for for the full catalog.")
    help_for: Optional[str] = Field(None, description="With op='help', filter the read-only contract lookup to one transform operation. Add dataset when choosing fields so its current schema is included.")
    params: dict[str, Any] = Field(default_factory=dict, description="Operation-specific JSON object; use op='help' to look up its exact fields and nested shapes.")
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
    action: Literal["create", "add", "list", "get",
                     "semantic_get", "semantic_put", "semantic_suggest",
                     "code_list", "code_get", "code_put", "code_validate", "code_render", "code_export"] = Field(
        "list", description="Item-based dashboards (original): create/add/list/get. Dashboards as code: "
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
    Tool("data_profile", "Column profile, or (mode=) deep EDA, a 0-100 quality score, or drift vs another dataset.\n"
         "Sinónimos: perfil de datos, describir columnas, estadísticas de columna, análisis exploratorio, eda, "
         "puntuación de calidad, calidad de datos, deriva de datos, drift, correlación, valores atípicos, outliers.",
         ProfileArgs, _ann(True), _run_profile),
    Tool("data_preview", "First rows of a dataset version, with total row count.\n"
         "Sinónimos: ver datos, muestra de filas, primeras filas.",
         PreviewArgs, _ann(True), _run_preview),
    Tool("data_transform", "Preview/apply dataset transformations, or inspect operation parameters (op='help').\n"
         "For MCP-only clients, use op='help'; add dataset to include current column names/types, version, and row count without values.\n"
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
    Tool("data_quality", "Define, run or report data-quality rules on a dataset. Reglas de calidad, validar datos.\n"
         "Rule kinds: not_null/unique/range/regex/row_count/freshness/referential/custom_sql (write on define/delete).\nSinónimos: reglas de calidad, validar datos, comprobar datos.",
         QualityArgs, _ann(False, False, False), _run_quality),
    Tool("data_chart", "Build and save a chart from a dataset (aggregated in SQL); image=true also returns a PNG (write).\n"
         "Sinónimos: crear gráfico, gráfica de barras, histograma, dispersión, mapa de calor.",
         ChartArgs, _ann(False, False, False), _run_chart),
    Tool("data_dashboard", "Dashboards: create, add charts/KPIs, list; or dashboards as code (YAML + metrics). Panel.\n"
         "Create a dashboard, add a chart/KPI item to it, list/get dashboards (write on "
         "create/add) — or work with dashboards as code: a semantic layer of named metrics/dimensions over a "
         "dataset (semantic_get/semantic_put/semantic_suggest) and a YAML dashboard spec compiled against it "
         "(code_list/code_get/code_put/code_validate/code_render/code_export). A failed code_put/code_validate "
         "comes back with one issue per problem, each carrying a `hint` — fix the YAML and call again; "
         "code_render never fails the whole dashboard, a broken widget just carries its own `error`.\n"
         "Sinónimos: panel de control, cuadro de mando, dashboard, dashboards como código, capa semántica, "
         "métricas y dimensiones, validar dashboard, renderizar dashboard, exportar dashboard.",
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
    Tool("data_log", "Search the analysis log (every operation, with its N-000123 id, input and result).\n"
         "Sinónimos: historial, registro de análisis, qué se ha hecho.",
         LogArgs, _ann(True), _run_log),
    Tool("data_ask", "Ask your data a question in plain language; one SQL query, shown. Pregunta a mis datos.\n"
         "A shared language model writes one SQL query, shown before running (needs a resolved language model).\nSinónimos: pregunta a mis datos, ask my data, analiza esto por mí.",
         AskArgs, _ann(True), _run_ask),
]

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}
assert len(TOOLS) <= 18, "keep the MCP surface at or under 18 tools"

#: tools whose handler is a coroutine (they await the language model); every other one is plain blocking code
ASYNC_TOOLS = frozenset({"data_ask"})


def tool_catalog() -> list[dict]:
    return agentkit.tool_catalog(TOOLS)


def call_tool_sync(services: Services, name: str, arguments: dict | None) -> Any:
    """Run a blocking tool: validate the arguments, call it and cap the result for the assistant's context
    (the shared kit). Raises ``UnknownTool`` (a ``KeyError``) for an unknown name and pydantic's ``ValidationError``
    for bad arguments."""
    if name in ASYNC_TOOLS:
        raise ValueError(f"{name} is async: use call_tool")
    return agentkit.call_tool(TOOLS, services, name, arguments)


async def call_tool(services: Services, name: str, arguments: dict | None) -> Any:
    """Run any tool from async code (the blocking ones run in place; use ``call_tool_sync`` in a worker thread)."""
    if name not in ASYNC_TOOLS:
        return call_tool_sync(services, name, arguments)
    tool = TOOLS_BY_NAME[name]
    args = tool.input_model.model_validate(arguments or {})
    result = await tool.run(services, args)
    if not isinstance(result, dict):
        result = {"result": result}
    return cap_result(result)
