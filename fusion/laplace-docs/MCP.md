# MCP tools

Transport: stdio. The adapter (`laplaces_hoard/mcp_server.py`) is a
standalone script — launch it by absolute path, not with `-m`; it imports
only the standard library, `httpx` and `mcp`. It reads the running app's
URL from `LAPLACE_URL` (default `http://127.0.0.1:8812`) and refuses any
host that is not loopback. Every tool is a thin wrapper over
`POST /api/agent/<tool>` on the running app, so the app must be started
first (Faustus does it from `faustus-plugin.json`, or run
`Iniciar Laplace's Hoard.cmd`).

## Any MCP client (not just Faustus)

```json
{
  "mcpServers": {
    "laplaces-hoard": {
      "command": "C:\\path\\to\\Laplace's Hoard\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\Laplace's Hoard\\laplaces_hoard\\mcp_server.py"],
      "env": { "LAPLACE_URL": "http://127.0.0.1:8812" }
    }
  }
}
```

On Linux/macOS, `command` is `.../.venv/bin/python`.

## Tools

Every result carries `id` (`L-000042`) and `cite` (`[L-000042]`); the
model is told to put the cite next to the number it uses.

| tool | read-only | arguments | returns |
| --- | --- | --- | --- |
| `calc` | yes | `expression`, `precision=15` (1–1000 significant digits) | `exact` (e.g. `7041/20`), `decimal` (text, `precision` digits), `is_exact` (the decimal is the value, no rounding), `latex`. `isprime`/`nextprime`/`factorint` add `value`/`factors`. Exact results over 2,000 characters are cut, with `exact_truncated` and `exact_digit_count`. |
| `math` | yes | `operation` (`simplify expand factor apart together solve nsolve diff integrate limit series summation product matrix dsolve inequality`), `expression` or `expressions`, `variable(s)`, `domain`, `order`, `lower`/`upper`, `point`, `direction`, `x0`, `matrix_op` + `matrix`/`matrix2`, `function` | `result` (text, capped at 1,500 characters), `latex`, `numeric` where meaningful. `solve`: a list of solutions, each with exact `values`, float `numeric` and `verified` (substituted back), plus `count`, `has_more` (max 20). |
| `units_convert` | yes | `quantity`, `to` | `to_magnitude` (12 significant digits), `to_unit`, `formatted`, `from_*`, `precision_note` |
| `stats` | yes | `test`, inline `data`/`data2` or `dataset` + `column`/`column2`/`group_by`/`where`, `mu`, `confidence`, `successes`/`trials`/`p0` | `test` (full name), statistic, `p_value`, effect size where standard (Cohen's d, rank-biserial, r, rho, r², slope CI), n per group, one neutral `interpretation` sentence |
| `date_calc` | yes | `operation` (`diff add business_days weekday iso_week age convert_tz parse`) and its fields | operation-specific; `business_days` also returns `counted`, `holidays_excluded` (max 25) and `holidays_excluded_count` |
| `data_list` | yes | – | per dataset: `name`, `kind`, `row_count`, `column_count`, `columns` (names, max 30), `source_path` |
| `data_register` | no | `path`, `name?`, `options?` (`delimiter`, `header`, `encoding`, `date_format`, `decimal_separator`/`thousands_separator`, `sheet`/`sheets`, `skip_rows`, `tables`, `glob`) | the new dataset's schema and profile, plus `numbers_converted` when Spanish-style numbers were read as numbers, `encoding_detected`, and a `hint` for a single row of nested lists; Excel/SQLite: `sheets`/`tables` and a short entry per dataset |
| `data_describe` | yes | `name` | `row_count`, `columns`, per-column `profile` (nulls %, approx. distinct, min/max, mean/sd, 10-bin histogram for numbers, top 5 values for text), 5 `sample_rows` |
| `data_query` | yes | `sql`, `limit=50` (max 1000) | `columns`, `rows`, `row_count` (returned), `total_rows` (whole result), `truncated`, `elapsed_ms` |
| `data_chart` | yes | `sql`, `kind` (`bar line area scatter histogram pie heatmap`), `x`, `y?`, `color?`, `title?`, `include_image?` | a JSON text part (`id`, `cite`, `spec_summary`, `row_count`, `png_bytes`, `chart_url` to open the saved PNG); the chart as MCP `ImageContent` (PNG, ≤ ~200 KB) only with `include_image=true` |
| `work_log` | yes | `limit=10` (max 50), `engine?`, `query?` | short entries (`id`, `cite`, `engine`, `operation`, `ok`, `source`, `input`, `result` capped at 300 characters), `has_more`; `query="L-000042"` returns that entry in full |

`ToolAnnotations`: `readOnlyHint` is true for everything except
`data_register` (which copies data into the local catalogue; the source file
is never modified), `destructiveHint=False`, `idempotentHint=True`,
`openWorldHint=False` on every tool. Every call — including `work_log` —
is recorded in the audit trail shown under "Assistant activity".

## Expression syntax (calc and math)

Python-like: `+ - * / // % **`, `^` also means power, parentheses,
comparisons (`==`, `<`, ...), and a single `=` in an equation. Numbers are
exact rationals (`0.1` is `1/10`). Functions: `sqrt cbrt root exp ln log
log10 log2 sin cos tan asin acos atan atan2 sinh cosh tanh floor ceil round
abs min max sum mean median factorial binomial gcd lcm mod pct pct_change
ratio isprime nextprime factorint`; constants `pi e tau inf oo I`. Nothing
else: attribute access, subscripts, lambdas, comprehensions, keyword
arguments and unknown names are rejected before SymPy sees them, and the
text is never passed to `eval` or `sympify`. `round` is exact and rounds
half away from zero.

## The SQL gate (`data_query`, `data_chart`)

Exactly one statement: `SELECT`, `WITH ... SELECT`, `DESCRIBE`,
`SUMMARIZE`, `EXPLAIN` or a single `PIVOT`/`UNPIVOT`. Everything else
(`ATTACH COPY INSTALL LOAD SET PRAGMA CREATE INSERT UPDATE DELETE EXPORT
CALL DROP ALTER`, multi-statement input) is rejected with a `sql_gate`
error before it runs, and queries run on a read-only connection with file
access disabled. See [ARCHITECTURE.md](ARCHITECTURE.md#connection-model).

## Errors

- App not running: `laplaces-hoard_unavailable: Laplace's Hoard is not
  running. Start it from Faustus (Apps) or with 'Iniciar Laplace's
  Hoard.cmd', then retry.`
- App too slow: `laplaces-hoard_timeout: ...` (the adapter waits 30 s).
- Any error from the app: `<code>: <message>`, e.g.
  `sql_gate: statement type not allowed: DROP. Only read-only SELECT ...`,
  `data: SQL error: Binder Error: Referenced column "nope" not found ...
  Candidate bindings: ...`, `math: diff needs 'variable': the expression has
  several symbols (x, y)`. Messages say what to change.

## Keywords for tool selection

Every tool's docstring ends with a `Keywords:` line in English and Spanish
(e.g. `calcular, cuánto es, porcentaje, IVA` for `calc`), because Faustus
picks tools by retrieval over the descriptions and people often write to
it in Spanish. `tests/test_mcp_protocol.py` checks that every tool has one.

## Notebook and application context

The complete catalogue now contains twenty tools. The original thirteen above retain their interfaces. notebook_cells, notebook_cell_get, computation_get and app_context are metadata reads and do not create new computation receipts. notebook_cell_create, notebook_cell_patch and notebook_cell_delete mutate the notebook through the same API used by the editor. Send only changed input/engine fields in a patch; later evaluation responses cannot replace newer content. See [API contracts](API.md) for arguments and concurrency behavior.
