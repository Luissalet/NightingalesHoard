# Preserve an imported Excel workbook

Nightingale still offers the ordinary flat export to one-sheet XLSX, CSV,
Parquet or JSON. For an imported `.xlsx`, it also supports a conservative
workbook-preserving copy:

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

This is a bounded preservation path, not a general Excel editor or a GridCraft
replacement. It makes no macro, formula-recalculation or complete Excel fidelity
claim. See [Spanish guide](WORKBOOK-EXPORT.es.md).
