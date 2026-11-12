<img src="app-icon.png" width="96" alt="">

# Nightingale's Hoard

A local data workbench: ingest, clean, check, chart and model datasets with reversible steps, plus a set of independent calculation modules. [Español](README.es.md)

<img src="docs/media/dashboard.png" width="760" alt="Dashboard with charts and KPI tiles">

## What it is

Nightingale's Hoard is the data app of the Hoard family. You load a messy file, clean it with versioned steps you can undo, check it against rules you define, chart and dashboard it, train a quick model and ask it questions in plain language. An assistant can do all of this through MCP in exactly the same way you do it in the browser.

Everything stays on your machine: DuckDB holds the data and SQLite holds the metadata (sources, versions, quality rules, charts, dashboards, models and the analysis log). There are no accounts and no network calls except to a file URL you give it.

Cleaning a real dataset is normally a one-way trip. Here every step creates a new, materialised version, so undo is instant and a step you did not like never touched the version before it. If the source file changes later, refresh re-ingests it and replays the whole recipe on the new data, stopping cleanly if a step no longer applies.

The app also bundles two switchable modules, Data and Calculation. Both are off on a fresh install; enable only what you need from Functions. Calculation (exact arithmetic, symbolic math, units, dates, statistics, SQL over files) works without loading the data workbench or DuckDB.

## What it does

### Ingest
- CSV/TSV (including semicolon-delimited and comma-decimal Spanish formats), Excel, Parquet, JSON/NDJSON, SQLite, a folder glob, a URL or pasted text.
- Records returned by another Hoard app (through Hoard Hub), flattened to one row per record, with presets such as ledger transactions.
- Refresh re-ingests a changed source and replays the recipe, then runs the saved quality rules.
- Deleting a dataset cascades to its rules, charts and models, after listing what depends on it.

### Clean and transform
- 22 step kinds: filter, select, drop, rename, cast, fill nulls, drop duplicates, derive, split column, text operations, replace, bin (bin count or explicit intervals), date parts, group (visual aggregation editor), pivot, unpivot, join, union, sort, sample, window (lag/lead, rolling sums and means) and raw SQL.
- Every step can be previewed before it is applied; applying creates a new version.
- Undo, redo and a recipe panel where any saved version can be opened; the recipe exports as a runnable SQL script.
- Joins with exact column pairs and a match check before applying.
- Compare two versions by column profile, or by rows (only A / only B, with repeated rows counted).
- Column frequencies with exact counts and percentages, paged.
- Data grid formatted by column type and interface language, with the raw value one hover away.
- Export a version as CSV, JSON, Parquet or XLSX; export back into a copy of the original workbook, changing only the cells that changed (single or several sheets).

### Quality rules
- Nine rule kinds: not null, unique, accepted values, range, regex, row count, freshness, referential integrity and custom SQL.
- Run on demand with a pass/fail history; a failed unique rule says how many values repeat and how many rows would remain after de-duplication.

### Charts and dashboards
- 11 chart kinds, aggregated in SQL, shown interactively (Vega-Lite) and as PNG, with an optional row filter and "use as a starting point" to copy a saved chart.
- Dashboards combine saved charts with SQL-expression KPI tiles. Every edit is a typed, reversible action with undo/redo and a change history.
- Dashboards as code: a YAML file over a semantic layer of named metrics and dimensions, with filters, tabs, loops, conditions and derived metrics; validated with hints, versioned, diffable and importable/exportable.

### Models and Lab
- Supervised learning with automatic task detection and cross-validation, k-means, PCA, isolation-forest anomaly detection and Holt-Winters forecasting. Results go to a new dataset by default.
- Lab, a separate page with seven tabs (Explore, Models, Diagnose, Optimize, Compare & drift, Pipeline, Report):
  - EDA: correlations, null patterns with imputation preview, outliers, encoding suggestions and a 0-100 quality score.
  - A model registry (linear, ridge, logistic, random forest, gradient boosting, extra trees, k-NN, MLP, Gaussian process, optionally XGBoost and LightGBM) with persisted, versioned models.
  - Diagnostics (residuals, predicted vs actual, calibration, ROC), hyperparameter tuning, SHAP or permutation explanations, Bayesian optimisation and Pareto fronts.
  - Drift detection, curve comparison, a PDF report and a visual pipeline editor sharing the Datasets step engine.
- Optional libraries (Optuna, SHAP, XGBoost, LightGBM) fall back to documented alternatives when missing. See [docs/LAB.md](docs/LAB.md).

### Story from the data
- Turns a dataset into an ordered list of findings (trend, group gap, outliers, concentration, change between periods), each with its chart, a sentence stating the exact figure and the query behind it. Findings that do not pass their statistical threshold are left out.
- Findings can be reordered, dropped and edited; a language model may reword a sentence only if no new number appears.
- Exports as one standalone HTML file or, through the Hub, as a presentation built by Cicero's Hoard.

### Ask your data
- A plain-language question becomes one visible SQL query, run through a shared local language model (Faustus, llama.cpp, Ollama or an OpenAI-compatible endpoint). With none configured it says so.

### Analysis tools
- **Reconcile**: compare two datasets row by row on key column pairs, with suggested keys, tolerances and normalisation; every row and cell is classified and the result can be saved or downloaded.
- **Excel workbooks as calculations**: compile an `.xlsx` into an executable calculation, run it with new inputs, trace results, check against Excel's saved values, recalculate in Excel or LibreOffice and export a standalone Python module or a small local web calculator. See [docs/WORKBOOK-COMPILER.md](docs/WORKBOOK-COMPILER.md) and [docs/WORKBOOK-EXPORT.md](docs/WORKBOOK-EXPORT.md).
- **Scenarios**: a seeded discrete-event simulation of an operation (arrivals, stations, shifts, buffers, costs, stock) with replications, 95% intervals, variant comparison and parameter sweeps.
- **Migrate**: rehearse a migration into typed target tables, with rejected rows and reasons, repeat-load idempotence, round-trip checks, totals and functional checks, and downloadable DDL and SQLite file.
- **Process**: discover a process from an event log (directly-follows graph, variants, rework, bottleneck, resource load), check conformance against a reference process and rehearse changes against the baseline.
- **Fabrication quotes**: unit-aware quotes from named parameters and formula line items, calibrated from past jobs and compared by scenario.
- **Experiments**: fit an interpretable dynamic model of a time series (twin); design and analyse factorial, central composite and Latin hypercube experiments; estimate the effect of a change (difference-in-differences, interrupted time series and others) with causal-graph adjustment and refutation checks.

### Calculation module
- Exact arithmetic, symbolic math, units, dates, statistics, SQL over files, charts and calculation history, independent of the data workbench. See [docs/FUSION.md](docs/FUSION.md).

### Analysis log
- Every operation gets an id such as `N-000123` with its input, a one-line summary, timing and whether you or the assistant ran it. It is an append-only audit trail, not a data backup.

## How to run it

Requires Python 3.11+ and Node 22+ (Node only to build the client).

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt      # Windows: venv\Scripts\pip install -r requirements.txt
venv/bin/pip install -r requirements-lab.txt  # optional: Optuna, SHAP, XGBoost, LightGBM
npm install
npm run build
venv/bin/python -m nightingale --demo         # Windows: venv\Scripts\python -m nightingale --demo
```

Open http://127.0.0.1:5189 and enable Data or Calculation in Functions. `--demo` seeds three invented datasets into a separate `data-demo/` folder; drop it for a clean workbench.

- `python scripts/launch.py` starts the app on a free port and opens the browser.
- `python scripts/dev.py` runs uvicorn with reload plus the Vite dev server.

| Variable | Default | Meaning |
| --- | --- | --- |
| `NIGHTINGALE_PORT` / `PORT` | `5189` | Preferred port; `PORT_STRICT=1` pins it, otherwise the first free port from there is used. |
| `NIGHTINGALE_DATA_DIR` | `<repo>/data` (`data-demo` with `--demo`) | DuckDB file, metadata SQLite, `mcp-token`, exports, chart images. |
| `NIGHTINGALE_ALLOWED_HOSTS` | | Extra host names accepted behind a tunnel. |

The server binds `127.0.0.1` and answers only `localhost`, `127.0.0.1` and `[::1]`. To reach it from a phone through a tunnel, list the host names in `NIGHTINGALE_ALLOWED_HOSTS` (comma-separated, exact or `*.suffix`, e.g. `my-pc.example,*.ts.net`); the `Origin` of API calls must match too. Opened through the tunnel, the browser offers to install it as a PWA.

## Connection with Faustus and Hoard Hub

- `faustus-plugin.json` is the plugin manifest; load it in Faustus to discover the MCP catalogue. Start the app from Hoard Hub and enable the module you need.
- The MCP bridge (`mcp_server.py`) calls the running app's HTTP API, like the browser UI, and never opens the database itself. Agent calls use the bearer token in `<data dir>/mcp-token` (`GET /api/agent/tools`, `POST /api/agent/call`).
- Tools by group: sources and ingest, datasets and profiling, transforms, versions and recipe, SQL queries, quality, charts, dashboards (plain and as code), models, export, story, workbooks, reconcile, scenarios, migration, process, experiments, analysis log, and module controls. Data tool reference: [docs/MCP.md](docs/MCP.md); Calculation tools: [fusion/laplace-docs/MCP.md](fusion/laplace-docs/MCP.md). Where names collide the Calculation tools use the `laplace_` prefix.
- The assistant is told to preview a transform before applying it, never to assume a dataset name, and to cite the analysis-log id when reporting a result.
- A "Faustus" button opens the family's shared assistant panel inside the app. It carries the dataset, dashboard, Lab model or log entry on screen as context and refreshes the lists after each turn.
- The app carries skills from the family's skill library for connected agents (data cleaning with lineage, measured improvement, verification loop, story from data), available through the skills tools and as prompts.
- Other docs: [docs/API.md](docs/API.md), [docs/TRANSFORM-OPERATIONS.md](docs/TRANSFORM-OPERATIONS.md), [docs/DASHBOARD-ACTIONS.md](docs/DASHBOARD-ACTIONS.md), [docs/DASHBOARDS_AS_CODE.md](docs/DASHBOARDS_AS_CODE.md).

## Screens

<details>
<summary>Screenshots</summary>

<img src="docs/media/datasets-workbench.png" width="420" alt="Datasets workbench"> <img src="docs/media/charts.png" width="420" alt="Charts">
<img src="docs/media/models.png" width="420" alt="Models"> <img src="docs/media/mobile-datasets.png" width="200" alt="Datasets on mobile">
<img src="docs/media/lab-explore.png" width="420" alt="Lab Explore"> <img src="docs/media/lab-models.png" width="420" alt="Lab Models">
<img src="docs/media/lab-diagnose.png" width="420" alt="Lab Diagnose"> <img src="docs/media/lab-optimize.png" width="420" alt="Lab Optimize">
<img src="docs/media/lab-compare.png" width="420" alt="Lab Compare and drift"> <img src="docs/media/lab-pipeline.png" width="420" alt="Lab Pipeline">
<img src="docs/media/lab-report.png" width="420" alt="Lab Report"> <img src="docs/media/lab-explore-mobile.png" width="200" alt="Lab Explore on mobile"> <img src="docs/media/lab-pipeline-mobile.png" width="200" alt="Lab Pipeline on mobile">

</details>

## Project layout

| Path | Contents |
| --- | --- |
| `nightingale/` | Python package: API, workbench engine, Lab, dashboards as code, story, scenarios, process mining and the other tools |
| `client/` | React/Vite interface |
| `fusion/` | Calculation module and its docs |
| `mcp_server.py` | MCP stdio bridge |
| `faustus-plugin.json` | Faustus plugin manifest |
| `scripts/` | `launch.py`, `dev.py` |
| `tests/` | Test suite |
| `docs/` | Reference docs and screenshots |
| `data/` | Local data (created at run time) |

## Development

```bash
venv/bin/python -m pytest -q     # Windows: venv\Scripts\python -m pytest -q
```

Data lives in `NIGHTINGALE_DATA_DIR`. Licence: MIT, Luis María Salete Cuartero.
