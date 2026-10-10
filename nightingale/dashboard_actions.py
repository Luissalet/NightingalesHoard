"""Typed, reversible dashboard actions.

Every change to an item-based dashboard (add/remove/move an item, change a KPI's query or a chart, retitle,
filter, lay out) is one *action*: a small JSON object with a ``type`` and a strict schema. A single pure
reducer, :func:`apply_action`, turns ``(state, action)`` into ``(new_state, inverse_action)``; the inverse is
itself a valid action, so undo is just "apply the inverse" and the UI, the REST API and the MCP tool share one
code path (``Services.dashboard_apply`` and friends persist the log).

A dashboard *state* is ``{"name", "items", "filter", "layout"}``. Items carry a stable ``id`` so actions can
name them; dashboards saved before actions existed get ids on first touch (``normalize_state``).
"""

from __future__ import annotations

import copy
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

MAX_ITEMS = 60
MAX_COLUMNS = 4
#: item fields an ``update_item`` may set (``type``/``id`` are immutable: remove and add a new item instead)
ITEM_FIELDS = {
    "chart": ("chart_id", "title", "span"),
    "kpi": ("dataset", "expr", "label", "title", "span"),
}


class ActionError(ValueError):
    """An action that cannot be applied. ``code`` is machine-readable, the message says what to change."""

    def __init__(self, code: str, message: str, path: str = ""):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.path = path

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "path": self.path}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChartItem(_Strict):
    id: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9_-]{1,40}$", description="Stable item id; generated when omitted.")
    type: Literal["chart"]
    chart_id: int = Field(..., ge=1, description="A saved chart (data_chart / chart list).")
    title: Optional[str] = Field(None, max_length=200)
    span: Optional[int] = Field(None, ge=1, le=MAX_COLUMNS, description="Columns the item spans.")


class KpiItem(_Strict):
    id: Optional[str] = Field(None, pattern=r"^[A-Za-z0-9_-]{1,40}$")
    type: Literal["kpi"]
    dataset: str = Field(..., min_length=1, max_length=200)
    expr: str = Field(..., min_length=1, max_length=500, description="A SQL aggregate expression, e.g. SUM(amount).")
    label: Optional[str] = Field(None, max_length=200)
    title: Optional[str] = Field(None, max_length=200)
    span: Optional[int] = Field(None, ge=1, le=MAX_COLUMNS)


Item = Annotated[Union[ChartItem, KpiItem], Field(discriminator="type")]


class AddItem(_Strict):
    type: Literal["add_item"]
    item: Item
    index: Optional[int] = Field(None, ge=0, description="Position to insert at; appended when omitted.")


class RemoveItem(_Strict):
    type: Literal["remove_item"]
    item_id: str = Field(..., min_length=1)


class MoveItem(_Strict):
    type: Literal["move_item"]
    item_id: str = Field(..., min_length=1)
    to_index: int = Field(..., ge=0)


class UpdateItem(_Strict):
    type: Literal["update_item"]
    item_id: str = Field(..., min_length=1)
    set: dict[str, Any] = Field(default_factory=dict, description="Fields to set: chart_id | dataset | expr | label | title | span.")
    unset: list[str] = Field(default_factory=list, description="Optional fields to clear: label | title | span.")


class SetName(_Strict):
    type: Literal["set_name"]
    name: str = Field(..., min_length=1, max_length=200)


class SetFilter(_Strict):
    type: Literal["set_filter"]
    filter: dict[str, Any] = Field(..., description="The dashboard's filter object (replaced as a whole; {} clears it).")


class SetLayout(_Strict):
    type: Literal["set_layout"]
    columns: int = Field(..., ge=1, le=MAX_COLUMNS)


Action = Annotated[Union[AddItem, RemoveItem, MoveItem, UpdateItem, SetName, SetFilter, SetLayout], Field(discriminator="type")]
_ADAPTER = TypeAdapter(Action)

ACTION_DOCS = {
    "add_item": "Add a chart or KPI item (at `index`, else the end). Inverse: remove_item.",
    "remove_item": "Remove an item by id. Inverse: add_item at its old position.",
    "move_item": "Move an item to a new index. Inverse: move_item back.",
    "update_item": "Change an item's query/chart/title/span (set and/or unset fields). Inverse: update_item with the old values.",
    "set_name": "Rename the dashboard (its title). Inverse: set_name with the old name.",
    "set_filter": "Replace the dashboard filter object. Inverse: set_filter with the old filter.",
    "set_layout": "Set the number of grid columns (1-4). Inverse: set_layout with the old value.",
}


def action_schemas() -> dict:
    """The JSON schema of every action (one entry per ``type``) plus the combined schema."""
    combined = _ADAPTER.json_schema()
    per_type = {}
    for cls in (AddItem, RemoveItem, MoveItem, UpdateItem, SetName, SetFilter, SetLayout):
        name = cls.model_fields["type"].annotation.__args__[0]
        per_type[name] = {"description": ACTION_DOCS[name], "schema": cls.model_json_schema()}
    return {"actions": per_type, "schema": combined,
            "state": {"name": "string", "items": "list of items (each has a stable id)", "filter": "object",
                      "layout": {"columns": f"1-{MAX_COLUMNS}"}}}


_TAGS = set(ACTION_DOCS) | {"chart", "kpi"}


def _format_errors(exc: ValidationError) -> ActionError:
    parts, first_path = [], ""
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"] if str(p) not in _TAGS)
        first_path = first_path or loc
        parts.append(f"{loc or 'action'}: {err['msg']}")
    return ActionError("invalid_action", "; ".join(parts[:5]) or "invalid action", first_path)


def parse_action(raw: Any) -> dict:
    """Validate ``raw`` against the action schemas; returns the normalized dict. Raises :class:`ActionError`."""
    if not isinstance(raw, dict):
        raise ActionError("invalid_action", "an action must be a JSON object with a `type`")
    kind = raw.get("type")
    if kind not in ACTION_DOCS:
        raise ActionError("unknown_action", f"unknown action type {kind!r}; use one of {sorted(ACTION_DOCS)}", "type")
    try:
        return _ADAPTER.validate_python(raw).model_dump(exclude_none=True)
    except ValidationError as exc:
        raise _format_errors(exc) from None


# ---- state ------------------------------------------------------------------------------------------------
def normalize_state(name: str, spec: dict) -> tuple[dict, bool]:
    """Build a state from a stored dashboard (``name`` column + ``spec_json``). Legacy items without an id get
    ``i1, i2...``. Returns ``(state, changed)`` where ``changed`` says ids/layout had to be filled in."""
    items = [dict(i) for i in (spec.get("items") or []) if isinstance(i, dict)]
    changed = len(items) != len(spec.get("items") or [])
    used = {i["id"] for i in items if isinstance(i.get("id"), str) and i["id"]}
    for item in items:
        if not isinstance(item.get("id"), str) or not item["id"]:
            item["id"] = _new_id(used)
            changed = True
    layout = spec.get("layout") if isinstance(spec.get("layout"), dict) else {}
    if "columns" not in layout:
        layout = {"columns": 3}
        changed = True
    return {"name": name, "items": items, "filter": spec.get("filter") or {}, "layout": layout}, changed


def state_to_spec(state: dict) -> dict:
    return {"items": state["items"], "filter": state["filter"], "layout": state["layout"]}


def _new_id(used: set[str]) -> str:
    n = 1
    while f"i{n}" in used:
        n += 1
    used.add(f"i{n}")
    return f"i{n}"


def _find(state: dict, item_id: str) -> int:
    for pos, item in enumerate(state["items"]):
        if item["id"] == item_id:
            return pos
    raise ActionError("unknown_item", f"no item with id {item_id!r} in this dashboard "
                      f"(ids: {[i['id'] for i in state['items']]})", "item_id")


# ---- the reducer ------------------------------------------------------------------------------------------
def apply_action(state: dict, raw_action: Any) -> tuple[dict, dict, dict]:
    """Apply one action. Returns ``(new_state, normalized_action, inverse_action)``; ``state`` is not mutated.

    The normalized action is what gets logged (an ``add_item`` carries the id that was generated), and applying
    the inverse to ``new_state`` gives back exactly ``state``."""
    action = parse_action(raw_action)
    new = copy.deepcopy(state)
    kind = action["type"]

    if kind == "add_item":
        if len(new["items"]) >= MAX_ITEMS:
            raise ActionError("too_many_items", f"a dashboard holds at most {MAX_ITEMS} items")
        item = dict(action["item"])
        used = {i["id"] for i in new["items"]}
        if "id" in item and item["id"] in used:
            raise ActionError("duplicate_item", f"an item with id {item['id']!r} already exists", "item.id")
        if "id" not in item:
            item["id"] = _new_id(used)
        index = action.get("index", len(new["items"]))
        if index > len(new["items"]):
            raise ActionError("bad_index", f"index {index} is past the end ({len(new['items'])} items)", "index")
        new["items"].insert(index, item)
        action = {"type": "add_item", "item": item, "index": index}
        inverse = {"type": "remove_item", "item_id": item["id"]}

    elif kind == "remove_item":
        pos = _find(new, action["item_id"])
        item = new["items"].pop(pos)
        inverse = {"type": "add_item", "item": item, "index": pos}

    elif kind == "move_item":
        pos = _find(new, action["item_id"])
        to = action["to_index"]
        if to >= len(new["items"]):
            raise ActionError("bad_index", f"to_index {to} is out of range (0-{len(new['items']) - 1})", "to_index")
        new["items"].insert(to, new["items"].pop(pos))
        inverse = {"type": "move_item", "item_id": action["item_id"], "to_index": pos}

    elif kind == "update_item":
        pos = _find(new, action["item_id"])
        item = new["items"][pos]
        allowed = ITEM_FIELDS[item["type"]]
        sets, unsets = action.get("set", {}), action.get("unset", [])
        if not sets and not unsets:
            raise ActionError("empty_update", "update_item needs `set` and/or `unset`", "set")
        for key in list(sets) + list(unsets):
            if key not in allowed:
                raise ActionError("bad_field", f"a {item['type']} item cannot change {key!r}; changeable: {list(allowed)}", key)
        for key in unsets:
            if key in sets:
                raise ActionError("bad_field", f"{key!r} is both set and unset", key)
            if key in ("chart_id", "dataset", "expr"):
                raise ActionError("bad_field", f"{key!r} is required and cannot be unset", key)
        probe = {**{k: v for k, v in item.items() if k not in unsets}, **sets}
        try:
            validated = parse_action({"type": "add_item", "item": probe})["item"]
        except ActionError as exc:
            raise ActionError("invalid_action", f"the updated item would be invalid: {exc}", exc.path) from None
        before = {k: item[k] for k in list(sets) + list(unsets) if k in item}
        inverse = {"type": "update_item", "item_id": item["id"],
                   "set": before, "unset": [k for k in list(sets) + list(unsets) if k not in item]}
        new["items"][pos] = validated

    elif kind == "set_name":
        inverse = {"type": "set_name", "name": new["name"]}
        new["name"] = action["name"]

    elif kind == "set_filter":
        inverse = {"type": "set_filter", "filter": new["filter"]}
        new["filter"] = action["filter"]

    else:  # set_layout
        inverse = {"type": "set_layout", "columns": new["layout"].get("columns", 3)}
        new["layout"] = {**new["layout"], "columns": action["columns"]}

    return new, action, inverse
