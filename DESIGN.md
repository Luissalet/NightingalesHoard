# Design

Nightingale's Hoard is the mutable sibling in the Hoard family: where Laplace's Hoard answers
questions about data it never changes, Nightingale is where the data gets built — ingest, clean,
check, chart, model, and export, with every step recorded and reversible.

## Visual identity

Deep rose, on a warm paper background — distinct from every other Hoard app's accent so several
can run side by side without being confused at a glance.

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--accent` | `#7a1f4a` | `#e0679a` | Primary buttons, active nav, focus ring base |
| `--accent-hover` | `#5e1738` | `#ec8fb5` | Hover state of the above |
| `--accent-light` | `#e0679a` | `#e0679a` | Chart palette base, subtle accents |
| `--accent-soft` | `#fbe6ef` | `#3a1526` | Chip backgrounds |
| `--paper` | `#fbf7f9` | `#1c1216` | Page background |
| `--white` | `#ffffff` | `#26181f` | Card/panel surfaces |
| `--ink` / `--supporting-ink` | `#241419` / `#6b5560` | `#f3e9ee` / `#cbb4c1` | Primary / secondary text |
| `--ok` / `--warn` / `--danger` | green / amber / red pairs | (dark variants) | Status chips, quality results |

Dark mode follows the system preference (`prefers-color-scheme`) with an explicit `data-theme`
override for a manual toggle. Typography is the system UI stack (`Segoe UI` first, for a
Windows-native feel); numbers use `font-variant-numeric: tabular-nums` everywhere they're compared
in a column (row counts, metrics, log durations).

## Why DuckDB, and why one connection

Laplace's Hoard multiplexes read-only and read-write DuckDB connections because it mostly answers
queries against data it doesn't touch. Nightingale's entire job is mutation, so that split adds
complexity without buying anything: a single persistent connection, guarded by one `RLock`, is
simpler and just as safe for a single-user desktop app.

Each transform step doesn't overwrite data — it **materializes a new table** (`ds_{id}_v{n}`) and
moves a friendly view to point at it. That's what makes undo an O(1) pointer move, redo possible
after an undo (until a new step branches and prunes the redone-away versions), and a full recipe
replayable against refreshed source data. The trade-off is disk space: a long, wide recipe on a
huge dataset keeps every intermediate version until the dataset is deleted. For the sizes this app
targets (a workbench dataset, not a warehouse), that trade is worth the correctness and the undo
story it buys.

## Why charts are two artifacts

Every chart is one `ChartSpec` (kind, x, y, aggregate, filter, color) compiled to **one aggregating
SQL query** — never a whole table pulled into Python to summarize client-side. From that one query
result, two renderings are produced on request: a **Vega-Lite spec** for the interactive client
(zoom, tooltip, resize) via `vega-embed`, and a **PNG** via `matplotlib` (`Agg` backend, so it never
tries to open a window — this matters most on Windows, where a GUI backend can crash a headless
process) for the MCP agent and for anything that needs a static image (a saved dashboard export).
A date/timestamp axis is encoded as Vega-Lite's `temporal` type rather than `nominal`, so the
library picks readable tick spacing instead of drawing one label per distinct date.

## Why the model layer excludes "id-like" columns by default

Training with every column as a feature is the obvious first thing to try, but a raw order id or
an unaggregated date column one-hot-encodes into hundreds of near-unique dummy columns, which can
turn a two-second fit into one that never converges. When features aren't given explicitly, any
non-numeric column whose distinct-value count is both above 30 and above 5% of the row count is
left out automatically, and reported back (`excluded_id_like_columns`) so it's never a silent
decision. Passing an explicit feature list always overrides this and is trusted as-is.

## Why forecasting aggregates duplicate timestamps

Row-level data — one row per transaction, per event — is the normal shape of a dataset in this
workbench, and it usually has several rows per date. A time series needs one point per period, so
`data_forecast` sums duplicate timestamps into one point per date before doing anything else.
Skipping this step means the median gap between (sorted, but often same-day) rows collapses to
zero, and the forecast silently repeats the same date forever — a real bug caught by walking the
UI with a real messy sales export, now covered by a regression test.

## Recipe as an artifact, not a black box

Every applied step is stored as `{op, params}` plus the exact SQL it compiled to. That list is
what "undo" walks, what "export as SQL" prints as a runnable script, and what "refresh" replays
against freshly re-ingested source data. If a step no longer makes sense against reshaped data
(a column was renamed by an earlier step, say), replay stops there and keeps whatever succeeded,
rather than raising past the point a user could still act on the dataset.

## MCP surface

Nightingale exposes exactly the tools listed in `docs/MCP.md` (`data_ingest` through `data_ask`,
18 total) — the same operations the UI calls, through the same `Services` layer, logged the same
way (`source="ui"` vs `source="agent"`). The stdio bridge (`mcp_server.py`) never opens the
database: it fetches the tool catalog from the running app and proxies every call over HTTP with a
bearer token, so the app's own validation and locking are the only place behavior can differ.
