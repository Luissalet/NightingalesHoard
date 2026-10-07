# Transform operation parameters

`data_transform` accepts one operation name and a JSON object in `params`.
Preview first with `preview=true` (the default), then apply with `preview=false`
when the proposed result is correct. `GET /api/transforms` returns this
operation inventory as JSON for clients that need machine-readable guidance;
add `?op=replace` to retrieve just one contract. MCP-only clients can call
`data_transform(op="help", help_for="replace")` without a dataset, or omit
`help_for` for the full read-only catalog. This lookup does not create a dataset
version or analysis entry.

Unknown top-level fields inside `params` return a contract error before preview
or apply. They are never silently ignored: for example, `replace` uses
`replacement`, not `new_value`. Omitting `replacement` intentionally still
means an empty string (delete the matched text). Existing documented fields,
including conditional fields and `sample`'s `n`/`frac`, retain their behavior.

When a transform names a column that is not in the current version, the error
lists the dataset's available columns. Use one of those exact names, then
preview again; Nightingale reports the mismatch and does not guess or rename
the requested column.

| Operation | Required `params` | Optional fields and rules | Example |
| --- | --- | --- | --- |
| `filter` | `expr` | One SQL predicate. | `{"expr":"amount > 0"}` |
| `select` | `columns` | List of existing column names. | `{"columns":["region","amount"]}` |
| `drop` | `columns` | List of existing column names; cannot drop every column. | `{"columns":["temporary_note"]}` |
| `rename` | `mapping` | Object from current names to new names. | `{"mapping":{"old_name":"new_name"}}` |
| `cast` | `column`, `to` | `to`: `integer`, `double`, `varchar`, `text`, `date`, `timestamp`, `boolean`, `decimal`. Optional `spanish_number`, `date_format`. | `{"column":"amount_text","to":"decimal"}` |
| `fill_null` | `column` | `strategy` defaults to `value`; then `value` is also required. Strategies: `value`, `mean`, `median`, `mode`, `forward`. | `{"column":"region","strategy":"mode"}` |
| `drop_duplicates` | None | Optional `subset` list; omitted means all columns. | `{"subset":["id"]}` |
| `derive` | `name`, `expr` | `expr` is a SQL expression. An existing name is replaced. | `{"name":"total","expr":"quantity * price"}` |
| `split_column` | `column`, `into` | `into` is a list of output names; `delimiter` defaults to comma. | `{"column":"place","delimiter":",","into":["city","country"]}` |
| `text` | `column`, `op` | `op`: `trim`, `upper`, `lower`, `title`, `strip_accents`, `collapse_spaces`. Optional `new_column`. `title` uppercases the first Unicode letter in each contiguous letter/mark sequence, lowercases the rest, and preserves the exact delimiters (including spaces, apostrophes and hyphens); null stays null. | `{"column":"name","op":"trim"}` |
| `replace` | `column`, `pattern` | `replacement` defaults to `""`; `regex` defaults to `false`. `pattern` is the source text or regex, not a `find`/`old_value` field. | `{"column":"status","pattern":"old","replacement":"new"}` |
| `bin` | `column` | Optional `new_column`; use `edges` and optional `labels` for explicit ranges, otherwise `bins` defaults to 5. | `{"column":"score","bins":4}` |
| `date_parts` | `column`, `parts` | Parts: `year`, `month`, `day`, `dow`, `quarter`, `week`, `hour`, `minute`. | `{"column":"created_at","parts":["year","month"]}` |
| `group` | `group_by`, `aggregations` | `group_by` is `string[]`. `aggregations` is an array of `{fn?, column?, alias?}`; `fn` defaults to `sum`, and `column` is required except for `count`. Functions: `sum`, `avg`, `min`, `max`, `count`, exact `count_distinct` (ignores nulls), `median`, `stddev`. | `{"group_by":["region"],"aggregations":[{"column":"amount","fn":"sum","alias":"total"}]}` |
| `pivot` | `on`, `value` | Optional `fn` (defaults to `sum`) and `group_by` list. | `{"on":"quarter","value":"amount","fn":"sum"}` |
| `unpivot` | `on` | List of columns to unpivot; output names default to `key` and `value`, configurable with `name_col` and `value_col`. | `{"on":["q1","q2"]}` |
| `join` | `other_dataset`, `on` | `on` is `array<{left: string, right: string}>`. `how` defaults to `left`; choices: `inner`, `left`, `right`, `full`. | `{"other_dataset":"regions","on":[{"left":"region_id","right":"id"}],"how":"left"}` |
| `union` | `other_dataset` | `distinct` defaults to `false`; columns are matched by name. | `{"other_dataset":"archive"}` |
| `sort` | `by` | `by` is `array<string | {column: string, desc?: boolean}>`; `desc` defaults to false. | `{"by":[{"column":"amount","desc":true}]}` |
| `sample` | `n` or `frac` | Optional `seed` defaults to 42. `n` selects rows; `frac` is passed as the Bernoulli sampling fraction. | `{"n":100,"seed":42}` |
| `window` | `fn`, `order_by` | `order_by` and `partition_by` are `string[]`. Functions: `lag`, `lead`, `rolling_mean`, `rolling_sum`, `row_number`, `rank`. The first four also require `column`; optional `offset` defaults to 1, `window_size` to 3, and `partition_by` to `[]`; `new_column` is optional. | `{"fn":"lag","column":"amount","order_by":["date"]}` |
| `sql` | `sql` | One read-only statement, with `__prev__` naming the current version's table. | `{"sql":"SELECT * FROM __prev__ WHERE amount > 0"}` |

The inventory describes the implemented builders in `nightingale/workbench/steps.py`;
it does not replace dataset-specific checks such as whether a named column exists.
SQL expressions still pass through Nightingale's SQL safety gate. Unknown
operations and missing fields return clear step errors before a new version is
created. See [Spanish reference](TRANSFORM-OPERATIONS.es.md).
