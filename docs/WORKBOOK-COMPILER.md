# Excel workbooks as calculations

Nightingale compiles an `.xlsx`/`.xlsm` workbook into an executable calculation: it reads formulas and Excel's saved values, separates **parameters** (cells a formula reads directly), **tables** (cells only read through ranges) and **results** (formulas no other formula reads), and runs each formula with Excel's semantics. **Workbooks** page, `/api/sheets/*` API and the `data_workbook` agent tool.

- `compile` (path or upload; macros never run), `evaluate` (new values by address, label or defined name; `as_of` for TODAY/NOW), `trace`, `check` (every formula against Excel's saved value), `verify` (seeded new cases recalculated by Excel through COM, or LibreOffice, compared result by result) and `export` (standalone Python module, standard library only, `compute()` and a command line).
- List validations become parameter options, including the cross-sheet ones Excel stores in an x14 extension.
- Anything outside the supported subset is **frozen** with Excel's saved value and listed with the reason; results depending on it are flagged `uses_frozen` and excluded from verification. Circular references are frozen (no iterative calculation).
- Evidence: random nested formulas recalculated by Excel, 3,073 of 3,093 equal (99.35%); approximate lookup fitted on 400 Excel observations on unsorted mixed data (descending 200/200, ascending 198/200). See `docs/WORKBOOK-COMPILER.es.md` for the function list and limits.
