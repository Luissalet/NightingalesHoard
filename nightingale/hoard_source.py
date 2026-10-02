"""Source kind "hoard": the records another app of the family returns from one of its tools.

``{app, tool, args, list_path?}`` is called through the hub (``family.call``), the list of records is found in
the answer, flattened to one row per record and handed to the normal JSON ingest. Presets name the common
cases. Nothing here touches the database: ``Services.ingest_hoard`` does that.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .hoard_link import family
from .workbench.engine import DataError

APP_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,59}$")
TOOL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
MAX_ROWS_DEFAULT = 20000
#: Keys that usually hold the records, tried before "the longest list".
LIST_KEYS = ("items", "rows", "records", "results", "entries", "transactions", "shipments", "data", "list", "apps")

PRESETS: dict[str, dict[str, Any]] = {
    "ledger_transactions": {
        "title": {"es": "Movimientos de Ledger", "en": "Ledger transactions"},
        "description": {"es": "Todos los movimientos (importes en céntimos, negativo = gasto), de 200 en 200 hasta 5000.",
                        "en": "Every entry (amounts in cents, negative = expense), 200 at a time up to 5000."},
        "name": "ledger_transactions", "app": "ledger", "tool": "list_entries", "args": {"order": "desc"}, "list_path": "items",
        "paginate": {"size": 200, "max_rows": 5000}, "verified": True,
    },
    "phileas_shipments": {
        "title": {"es": "Envíos entregados de Phileas", "en": "Phileas delivered shipments"},
        "description": {"es": "Historial de envíos entregados con los días de tránsito (días naturales de enviado a entregado).",
                        "en": "Delivered shipments with their transit days (calendar days from shipped to delivered)."},
        "name": "phileas_shipments", "app": "phileas", "tool": "shipments_list", "args": {"filter": "delivered", "limit": 500},
        "list_path": "shipments", "epoch_columns": "_ts", "derive": [{"name": "transit_days", "from": "shipped_ts", "to": "delivered_ts"}],
        "verified": True,
    },
    "argus_app_time": {
        "title": {"es": "Tiempo por aplicación de Argus", "en": "Argus time per app"},
        "description": {"es": "Tiempo de pantalla por aplicación. SIN VERIFICAR: el nombre de la herramienta no se ha comprobado contra Argus.",
                        "en": "Screen time per app. UNVERIFIED: the tool name has not been checked against Argus."},
        "name": "argus_app_time", "app": "argus", "tool": "screen_app_time", "args": {}, "list_path": None, "verified": False,
    },
}


def list_presets(lang: str = "") -> list[dict[str, Any]]:
    out = []
    for key, p in PRESETS.items():
        item = {"id": key, **{k: p[k] for k in ("name", "app", "tool", "args", "list_path", "verified") if k in p}}
        item["title"], item["description"] = p["title"], p["description"]
        if lang in ("es", "en"):
            item["title"], item["description"] = p["title"][lang], p["description"][lang]
        out.append(item)
    return out


def resolve_spec(preset: Optional[str], app: Optional[str], tool: Optional[str], args: Optional[dict], list_path: Optional[str],
                 options: Optional[dict]) -> dict[str, Any]:
    """Merge a preset with explicit fields (explicit wins) and validate. Returns the stored source spec."""
    base: dict[str, Any] = {}
    if preset:
        if preset not in PRESETS:
            raise DataError(f"unknown preset {preset!r}; choose from {', '.join(PRESETS)}")
        base = json.loads(json.dumps({k: v for k, v in PRESETS[preset].items() if k not in ("title", "description")}))
    options = dict(options or {})
    spec: dict[str, Any] = {
        "app": (app or base.get("app") or "").strip().lower(), "tool": (tool or base.get("tool") or "").strip(),
        "args": args if args is not None else base.get("args", {}),
        "list_path": (list_path if list_path is not None else base.get("list_path")) or None,
    }
    for key in ("paginate", "epoch_columns", "derive"):
        value = options.get(key, base.get(key))
        if value:
            spec[key] = value
    if "max_rows" in options:
        spec["max_rows"] = options["max_rows"]
    if base.get("name"):
        spec["preset"] = preset
    if not spec["app"] or not APP_RE.match(spec["app"]):
        raise DataError("hoard source: app is required (an app id such as 'ledger')")
    if not spec["tool"] or not TOOL_RE.match(spec["tool"]):
        raise DataError("hoard source: tool is required (a tool name of that app)")
    if not isinstance(spec["args"], dict):
        raise DataError("hoard source: args must be an object")
    if spec["list_path"] is not None and not isinstance(spec["list_path"], str):
        raise DataError("hoard source: list_path must be text such as 'items' or 'data.rows'")
    pag = spec.get("paginate")
    if pag is not None:
        if not isinstance(pag, dict):
            raise DataError("hoard source: paginate must be an object {size, max_rows?, limit_arg?, offset_arg?}")
        size = _int(pag.get("size"), 1, 5000, "paginate.size")
        spec["paginate"] = {"size": size, "max_rows": _int(pag.get("max_rows", MAX_ROWS_DEFAULT), size, 200000, "paginate.max_rows"),
                            "limit_arg": str(pag.get("limit_arg") or "limit"), "offset_arg": str(pag.get("offset_arg") or "offset")}
    spec["max_rows"] = _int(spec.get("max_rows", MAX_ROWS_DEFAULT), 1, 200000, "max_rows")
    return spec


def _int(value: Any, low: int, high: int, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise DataError(f"hoard source: {label} must be a whole number") from None
    if not low <= number <= high:
        raise DataError(f"hoard source: {label} must be between {low} and {high}")
    return number


# ---------- finding the records ----------
def _as_json(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in "[{":
            try:
                return json.loads(text)
            except ValueError:
                return value
    if isinstance(value, dict) and isinstance(value.get("content"), list) and set(value) <= {"content", "isError", "structuredContent"}:
        for part in value["content"]:  # an MCP-style answer: the JSON is in the text part
            if isinstance(part, dict) and part.get("type") == "text":
                parsed = _as_json(part.get("text"))
                if not isinstance(parsed, str):
                    return parsed
    return value


def _candidates(value: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, list]]:
    found: list[tuple[str, list]] = []
    if isinstance(value, dict) and depth <= 3:
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, list) and child and all(isinstance(x, dict) for x in child):
                found.append((path, child))
            elif isinstance(child, dict):
                found += _candidates(child, path, depth + 1)
    return found


def _dig(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            keys = ", ".join(list(current)[:12]) if isinstance(current, dict) else type(current).__name__
            raise DataError(f"list_path {path!r} not found in the answer (at {part!r}; available: {keys})")
    return current


def _as_records(value: Any, path: str) -> list[dict]:
    if isinstance(value, dict):  # {key: {...}} or {key: scalar} -> one record per key
        return [({"key": k, **v} if isinstance(v, dict) else {"key": k, "value": v}) for k, v in value.items()]
    if isinstance(value, list):
        return [x if isinstance(x, dict) else {"value": x} for x in value]
    raise DataError(f"list_path {path!r} is not a list of records (it is {type(value).__name__})")


def extract_records(result: Any, list_path: Optional[str] = None) -> tuple[list[dict], str]:
    """The records of a tool answer and the path they were found at ('' for the answer itself)."""
    data = _as_json(result)
    if list_path:
        return _as_records(_dig(data, list_path), list_path), list_path
    if isinstance(data, list):
        if not data:
            raise DataError("the tool answered with an empty list: there are no records to ingest yet")
        return _as_records(data, ""), ""
    found = _candidates(data)
    if not found:
        if isinstance(data, dict) and any(isinstance(v, list) and not v for v in data.values()):
            raise DataError("the tool answered with an empty list: there are no records to ingest yet")
        keys = ", ".join(list(data)[:12]) if isinstance(data, dict) else type(data).__name__
        raise DataError(f"no list of records found in the answer (keys: {keys}); pass list_path, e.g. 'items'")
    for key in LIST_KEYS:
        for path, rows in found:
            if path == key or path.endswith("." + key):
                return rows, path
    path, rows = max(found, key=lambda item: len(item[1]))
    return rows, path


# ---------- shaping the rows ----------
def _flat(record: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in record.items():
        name = f"{prefix}_{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(_flat(value, name))
        elif isinstance(value, list):
            out[name] = json.dumps(value, ensure_ascii=False, default=str)
        else:
            out[name] = value
    return out


def _iso(epoch: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def shape_rows(records: list[dict], spec: dict[str, Any]) -> list[dict]:
    """One flat row per record: nested objects become ``a_b`` columns, lists JSON text, epoch columns become timestamps,
    derived day counts are added, and a column that mixes text and numbers becomes text."""
    rows = [_flat(r) for r in records]
    suffix = spec.get("epoch_columns")
    epoch_names = set()
    if suffix:
        wanted = [suffix] if isinstance(suffix, str) else list(suffix)
        for row in rows:
            for key in row:
                if any(key == w or key.endswith(w) for w in wanted):
                    epoch_names.add(key)
    for derive in spec.get("derive") or []:
        if not isinstance(derive, dict) or not derive.get("name") or not derive.get("from") or not derive.get("to"):
            continue
        for row in rows:
            try:
                row[derive["name"]] = round((float(row[derive["to"]]) - float(row[derive["from"]])) / 86400.0, 1)
            except (KeyError, TypeError, ValueError):
                row[derive["name"]] = None
            if row[derive["name"]] is not None and row[derive["name"]] < 0:
                row[derive["name"]] = None
    for row in rows:
        for key in epoch_names:
            if key in row and row[key] is not None:
                row[key] = _iso(row[key])
    kinds: dict[str, set] = {}
    for row in rows:
        for key, value in row.items():
            if value is not None:
                kinds.setdefault(key, set()).add("bool" if isinstance(value, bool) else "num" if isinstance(value, (int, float)) else "text")
    mixed = {k for k, v in kinds.items() if len(v) > 1}
    if mixed:
        for row in rows:
            for key in mixed:
                if row.get(key) is not None:
                    row[key] = str(row[key]).lower() if isinstance(row[key], bool) else str(row[key])
    return rows


# ---------- calling the app ----------
def _hub_call(app: str, tool: str, args: dict, timeout: float) -> dict:
    return family.call(app, tool, args, timeout=timeout)


def _answer(app: str, tool: str, args: dict, timeout: float, call: Callable) -> Any:
    reply = call(app, tool, args, timeout)
    if not isinstance(reply, dict) or not reply.get("ok"):
        detail = (reply or {}).get("error") if isinstance(reply, dict) else None
        raise DataError(f"{app}.{tool} did not answer: {str(detail or 'unknown error')[:300]}")
    result = reply.get("result")
    if isinstance(result, dict) and result.get("ok") is False and result.get("error"):
        raise DataError(f"{app}.{tool}: {str(result['error'])[:300]}")
    return result


def fetch(spec: dict[str, Any], *, timeout: float = 90.0, call: Optional[Callable] = None) -> dict[str, Any]:
    """Call the tool (paging when asked) and return ``{rows, list_path, pages, truncated}``; raises ``DataError``."""
    call = call or _hub_call
    app, tool, args = spec["app"], spec["tool"], dict(spec.get("args") or {})
    pag, cap = spec.get("paginate"), spec.get("max_rows", MAX_ROWS_DEFAULT)
    if not pag:
        records, path = extract_records(_answer(app, tool, args, timeout, call), spec.get("list_path"))
        truncated = len(records) > cap
        return {"rows": shape_rows(records[:cap], spec), "list_path": path, "pages": 1, "truncated": truncated}
    size, limit_cap = pag["size"], min(pag["max_rows"], cap)
    records: list[dict] = []
    path, pages, truncated = "", 0, False
    while len(records) < limit_cap:
        page_args = {**args, pag["limit_arg"]: size, pag["offset_arg"]: len(records)}
        page, path = extract_records(_answer(app, tool, page_args, timeout, call), spec.get("list_path") or (path or None))
        pages += 1
        records += page
        if len(page) < size:
            break
    else:
        truncated = True  # stopped at the cap, there may be more
    if len(records) > limit_cap:
        records, truncated = records[:limit_cap], True
    return {"rows": shape_rows(records, spec), "list_path": path, "pages": pages, "truncated": truncated}
