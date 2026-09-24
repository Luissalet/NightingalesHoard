# Dashboards as code

A dashboard is normally something you click together. Here it's also
something you (or the assistant) can *write*: a plain YAML file that says
what to show, checked into the same workbench as the data it reads, kept in
version history, and reviewable in a diff before it's shared. This is on top
of the original item-based dashboards (Charts + KPI tiles you add from the
UI), which keep working exactly as before — this is a second, textual way to
build a dashboard, over the same charts engine.

Two documents make it work:

1. **The semantic layer** — one YAML document per workbench (`semantic.yaml`)
   that names *metrics* and *dimensions* over a dataset once, so every
   dashboard reuses the same definitions instead of re-typing `SUM(importe)`
   in five places.
2. **A dashboard spec** — a YAML file per dashboard that lays out filters,
   tabs, rows and widgets against one semantic model.

Both are validated before they run, and every validation issue carries a
`path`, a `message`, and a `hint` — enough for an agent to fix the YAML and
retry without a human in the loop. Rendering never fails the whole
dashboard either: a bad widget carries its own `error` and every other
widget still renders.

## The semantic layer

```yaml
version: 1
models:
  sales:                      # semantic model name
    dataset: ventas            # a registered dataset (its current version) — or `sql: SELECT ...`
    time_dimension: fecha
    dimensions:
      region: {column: region, label: Región}
      month: {expr: "date_trunc('month', fecha)", label: Mes, type: time}
    metrics:
      revenue: {expr: "SUM(importe)", format: "€,.0f", label: Ingresos}
      orders: {expr: "COUNT(*)", format: ",d"}
      avg_ticket: {expr: "SUM(importe)/NULLIF(COUNT(*),0)", format: "€,.2f"}
      revenue_prev: {metric: revenue, offset: "-1 month"}     # derived: same metric shifted on the time dimension
```

- A **dimension** is `{column: ...}` (a plain column) or `{expr: ...}` (any
  DuckDB expression over the dataset), optionally `type: time` for a bucket
  like `month`.
- A **metric** is `{expr: ...}` (an aggregate expression) or a *derived*
  metric, `{metric: <other metric>, offset: "-1 month"}` — the same base
  metric evaluated at an earlier (or later, with a `+`) point in time. Units:
  `day`, `week`, `month`, `quarter`, `year`.
- Every column/expr here runs with exactly the same read-only trust as a
  chart's filter fragment elsewhere in the app — it's compiled straight into
  SQL against the dataset's own view, checked by the same read-only gate as
  `data_query`.

Don't want to write it by hand? `semantic_suggest` (dataset →) proposes a
starting model from the dataset's own columns: numeric columns get `SUM`/
`AVG` metrics, low-cardinality text columns become dimensions, and a date
column becomes the time dimension plus a `month` grain — always a starting
point to edit, never a final answer.

## The dashboard spec

```yaml
version: 1
name: Board meeting
description: Top-line metrics
model: sales
filters:                       # user-facing filters with defaults
  - name: date_range
    type: date-range
    dimension: month
    default: [2026-01-01, 2026-06-30]
  - name: region
    type: select
    dimension: region
    default: all
tabs:
  - name: Overview
    rows:
      - cols: 4                # widgets across
        widgets:
          - {type: metric, metric: revenue, compare: revenue_prev, title: Ingresos}
          - {type: metric, metric: orders}
          - {type: chart, kind: line, x: month, y: revenue, color: region}
          - {type: table, dimensions: [region], metrics: [revenue, orders], sort: -revenue, limit: 20}
      - for: {each: region, in: {dimension: region, top: 3, by: revenue}}   # loop → one row per top-3 region
        widgets:
          - {type: chart, kind: bar, x: month, y: revenue, filter: {region: "{{ each }}"}, title: "{{ each }}"}
      - if: "{{ filters.region == 'all' }}"      # conditional row
        widgets:
          - {type: text, markdown: "Showing every region."}
  - name: Detail
    rows: [...]
```

Widget types: `metric` (a number, optionally `compare`d against another
metric with a `delta_pct`), `chart` (`bar`/`grouped_bar`/`stacked_bar`/
`line`/`area`/`scatter`/`pie`/`donut`, drawn with the same styling as the
Charts page), `table`, and `text` (a small markdown subset: bold, italics,
links).

`for` expands one row into one per top-N value of a dimension (ranked by a
metric); inside it, and inside a row's `if`, `{{ each }}` and
`filters.<name>` are the only template variables — a tiny expression
evaluator (`==`, `!=`, `in`, `and`/`or`/`not`, literals) checks `if`
conditions without ever calling `eval`.

## Fix-hint philosophy

Every check — the semantic layer's `validate`, the spec's `validate`, and
the tool's own error messages — returns *what's wrong*, *where* (a dotted
path like `models.sales.metrics.revenue`), and *how to fix it* (a `hint`).
That's what lets `data_dashboard`'s `code_put`/`code_validate` actions be
called in a loop by an agent: write YAML, validate, read the hints, patch,
retry — the same shape as every other tool in this app that prefers a
precise, actionable error over a stack trace.

## Storage, history and interop

Code dashboards live as files under `<data_dir>/dashboards/<slug>.yaml` —
plain text, so a diff of two versions is a real diff. Every save keeps the
last 20 versions (`.../dashboards/history/<slug>/`), browsable from the
client's History panel. `code_export` turns a rendered code dashboard into
the original item-based kind (a KPI item per metric widget; a chart item
per chart widget whose metric is a plain aggregate over a real column) so it
also shows on the plain Dashboards page; a chart widget over a derived
metric or an `expr` dimension can't be represented that way and is skipped
with a warning rather than faked. `code_import` (from an existing item
dashboard) is best-effort the other way: without a semantic model behind
it, each item becomes a static text widget carrying its current value —
useful as a starting point, not a live dashboard.

## API

`/api/dac/semantic` (GET/PUT), `/api/dac/semantic/validate` (POST),
`/api/dac/semantic/suggest?dataset=` (GET), `/api/dac/dashboards`
(GET/POST), `/api/dac/dashboards/{slug}` (GET/PUT/DELETE),
`/api/dac/dashboards/{slug}/rename`, `/validate`, `/render` (POST with
`{"filters": {...}}`), `/history`, `/diff?a=&b=current`, `/export`, and
`/api/dac/dashboards/import`.

## Agent tool

Everything above is also the `data_dashboard` tool's `action`: alongside
the original `create`/`add`/`list`/`get`, it takes `semantic_get`/
`semantic_put`/`semantic_suggest` and `code_list`/`code_get`/`code_put`/
`code_validate`/`code_render`/`code_export` — no new tool was added, to keep
the MCP surface at its 18-tool cap.
