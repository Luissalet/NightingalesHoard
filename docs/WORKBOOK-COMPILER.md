# Excel workbooks as calculations

Nightingale compiles an `.xlsx`/`.xlsm` workbook into an executable calculation: it reads formulas and Excel's saved values, separates **parameters** (cells a formula reads directly), **tables** (cells only read through ranges) and **results** (formulas no other formula reads), and runs each formula with Excel's semantics. **Workbooks** page, `/api/sheets/*` API and the `data_workbook` agent tool.

- `compile` (path or upload; macros never run), `evaluate` (new values by address, label or defined name; `as_of` for TODAY/NOW), `trace`, `check` (every formula against Excel's saved value), `verify` (seeded new cases recalculated by Excel through COM, or LibreOffice, compared result by result) and `export` (standalone Python module, standard library only, `compute()` and a command line).
- List validations become parameter options, including the cross-sheet ones Excel stores in an x14 extension.
- The source workbook's1900/1904 date system is retained in the model summary and used for constants, formula evaluation, formatted results, independent verification cases and standalone Python exports. Date contexts are isolated between concurrent calculations. Literal text beginning with `=` remains text; the OOXML cell type determines whether a cell is a formula.
- Independent verification with no cases or no eligible results is `inconclusive`, with zero comparisons; it does not run a reference engine or claim agreement.
- Anything outside the supported subset is **frozen** with Excel's saved value and listed with the reason; results depending on it are flagged `uses_frozen` and excluded from verification. Circular references are frozen (no iterative calculation).
- Evidence: random nested formulas recalculated by Excel, 3,073 of 3,093 equal (99.35%); approximate lookup fitted on 400 Excel observations on unsorted mixed data (descending 200/200, ascending 198/200). See `docs/WORKBOOK-COMPILER.es.md` for the function list and limits.

Corrections2006:38workbook tests pass, including independent recalculation by installed Excel for both date systems and changed literal-text/date inputs, standalone export and interleaved/concurrent model evaluations. Real stdio MCP and the Workbooks screen were checked with invented inputs. This is a candidate awaiting cross-review and integration; it does not extend the supported formula subset.

## Deliver a calculator to a client

**Export calculator** downloads one Python file with the compiled calculation, a local web form, results, fonts and their licences. The recipient needs Python 3, but neither Nightingale, Excel, an LLM nor third-party Python packages. Run `python presupuesto_app.py --port 8080` and open the printed local address. It binds to `127.0.0.1`; no internet connection is required.

Numeric inputs, text, booleans, validation lists and date/time fields retain their types. Dates use the workbook's 1900/1904 epoch. Errors remain visible as Excel errors; frozen results are identified. An optional calculation date controls TODAY/NOW. Reset restores the workbook inputs. The calculation and the supported formula subset are identical to the Python module export; this does not reproduce an entire spreadsheet or execute macros.

API: `GET /api/sheets/{id}/export.app.py`. MCP: `data_workbook` with `action="export"`, `workbook_id` and `format="app"`. Omit `format`, or use `"python"`, for the existing calculation module. Exports saved by MCP and downloaded through HTTP contain the same UTF-8 bytes, including on Windows.

Example: a machining quotation with quantities, unit price, material, VAT, urgency and order date produces a total and a working-day delivery date. The invented fixture gives 617.83 EUR and 2026-11-10 for 24 steel units, without urgency. The exported form and native MCP agree. Ten export tests cover both epochs, mixed types, frozen values and errors, JavaScript input conversion, native API/tool export and an actual server started with `python -I -S`. Feature 2011 is a candidate awaiting review and integration, dependent on corrections 2006.
