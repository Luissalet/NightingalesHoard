# MCP tools

`mcp_server.py` is a stdio bridge: at startup it fetches the tool catalog from the running app
(`GET /api/agent/tools`) and, for every call, proxies to `POST /api/agent/call` with the bearer
token from `<DATA_DIR>/mcp-token`. It never opens the database directly — the running app (`python
-m nightingale`) is what does all the work, so it must be running for the bridge to do anything.

Env vars the bridge reads: `NIGHTINGALE_URL` (default `http://127.0.0.1:5189`), and either
`NIGHTINGALE_TOKEN_FILE` (a path to the token file) or `NIGHTINGALE_TOKEN` (the token itself) —
if neither is set it falls back to `<NIGHTINGALE_DATA_DIR or ./data>/mcp-token`.

Analysis operations are recorded in the analysis log with `source: "agent"` and an id like
`N-000123`, exactly like a UI action would be. The read-only `data_transform(op="help")`
parameter lookup does not create a version or log entry.

18 tools, matching the family contract's cap. The Lab package (EDA/quality/drift, the model
registry, diagnostics, tuning, explanations, optimization/Pareto, PDF reports, the visual pipeline
graph — see `docs/LAB.md`) is reached entirely through new `mode`s on `data_profile` and new
`action`s on `data_model`, so the cap never had to move:

| Tool | Kind | What it does |
| --- | --- | --- |
| `data_ingest` | write, destructive on `action="delete"` | `action="ingest"` (default) loads a file, folder (glob), URL, pasted CSV/JSON text, or (`kind="hoard"`) the records another Hoard app returns from a tool through the hub, as a new dataset. For `kind="hoard"` give `preset` (`ledger_transactions`, `phileas_shipments`, `argus_app_time` [tool name unverified]) or `app` + `tool` (+ `args`, `list_path`); running it again under the same name adds a new version. `action="delete"` permanently removes an existing dataset (its versions, quality rules, charts, and models); pass `force: true` to delete past dependents, otherwise the call fails and lists them. |
| `data_refresh` | write | Re-ingest a dataset's original source and replay its recorded recipe on the fresh data. |
| `data_list` | read | Every registered dataset, with row/column counts and last update time. |
| `data_profile` | read | `mode="profile"` (default): per-column profile (type, nulls %, distinct, min/max/mean/sd, histogram, top values, IQR outliers). `mode="eda"`: the Lab bundle — Pearson+Spearman correlation, null co-occurrence, IQR+z-score outliers, encoding suggestions, quality score. `mode="quality_score"`: just the 0-100 score. `mode="drift"`: KS test/PSI/Wasserstein/mean-sd shift against `other_dataset`. |
| `data_preview` | read | First rows of a dataset version, plus the total row count. |
| `data_transform` | read-only for `op="help"`; write when `preview: false` | Apply or preview one of 22 native cleaning/reshaping steps, or look up contracts with `data_transform(op="help", help_for="replace", dataset="sales")` to include current column names/types, version, and row count (no cell values). Omit `dataset` for the catalog-only response. REST: `GET /api/transforms?op=replace&dataset=sales`. Missing required transform fields return a step-specific error. See [transform operation reference](TRANSFORM-OPERATIONS.md) / [Spanish reference](TRANSFORM-OPERATIONS.es.md). |
| `data_undo` | write, non-destructive | Move a dataset to an earlier (`undo`) or later (`redo`) version. |
| `data_recipe` | read / write on `replay` | Show a dataset's step history, export it as a runnable SQL script, or replay it (same as `data_refresh`). |
| `data_query` | read | Run one read-only `SELECT`/`WITH`/`DESCRIBE`/`SUMMARIZE` over registered dataset views. |
| `data_quality` | write on `define`/`delete` | Define, run, or report data-quality rules: `not_null`, `unique`, `accepted_values`, `range`, `regex`, `row_count`, `freshness`, `referential`, `custom_sql`. |
| `data_chart` | write | Build and save a chart (aggregated in SQL); `image: true` also returns a base64 PNG. |
| `data_dashboard` | write on `create`/`add` | Create a dashboard, add a chart or KPI item to it, or list/get dashboards. |
| `data_model` | write on most actions | `action="train"` (default): the original quick supervised model, unchanged, unless `lab=true` — then it trains through the Lab registry (many more backends, and the model artifact is persisted so it can be evaluated/tuned/explained/optimized later). `action="list"`: the original quick-model list. `action="backends"`: list Lab registry backends and whether optional ones (xgboost/lightgbm) are installed. `action="registry"`: list/get/delete a saved Lab model (`registry_action`). `action="evaluate"`: diagnostics (metrics, residuals, error by group, bias over time, fit-gap) for a saved model. `action="tune"`: hyperparameter search (Optuna or a randomized fallback), saves the tuned model. `action="explain"`: global + per-row feature importance (SHAP or permutation importance + partial dependence). `action="optimize"`: suggest input values that maximize/minimize the target (Bayesian optimization, GP surrogate + EI/UCB). `action="pareto"`: non-dominated front across 2-3 saved models' predictions. `action="compare"`: curve/series comparison (`x`/`y`/`group`) or, with `model_ids`, a saved-models metrics comparison. `action="report"`: render a Lab PDF report. See `docs/LAB.md` for the full parameter reference (most of this lives under the `params` bag to stay within the tool's own schema). |
| `data_cluster` | write | K-means clustering with an elbow/silhouette scan. Cluster labels go to a new dataset by default — see `write_to` below. |
| `data_forecast` | write | Time-series forecast (Holt-Winters, or a documented seasonal-naive fallback) with a confidence interval. The forecast goes to a new dataset (`<source>__forecast_<model_id>`) by default. |
| `data_export` | read/write | `mode="inventory"` reads the imported workbook's source hash, sheet/table/comment inventory and OOXML part hashes; `mode="flat"` exports one dataset to CSV/XLSX/Parquet/JSON; `mode="preserve_workbook"` writes changed cells from a same-sized dataset version into a copy of the original `.xlsx` and returns a lineage receipt. Optional `workbook_updates` adds more dataset/version pairs from the same original workbook to that one copy; all tabs are preflighted before the workbook file is replaced, and the receipt lists each tab. Unchanged cells, formulas, comments, hyperlinks and validation metadata are preserved; formulas cannot be overwritten and validation rules are not enforced. Shape changes are refused. See [workbook export](WORKBOOK-EXPORT.md) / [Spanish guide](WORKBOOK-EXPORT.es.md). |
| `data_log` | read | Search the analysis log by free text, source (`ui`/`agent`), dataset, or an id like `N-000123`. |
| `data_ask` | read | Ask a question in plain language; a shared language model (via Hoard Link) writes and runs one SQL query, shown alongside the answer. Needs a resolved LLM backend — see `data_ask`'s error message if none is configured. |

## A minimal walk

```python
# list what's there
data_list()
# bring in a CSV
data_ingest(kind="file", path="/path/to/sales.csv", name="sales")
# see what it looks like before touching it
data_profile(dataset="sales")
# preview a cleaning step before committing to it
data_transform(dataset="sales", op="fill_null", params={"column": "region", "strategy": "mode"}, preview=True)
# apply it for real
data_transform(dataset="sales", op="fill_null", params={"column": "region", "strategy": "mode"}, preview=False)
# ask a read-only question
data_query(sql="SELECT region, SUM(amount) AS total FROM sales GROUP BY region ORDER BY total DESC")
# check what just happened
data_log(source="agent", limit=5)
```

## Model/cluster/forecast output: `write_to`

`data_model` (train), `data_cluster`, and `data_forecast` all write their per-row output
somewhere by default — a prediction column, a cluster label, an anomaly flag, a forecast. The
`write_to` parameter controls where:

- `"new_dataset"` (the default): a fresh dataset named `<source>__<model|clusters|anomalies|forecast>_<model_id>`,
  holding a natural key/id column (or a synthetic `_row_index`), the inputs, and the output. The
  source dataset is never touched — its columns, types, and version stay exactly as they were.
- `"new_version"` (`data_model`/`data_cluster` only, an explicit opt-in): adds the output as a new
  column on a new version of the *source* dataset itself. It only ever adds a column — an existing
  column's type is never changed (a DATE stays DATE, a DECIMAL stays DECIMAL), which is why this
  isn't the default: retyping an existing column on the very dataset you're building on top of is a
  surprising, previously-buggy thing for a "just train a model" call to do.
- `"none"`: run the model and return its metrics/predictions in the response, but don't write
  anything to any dataset.

`data_forecast` only supports `"new_dataset"`/`"none"` — a forecast's rows (future dates) don't
line up with the source dataset's rows, so `"new_version"` doesn't apply to it.

## Deleting a dataset

There's no separate 19th tool for this (the family contract caps the tool count at 18): it's
`data_ingest` with `action="delete"` and `name` set to the dataset to remove. It also exists as
`DELETE /api/datasets/{name}` in the HTTP API (`?force=true` to bypass the dependents check) and as
a "Delete dataset" button with a confirmation dialog in the Datasets screen, which shows exactly
which charts/dashboards/models depend on it before you confirm.

## Boundaries worth knowing

- `data_transform`'s `sql` op and `data_query` both run through the same gate as `data_quality`'s
  `custom_sql` rule: exactly one `SELECT`/`WITH`/`DESCRIBE`/`SUMMARIZE`/`EXPLAIN`/`PIVOT` statement,
  read-only. Anything else (a second statement, `INSERT`, `ATTACH`, ...) is refused before it
  reaches DuckDB.
- `data_model`'s automatic feature selection excludes near-unique text columns (order ids, raw
  dates) to avoid a one-hot-encoding blow-up; an explicit `features` list is always honored as-is.
  The excluded set is reported back in the result.
- Heavy operations (ingest, refresh, model training) run in a thread pool with a timeout (5 min for
  ingest/refresh/export, 3 min for models) so a pathological file or fit can't hang the whole app.
- `data_ask` needs a locally resolvable language model through Hoard Link (Faustus, a local
  llama.cpp server, Ollama, or an OpenAI-compatible endpoint). With none configured it returns a
  clear error rather than a made-up answer.
- Lab's model registry is separate bookkeeping from the original `data_model`/`data_cluster`/
  `data_forecast` models: only a model trained with `lab=true` (or `action="tune"`, which always
  goes through the registry) is persisted to disk and can be evaluated/tuned/explained/optimized
  afterwards. `algorithm` names overlap between the two ("random_forest" exists in both) — `lab`
  is what decides which path a `train` call takes; every other Lab action always uses the registry.
- XGBoost, LightGBM, Optuna and SHAP are optional (`requirements-lab.txt`); every Lab feature that
  would use one degrades to a documented fallback (a randomized search instead of Optuna's TPE,
  permutation importance + partial dependence instead of SHAP) rather than failing outright.
