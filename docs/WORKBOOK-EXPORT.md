# Preserve an imported Excel workbook

Nightingale still offers the ordinary flat export to one-sheet XLSX, CSV,
Parquet or JSON. For an imported `.xlsx`, it also supports a conservative
workbook-preserving copy:

The Datasets UI exposes this as **Preserve original workbook (.xlsx)**, alongside
the unchanged flat export. It inspects the linked workbook through the existing
inventory route and downloads one selected dataset/version's sheet through
`POST /api/export/workbook/download` (`dataset` and explicit nonnegative `version`).
That route returns the native exported file; it does not create another exporter.
Changing the displayed dataset/version clears the previous inventory and download
state. The UI reports that a browser download was requested, not that it was saved.

1. Ingest the workbook with `data_ingest(kind="file", path=..., name=...)`.
   Each selected worksheet becomes its own dataset. The source record stores a
   SHA-256 snapshot, worksheet/table inventory and hashes of the original OOXML
   package parts. `GET /api/workbooks/{dataset}` or `data_export(mode="inventory")`
   reads that inventory and confirms whether the source still matches.
2. Preview, transform, check and undo through the existing dataset workflow.
   Export a chosen version with `data_export(dataset=..., format="xlsx",
   mode="preserve_workbook", version=...)`, or `POST /api/export` with the same
   fields. The target sheet and original header row are derived from the
   dataset's version-zero ingest record.
3. Nightingale writes a copy under `data/exports/` plus a
   `.workbook-export.json` receipt. The receipt includes source and output
   hashes, dataset/version, source id, target sheet/range, unchanged/changed
   OOXML part names, warnings and the analysis-log id.

To update several imported tabs in that same copy, keep `dataset` and
`version` for the first tab and add `workbook_updates`, for example:

```json
{"dataset":"sales__Ventas","format":"xlsx","mode":"preserve_workbook","version":2,
 "workbook_updates":[{"dataset":"sales__Inventario","version":1}]}
```

The REST request and `data_export` MCP tool accept the same shape. Every update
must refer to the same original workbook path (case-insensitive on Windows) and
the same source SHA-256 snapshot. Each selected version is checked against its
version-zero baseline, imported columns, sheet, table/filter rectangle and
formula cells before the output workbook is published. Repeated sheet targets
are rejected. One workbook is written and its receipt lists each dataset,
version and changed-cell count. A preflight error leaves an existing workbook
and its existing receipt unchanged. This guarantee covers validation and the
workbook file replacement; the analysis log, receipt write and workbook file
are not one cross-database transaction.

This mode patches only cells whose values changed from the imported version in
the selected worksheet and sets workbook calculation flags for a full
recalculation on the next open. Unchanged source cells retain their original
OOXML cell content and type, including text IDs such as `00123`, formula cells,
and text that resembles a date. It retains styles, number formats, tables,
filters, comments, hyperlinks, charts, validations and unrelated package parts
as OOXML data. Validation metadata is kept, but Nightingale does not check
edited values against validation rules. It does not evaluate formulas or
refresh chart caches; formula/chart results can remain stale until Excel or
LibreOffice recalculates the output. The original workbook is never overwritten.
Changed typed date/datetime values use the source workbook's 1900 or 1904 date
epoch; booleans remain boolean cells, text beginning with `=` remains text, and
decimal values are written as numeric cells.

The export refuses a source whose SHA-256 differs from the ingestion snapshot,
an attempted change to a formula cell, a table or filter whose range is not
exactly the imported rectangle, changed row count, renamed/dropped/added
columns, missing source rows, and `.xlsm`. It preserves a same-sized tabular
range; it does not resize tables or reflow workbook objects.
The flat export remains available for deliberately generated one-sheet data.

## One new workbook with a sheet per dataset

`data_export` with `mode="sheets"` and `format="xlsx"` writes a new workbook
(not a copy of an imported one) with one sheet per dataset version. The
request names the first sheet with `dataset`, `version`, `sheet_name` and
`totals`, and the others in `extra_sheets`:

```json
{"dataset":"production","format":"xlsx","mode":"sheets","sheet_name":"Production","totals":true,
 "extra_sheets":[{"dataset":"staff","version":1,"sheet_name":"Staff"},{"dataset":"notes"}]}
```

Each sheet has a bold header row frozen under the top edge. With `totals: true`
the sheet ends in a row marked "Totals" (in the language of the request, the
`lang` argument of the tool, or the app) with the sum of every numeric column, in bold with a top border and a
fill; a column with a non-finite number gets no sum. A chosen `sheet_name` is
used as given and refused when Excel would refuse it (over 31 characters,
`[ ] : * ? / \`, apostrophe at either end, `History`, empty, or the same name
as another sheet ignoring case); without one the sheet takes the dataset name,
then `name_v<version>`, then a number. Everything is validated before the file
is written, the workbook is published atomically and the result lists each
sheet, dataset, version, row count and totals. `data_export_undo` removes it
or restores the file it replaced. A dataset name that does not exist is answered
with that name only, never with the list of registered datasets; `data_list`
shows what can be used. Values a worksheet cannot hold (NaN,
infinities, objects) are written as text.

This is a bounded preservation path, not a general Excel editor or a GridCraft
replacement. It makes no macro, formula-recalculation or complete Excel fidelity
claim. See [Spanish guide](WORKBOOK-EXPORT.es.md).
