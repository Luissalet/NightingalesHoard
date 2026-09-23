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
| **Ingest** | CSV/TSV (incl. semicolon-delimited, comma-decimal Spanish formats), Excel, Parquet, JSON/NDJSON, SQLite, a folder glob, a URL, or pasted text. | A single ingest call runs in a background thread with a 5-minute cap; very large files should be Parquet or pre-filtered CSV rather than a 50-sheet Excel workbook. |
| **Transform** | 21 step kinds (filter, select, drop, rename, cast, fill_null, drop_duplicates, derive, split_column, text ops, replace, bin, date_parts, group, pivot, unpivot, join, union, sort, sample, window, raw SQL) — every one previewable before it's applied. | The `sql` step and the SQL query tool both accept exactly one read-only statement; no writes, no multi-statement scripts. |
| **Undo / redo / recipe** | Every version is a real table; a side panel shows the full recipe, exports it as a runnable SQL script, and can replay it against re-ingested source data. | Branching after an undo discards the redone-away versions (like any editor's undo stack) — there's one timeline, not a tree. |
| **Quality rules** | 9 rule kinds (not_null, unique, accepted_values, range, regex, row_count, freshness, referential integrity, custom SQL), run on demand, with a pass/fail history. | Rules check the dataset's *current* version; they don't run automatically on ingest unless you re-run them yourself. |
| **Charts & dashboards** | 11 chart kinds, aggregated in SQL (never a full table pulled client-side), rendered interactively (Vega-Lite) and as PNG (for export/agent use); dashboards combine saved charts with SQL-expression KPI tiles. | Charts read the dataset's current version at render time — a chart doesn't freeze past data, it reflects the latest cleaning. |
| **Models** | Supervised learning (auto task detection, cross-validation, feature importance, confusion matrix), k-means clustering (elbow + silhouette), PCA, isolation-forest anomaly detection, Holt-Winters forecasting with interval and a documented seasonal-naive fallback. | Training samples down at 200k rows; automatic feature selection excludes near-unique identifier-like columns (reported back) to avoid a one-hot blow-up — pass an explicit feature list to override. |
| **Analysis log** | Every operation (ingest, transform, quality run, chart, model, export...) gets an id like `N-000123`, with input, a one-line summary, timing, and whether it came from you or the assistant. | The log is append-only and capped per-field in size; it's an audit trail, not a full data backup. |
| **Ask your data** | A plain-language question becomes one SQL query (shown, not hidden) run through a shared local language model via Hoard Link. | Needs a resolved LLM backend (Faustus, a local llama.cpp server, Ollama, or an OpenAI-compatible endpoint) — with none configured it says so clearly instead of guessing. |

## Use cases

- Drop in a messy CSV a colleague sent you, watch the workbench guess types and encodings, clean it
  with a handful of steps, and export something you'd actually hand to someone else.
- Ask an assistant to "look at this data and tell me what's off" — it can profile, run quality
  checks, and cite the exact `N-000123` log entry for whatever it found.
- Keep a recipe against a source file that updates weekly: refresh re-applies every step to the new
  data in one call.
- Train a same-afternoon model on a dataset you just finished cleaning, without leaving the app or
  writing a notebook.

## Quick start

Requires Python 3.11+ and Node 22+ (Node only to build the client).

```bash
git clone <this repo> nightingale-hoard
cd nightingale-hoard
python -m venv venv
venv/bin/pip install -r requirements.txt      # Windows: venv\Scripts\pip install -r requirements.txt
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
- `GET /api/sources`, `POST /api/sources/ingest`
- `GET /api/datasets`, `POST /api/datasets/{name}/refresh`
- `GET /api/datasets/{name}/{profile,preview,lineage,recipe,correlation}`
- `POST /api/datasets/{name}/{transform,undo,redo,join-preview}`
- `POST /api/query`
- `GET/POST /api/datasets/{name}/quality`, `POST /api/datasets/{name}/quality/run`, `DELETE /api/quality/{id}`
- `GET/POST /api/charts`, `GET/DELETE /api/charts/{id}`
- `GET/POST /api/dashboards`, `GET /api/dashboards/{id}`, `POST /api/dashboards/{id}/items`
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

104 tests covering the workbench engine (every ingestion path, every transform step, versioning and
undo/redo/replay), quality rules, charts and dashboards, models (including the id-like-column
exclusion and the duplicate-timestamp forecasting fix), the HTTP API, agent tools through
`/api/agent/call`, the request guard, the PWA endpoints, and a subprocess end-to-end test through
the MCP stdio bridge.

## License

MIT — Luis María Salete Cuartero.
