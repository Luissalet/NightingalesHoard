<img src="app-icon.png" width="96" alt="">

# Nightingale's Hoard

The data workbench for a local language model: ingest a messy file, clean it with versioned steps
you can undo, check it against rules you define, chart and dashboard it, train a quick model, and
ask it a question — all on your own machine, and all reachable by an assistant through MCP exactly
the way you'd use it yourself.

Everything stays local: DuckDB for the data, SQLite for metadata (sources, versions, quality rules,
charts, dashboards, models, the analysis log), no accounts, no network calls except to a file URL
you gave it.

Part of the Hoard family (see `faustus-plugin.json`) — where Laplace's Hoard is a read-only query
engine, Nightingale is the workbench: it's where the data actually gets built and changed.

## Why this exists

Cleaning a real dataset is normally a one-way trip: you overwrite the file, or you keep five
half-named copies "just in case." Nightingale keeps the trip reversible. Every cleaning step —
filter, rename, cast a type, fill a null, derive a column, join two datasets, twenty-one kinds in
total — creates a new, real, materialized version. Undo is instant. A step you didn't like never
touched the version before it. And if the *source file* changes later, "refresh" re-ingests it and
replays your whole recipe on the fresh data automatically, stopping cleanly if a step no longer
applies.

## What's implemented

| Feature | What it does | Boundaries |
| --- | --- | --- |
| **Ingest** | CSV/TSV (incl. semicolon-delimited, comma-decimal Spanish formats), Excel, Parquet, JSON/NDJSON, SQLite, a folder glob, a URL, or pasted text. | A single ingest call runs in a background thread with a 5-minute cap; very large files should be Parquet or pre-filtered CSV rather than a 50-sheet Excel workbook. Ingesting under a name that is already taken is refused (and the existing dataset left untouched): use refresh to reload it or pick another name. |
| **From the family** | Ingest source kind `hoard`: the records another Hoard app returns from one of its tools, called through Hoard Hub (`family.call`), with the list of records found in the answer (or given as `list_path`), flattened to one row per record (nested objects become `a_b` columns, lists JSON text) and ingested like JSON. Presets: `ledger_transactions` (Ledger `list_entries`, 200 per call up to 5000 entries, amounts in cents), `phileas_shipments` (Phileas `shipments_list` delivered, epoch times become timestamps and `transit_days` is added: calendar days from shipped to delivered), `argus_app_time` (Argus `screen_app_time`: **unverified**, that app was not available to check the tool name or its answer shape). In the UI: Sources, **From the family** (Desde la familia). Through MCP: `data_ingest` with `kind="hoard"` and `preset` or `app` + `tool` (+ `args`, `list_path`, options `paginate`, `max_rows`, `epoch_columns`, `derive`). | Needs the hub and the other app running; with either missing the error says which. Running the same hoard ingest again under the same name adds a NEW VERSION (`ingest`) on top of the current one, so Undo returns to the previous snapshot and cleaning steps are not replayed; `data_refresh` instead rebuilds from the app and replays the recipe. A different app/tool under a taken name is refused like any ingest. Pagination only for tools with limit/offset arguments; a capped import says `truncated`. |
| **Transform** | 21 step kinds (filter, select, drop, rename, cast, fill_null, drop_duplicates, derive, split_column, text ops, replace, bin, date_parts, group, pivot, unpivot, join, union, sort, sample, window, raw SQL) — every one previewable before it's applied. | The `sql` step and the SQL query tool both accept exactly one read-only statement; no writes, no multi-statement scripts. |
| **Undo / redo / recipe** | Every version is a real table; a side panel shows the full recipe, exports it as a runnable SQL script, and can replay it against re-ingested source data. | Branching after an undo discards the redone-away versions (like any editor's undo stack) — there's one timeline, not a tree. |
| **Quality rules** | 9 rule kinds (not_null, unique, accepted_values, range, regex, row_count, freshness, referential integrity, custom SQL), run on demand, with a pass/fail history. A failed `unique` rule says how many values repeat, how many rows are extra copies, and how many rows would remain after de-duplication. | Rules check the dataset's *current* version; they don't run automatically on ingest unless you re-run them yourself. |
| **Charts & dashboards** | 11 chart kinds, aggregated in SQL (never a full table pulled client-side), rendered interactively (Vega-Lite) and as PNG (for export/agent use); dashboards combine saved charts with SQL-expression KPI tiles. | Charts read the dataset's current version at render time — a chart doesn't freeze past data, it reflects the latest cleaning. |
| **Dashboards as code** | A dashboard as a plain YAML file over a reusable semantic layer (named metrics/dimensions per dataset) — filters, tabs, rows of metric/chart/table/text widgets, `for` loops over top-N dimension values, `if` conditions, and derived metrics (e.g. a metric shifted a month back). Validated with a `hint` on every issue, rendered with each widget's own SQL and error (one bad widget never breaks the rest), kept in a 20-version history with diffs, and exportable into the item-based dashboards above. See `docs/DASHBOARDS_AS_CODE.md`. | Everything compiles to read-only SQL over a dataset's own view, same trust level as a chart filter. |
| **Models** | Supervised learning (auto task detection, cross-validation, feature importance, confusion matrix), k-means clustering (elbow + silhouette), PCA, isolation-forest anomaly detection, Holt-Winters forecasting with interval and a documented seasonal-naive fallback. A "Write results to" control (new dataset by default, with a one-line explanation of the alternative) chooses where predictions/labels/flags land: `new_dataset` (default) puts them in their own new dataset (`<source>__model_<id>` etc.), the source dataset untouched; `new_version` writes onto the source itself instead, and even then only ever adds a column; `none` just shows the result. Every copied source column — in either destination — keeps its exact DuckDB type (a DATE stays a DATE, a DECIMAL stays a DECIMAL); only the new prediction/label columns are computed in pandas. | Training samples down at 200k rows; automatic feature selection excludes near-unique identifier-like columns (reported back) to avoid a one-hot blow-up — pass an explicit feature list to override. |
| **Delete a dataset** | Removes a dataset, all its versions, and cascades to its quality rules, charts, and models; a dashboard that used one of those charts keeps working (it shows the item as unavailable) rather than breaking. Available from the Datasets screen (with a confirmation dialog listing what depends on it), `DELETE /api/datasets/{name}` (`?force=true` to go past dependents), and the `data_ingest` MCP tool's `action="delete"`. | Blocked by default when charts/dashboards/models depend on the dataset — pass `force`/`force=true` to delete anyway. |
| **Analysis log** | Every operation (ingest, transform, quality run, chart, model, export...) gets an id like `N-000123`, with input, a one-line summary, timing, and whether it came from you or the assistant. | The log is append-only and capped per-field in size; it's an audit trail, not a full data backup. |
| **Ask your data** | A plain-language question becomes one SQL query (shown, not hidden) run through a shared local language model via Hoard Link. | Needs a resolved LLM backend (Faustus, a local llama.cpp server, Ollama, or an OpenAI-compatible endpoint) — with none configured it says so clearly instead of guessing. |
| **Lab** | Deeper data science on top of the workbench, with its own page (seven tabs — Explore, Models, Diagnose, Optimize, Compare & drift, Pipeline, Report): full EDA (correlation, null patterns + imputation preview, IQR/z-score outliers, encoding suggestions with one-click apply-as-step) and a 0-100 quality score (only ever shows 100 when every component is actually perfect); a pluggable model registry (linear/ridge/logistic, random forest, gradient boosting, extra trees, k-NN, MLP, Gaussian process, plus XGBoost/LightGBM if installed) with versioned, persisted models; model diagnostics (residuals, predicted-vs-actual, error by group, bias over time, over/under-fitting, calibration, and for classifiers a plotted ROC curve — one-vs-rest per class when there are more than two); hyperparameter tuning (Optuna TPE or a randomized fallback); explanations (SHAP or permutation importance + partial dependence); Bayesian optimization of a model's inputs (GP surrogate + EI/UCB) and multi-objective Pareto fronts; drift detection and curve/series comparison between datasets; a PDF report; and a visual pipeline editor (add/reorder/edit/preview steps as a node chain, applied through the same step engine as the Datasets page). See `docs/LAB.md`. Datasets and Models each link straight into it ("Open in Lab" / "Open in Lab registry"). | XGBoost/LightGBM/Optuna/SHAP are optional (`requirements-lab.txt`) — every feature degrades to a documented fallback without them. |

The data grid formats every cell by its DuckDB column type and the UI's own language setting —
dates and timestamps render as dates (a timestamp at exact midnight drops its time-of-day), and
numbers get thousands separators and sensible precision (2 decimals for `DECIMAL` columns, up to 4
significant digits for other numeric types) — while the exact raw value stays one hover away (a
tooltip) and a double-click away (copies it to the clipboard).

### Lab, in the UI

<img src="docs/media/lab-explore.png" width="420" alt="Lab Explore tab: quality score gauge, correlation heatmap, null patterns, outlier box plots"> <img src="docs/media/lab-diagnose.png" width="420" alt="Lab Diagnose tab: metrics, predicted-vs-actual, residuals, learning curve">

## Use cases

- Drop in a messy CSV a colleague sent you, watch the workbench guess types and encodings, clean it
  with a handful of steps, and export something you'd actually hand to someone else.
- Ask an assistant to "look at this data and tell me what's off" — it can profile, run quality
  checks, and cite the exact `N-000123` log entry for whatever it found.
- Keep a recipe against a source file that updates weekly: refresh re-applies every step to the new
  data in one call, runs saved quality rules, and reports failing rules with sample rows. Pass
  `check_quality=false` to skip the checks for a particular refresh.
- Train a same-afternoon model on a dataset you just finished cleaning, without leaving the app or
  writing a notebook.

## Quick start

Requires Python 3.11+ and Node 22+ (Node only to build the client).

```bash
git clone <this repo> nightingale-hoard
cd nightingale-hoard
python -m venv venv
venv/bin/pip install -r requirements.txt      # Windows: venv\Scripts\pip install -r requirements.txt
venv/bin/pip install -r requirements-lab.txt  # optional: Optuna/SHAP/XGBoost/LightGBM for the Lab package
npm install
npm run build
venv/bin/python -m nightingale --demo         # Windows: venv\Scripts\python -m nightingale --demo
```

Open http://127.0.0.1:5189. `--demo` seeds three invented datasets (a messy Spanish-format sales
CSV, a customer table, a seasonal sensor time series with a few injected anomalies) into a separate
`data-demo/` folder, so it never touches real data. Drop `--demo` for a clean workbench.

- `python scripts/launch.py` starts the app on a free port and opens the browser.
- `python scripts/dev.py` runs uvicorn with `--reload` plus the Vite dev server (proxying `/api`).

## Configuration (environment)

| Variable | Default | Meaning |
| --- | --- | --- |
| `NIGHTINGALE_PORT` / `PORT` | `5189` | Preferred port; `PORT_STRICT=1` pins it, otherwise the first free port from there. |
| `NIGHTINGALE_DATA_DIR` | `<repo>/data` (`data-demo` with `--demo`) | DuckDB file, metadata SQLite, `mcp-token`, exports, chart images. |
| `NIGHTINGALE_ALLOWED_HOSTS` | | Extra host names accepted behind a tunnel (see below). |

### Access from your phone (behind a tunnel)

The server binds `127.0.0.1` and only answers requests whose `Host` is `localhost`, `127.0.0.1` or
`[::1]`. To reach it from your phone through a tunnel, list the extra host names in
`NIGHTINGALE_ALLOWED_HOSTS`, comma-separated, exact names or `*.suffix`:
`NIGHTINGALE_ALLOWED_HOSTS=my-pc.example,*.ts.net`. Port and letter case are ignored, and the
`Origin` of API calls must resolve to one of those hosts too. Once opened through the tunnel, the
browser offers to install it as a PWA.

## Connect to Faustus

Drop `faustus-plugin.json` into Faustus (or point it at this repo) and it picks up the health
check, launch command, and MCP bridge automatically — no manual wiring. The plugin never opens the
database itself; it only talks to the running app's HTTP API, the same as the browser UI does.

## API

All JSON; errors are `{ "error": "..." }`.

- `GET /api/health`, `GET /api/status`
- `GET /api/sources`, `POST /api/sources/ingest` (`kind: "hoard"` with `preset` or `app` + `tool` + `args` + `list_path`), `GET /api/sources/hoard-presets`
- `GET /api/datasets`, `POST /api/datasets/{name}/refresh`, `DELETE /api/datasets/{name}`, `GET /api/datasets/{name}/dependents`
- `GET /api/datasets/{name}/{profile,preview,lineage,recipe,correlation}`
- `POST /api/datasets/{name}/{transform,undo,redo,join-preview}`
- `POST /api/query`
- `GET/POST /api/datasets/{name}/quality`, `POST /api/datasets/{name}/quality/run`, `DELETE /api/quality/{id}`
- `GET/POST /api/charts`, `GET/DELETE /api/charts/{id}`
- `GET/POST /api/dashboards`, `GET /api/dashboards/{id}`, `POST /api/dashboards/{id}/items`
- `GET/PUT /api/dac/semantic`, `POST /api/dac/semantic/validate`, `GET /api/dac/semantic/suggest?dataset=`
- `GET/POST /api/dac/dashboards`, `GET/PUT/DELETE /api/dac/dashboards/{slug}`, `POST /api/dac/dashboards/{slug}/{rename,validate,render,export}`, `GET /api/dac/dashboards/{slug}/{history,diff}`, `POST /api/dac/dashboards/import` — see `docs/DASHBOARDS_AS_CODE.md`
- `POST /api/models/{train,cluster,pca,anomaly,forecast}`, `GET /api/models`
- `POST /api/export`
- `GET /api/log`, `GET /api/log/{id}`
- `POST /api/ask`, `GET /api/ask/available`
- `GET /api/agent/tools` (catalog + instructions), `POST /api/agent/call` (Bearer token from `<DATA_DIR>/mcp-token`)

## MCP tools

See `docs/MCP.md` for the full reference (18 tools, `data_ingest` through `data_ask`) and a minimal
walk-through. The shipped instructions tell the assistant to preview a transform before applying it
when the effect isn't obviously safe, to never assume a dataset name (`data_list` first), and to
cite the analysis-log id (`N-000123`) when reporting a result back.

## Tests

```bash
venv/bin/python -m pytest -q     # Windows: venv\Scripts\python -m pytest -q
```

Tests cover the workbench engine (every ingestion path, every transform step, versioning and
undo/redo/replay), quality rules, charts and dashboards, models (including the id-like-column
exclusion, the duplicate-timestamp forecasting fix, and that both the default new-dataset output
and the `write_to="new_version"` opt-in preserve every copied column's exact type), dataset
deletion (dependents check, cascade, force), the HTTP API, agent tools through `/api/agent/call`,
the request guard, the PWA endpoints, and a subprocess end-to-end test through the MCP stdio
bridge.

## License

MIT — Luis María Salete Cuartero.


## Shared services (HoardLink 0.8.1)

URL imports use the shared Web client and the Hub catalogue bridge. Import and analysis behavior remains in the app.

The vendored copy is maintained by HoardLink’s sync script. Windows validation and the family service contract are documented in HoardLink’s `docs/commons/windows-validation.md` and `docs/commons/services.md`.
