# Copilot and notebook API

The browser uses the existing same-origin UI routes. The MCP adapter uses the paired agent routes and the existing app token. No new account, permissions flow, chat store or agent loop is added.

## Common copilot

HoardLink installs GET /api/copilot/info, GET /api/copilot/panel.js, GET /api/copilot/launcher.js and POST /api/copilot/proxy. It injects the shared launcher into HTML and relays to the configured Faustus service. Conversations, drafts and turns remain in Faustus. Credentials stay in the server. The data-space identity is persisted in the app data directory.

The thin browser hook window.hoardCopilot.context supplies the current section, locale, one view.editor with the visible form fields, selected text and native references. Password and hidden inputs are excluded. Laplace has no domain projects: project is null. Cells use their SQLite ids, computations their L-... ids and datasets their registered names. A missing object is omitted; new notebook and dataset forms keep their draft fields without inventing an id. Context resolution never creates a calculation or changes the work-log count.

## Native operations

Each operation is POST /api/ui/<name> and POST /api/agent/<name>, with the same implementation. The seven operations below are also MCP tools; all thirteen previous tools remain available.

| Name | Input | Result |
| --- | --- | --- |
| notebook_cells | {} | Existing cells, inputs and results |
| notebook_cell_get | {id} | One existing cell |
| notebook_cell_create | {engine,input} | Add/evaluate a cell with calc, math, units or dates |
| notebook_cell_patch | {id,patch:{input?,engine?}} | Apply only supplied fields and evaluate the current merged cell |
| notebook_cell_delete | {id} | Remove that cell, leaving the others intact |
| computation_get | {id:"L-..."} | Original saved input/output, without rerunning |
| app_context | {kind?,object_id?} | project:null and verified active_objects; empty for unavailable references |

Metadata reads do not create artificial work-log entries. Calculations and notebook writes retain the normal source attribution. A failed evaluation still returns the saved cell id and its error so the caller does not blindly create another cell.

Cell edits use one SQL update for the supplied fields. An evaluation result is stored only while its input and engine still match; a slow response cannot replace the result of a later edit. The editor submits changed fields only and merges refreshed untouched fields without remounting the form. If the cell is removed while being edited, its pending text remains visible as an unavailable draft until the user cancels or copies it.

Settings submit only changed URL/model fields. HoardLink update_json serializes the read/merge/write and atomically replaces the config, retaining unrelated fields and concurrent sibling changes. The existing Ask your data feature remains a bounded shared-model call; it is not a second copilot.

At turn end the adapter refreshes the notebook, datasets, work log, activity and settings without recreating open forms. Other calculation drafts stay in their current components. The panel follows the app language and theme through its public properties.

The scripted QA proves the real panel, relay and native tool/editor flow, not the global model's autonomous reasoning quality. The application continues working when Faustus is unavailable; conversation controls report the common connection error while the editor remains usable.
