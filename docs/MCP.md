# MCP tools

`mcp_server.py` is a stdio bridge: at startup it fetches the tool catalog from the running app
(`GET /api/agent/tools`) and, for every call, proxies to `POST /api/agent/call` with the bearer
token from `<DATA_DIR>/mcp-token`. It never opens the database directly — the running app (`python
-m nightingale`) is what does all the work, so it must be running for the bridge to do anything.

Env vars the bridge reads: `NIGHTINGALE_URL` (default `http://127.0.0.1:5189`), and either
`NIGHTINGALE_TOKEN_FILE` (a path to the token file) or `NIGHTINGALE_TOKEN` (the token itself) —
if neither is set it falls back to `<NIGHTINGALE_DATA_DIR or ./data>/mcp-token`.

Every call is recorded in the analysis log with `source: "agent"` and an id like `N-000123`,
exactly like a UI action would be, so "what did the assistant just do to my data" is always
answerable from the **Log** screen.

18 tools, matching the family contract's cap:

| Tool | Kind | What it does |
| --- | --- | --- |
| `data_ingest` | write | Load a file, folder (glob), URL, or pasted CSV/JSON text as a new dataset. |
| `data_refresh` | write | Re-ingest a dataset's original source and replay its recorded recipe on the fresh data. |
| `data_list` | read | Every registered dataset, with row/column counts and last update time. |
| `data_profile` | read | Per-column profile: type, nulls %, distinct count, min/max/mean/sd, histogram, top values, IQR outlier count. |
| `data_preview` | read | First rows of a dataset version, plus the total row count. |
| `data_transform` | write (unless `preview: true`) | Apply or preview one of 21 cleaning/reshaping steps (filter, select, drop, rename, cast, fill_null, drop_duplicates, derive, split_column, text, replace, bin, date_parts, group, pivot, unpivot, join, union, sort, sample, window, sql). |
| `data_undo` | write, non-destructive | Move a dataset to an earlier (`undo`) or later (`redo`) version. |
| `data_recipe` | read / write on `replay` | Show a dataset's step history, export it as a runnable SQL script, or replay it (same as `data_refresh`). |
| `data_query` | read | Run one read-only `SELECT`/`WITH`/`DESCRIBE`/`SUMMARIZE` over registered dataset views. |
| `data_quality` | write on `define`/`delete` | Define, run, or report data-quality rules: `not_null`, `unique`, `accepted_values`, `range`, `regex`, `row_count`, `freshness`, `referential`, `custom_sql`. |
| `data_chart` | write | Build and save a chart (aggregated in SQL); `image: true` also returns a base64 PNG. |
| `data_dashboard` | write on `create`/`add` | Create a dashboard, add a chart or KPI item to it, or list/get dashboards. |
| `data_model` | write on `train` | Train a quick supervised model (regression/classification, auto-detected) for a target column, or list saved models. |
| `data_cluster` | write | K-means clustering with an elbow/silhouette scan; writes cluster labels back as a new dataset version. |
| `data_forecast` | write | Time-series forecast (Holt-Winters, or a documented seasonal-naive fallback) with a confidence interval. |
| `data_export` | write | Export a dataset to CSV/XLSX/Parquet/JSON inside the app's `exports/` folder. |
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
