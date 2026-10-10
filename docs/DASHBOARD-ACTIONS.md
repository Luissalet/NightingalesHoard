# Dashboard actions

Every change to an item-based dashboard is a typed **action**: a JSON object with a `type`, validated against a
strict schema, applied by one pure reducer (`nightingale/dashboard_actions.py`), recorded in a per-dashboard action
log and reversible. The Dashboards page, the REST API and the `data_dashboard` MCP tool all go through the same
service methods, so a person and an assistant edit the same way and see the same history.

## State

`{"name", "items", "filter", "layout": {"columns": 1-4}}`. Every item has a stable `id` (`i1`, `i2`, ...) and is a
`chart` item (`chart_id`) or a `kpi` item (`dataset`, `expr`, `label`); both accept an optional `title` and `span`.

## Actions

| type | fields | inverse |
|---|---|---|
| `add_item` | `item`, optional `index` (appended when omitted; the id is generated when omitted) | `remove_item` |
| `remove_item` | `item_id` | `add_item` at the old position |
| `move_item` | `item_id`, `to_index` | `move_item` back |
| `update_item` | `item_id`, `set` {chart_id / dataset / expr / label / title / span}, `unset` [label / title / span] | `update_item` with the old values |
| `set_name` | `name` | `set_name` with the old name |
| `set_filter` | `filter` (object, replaced as a whole) | `set_filter` with the old one |
| `set_layout` | `columns` (1-4) | `set_layout` with the old value |

The full JSON schemas: `GET /api/dashboards/action-schemas`, or `data_dashboard` with `action="action_schemas"`.

## Errors

A rejected action changes nothing and leaves no log entry. The error names a code and what to change, e.g.
`[unknown_item] no item with id 'x' in this dashboard (ids: ['i1'])`. Codes: `unknown_action`, `invalid_action`
(schema violation, with the field path), `unknown_item`, `bad_index`, `duplicate_item`, `bad_field`, `empty_update`,
`too_many_items` (60), `unknown_chart`, `unknown_dataset`, `nothing_to_undo`, `nothing_to_redo`, `history_conflict`.
Over REST they are HTTP 400 (unknown dashboard: 404).

## Undo, redo, history

Each applied action is stored with its inverse. Undo applies the latest live action's inverse and flags it undone; redo
re-applies the earliest undone action. A new action discards the undone tail. `history` returns the log (oldest first,
each entry with its summary, source `ui`/`agent`, and `undone` flag), the dashboard's `initial_state` snapshot, the
current `state`, and `can_undo` / `can_redo`.

| | REST | MCP (`data_dashboard`) |
|---|---|---|
| apply | `POST /api/dashboards/{id}/actions` `{"action": {...}}` | `action="apply_action"`, `dashboard_id`, `change` |
| undo / redo | `POST /api/dashboards/{id}/undo` / `redo` | `action="undo"` / `"redo"` |
| history | `GET /api/dashboards/{id}/history?limit=` | `action="history"`, optional `limit` |
| schemas | `GET /api/dashboards/action-schemas` | `action="action_schemas"` |

The older `POST /api/dashboards/{id}/items` and `action="add"` still work: they are the `add_item` action.

## Existing dashboards

Dashboards saved before the log existed are migrated the first time the app starts or the dashboard is read: items
get stable ids, the grid defaults to 3 columns, and the dashboard's state at that moment is stored as its initial
snapshot. Nothing is lost and no data changes beyond those added fields.

## Limits

- Item references (chart id, dataset) are checked when an item is added or changed, not when undoing or redoing.
- If a dashboard is changed outside the action log (for example by an import) in a way an inverse cannot apply to,
  undo fails with `history_conflict` rather than guessing.
- Dashboards as code (YAML) keep their own 20-version history and are not part of this log.
