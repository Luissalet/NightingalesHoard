"""SQLite state: work log (computations), notebook cells, settings.

Stdlib sqlite3 only, WAL mode. No FastAPI imports here: this module is
part of the core and is used directly by both the HTTP layer and tests.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS computations (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    engine TEXT NOT NULL,
    operation TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    ok INTEGER NOT NULL,
    error TEXT,
    elapsed_ms REAL NOT NULL,
    source TEXT NOT NULL,
    chart_path TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_computations_engine ON computations(engine);
CREATE INDEX IF NOT EXISTS idx_computations_source ON computations(source);

CREATE TABLE IF NOT EXISTS cells (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    engine TEXT NOT NULL,
    input TEXT NOT NULL,
    result_json TEXT,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_local = threading.local()
_lock = threading.Lock()
BUSY_TIMEOUT_S = 15.0


def connect(data_dir: Path) -> sqlite3.Connection:
    """Open (or create) the app database with WAL mode and row access by name."""
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "app.sqlite"
    # An explicit busy timeout: the work log is written by request threads, the notebook and the sandbox bookkeeping at
    # once, and without one a brief lock became "database is locked" for the caller.
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=BUSY_TIMEOUT_S)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_S * 1000)}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    with _lock:
        conn.executescript(_SCHEMA)
        conn.commit()
    return conn


MAX_LOG_JSON = 20000


def _capped_json(value: Any) -> str:
    """JSON for the log, never cut mid-document (a cut string would no longer parse)."""
    text = json.dumps(value, default=str, ensure_ascii=False)
    if len(text) <= MAX_LOG_JSON:
        return text
    return json.dumps({"truncated": True, "preview": text[: MAX_LOG_JSON - 200]}, ensure_ascii=False)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _next_id(conn: sqlite3.Connection) -> str:
    cur = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM computations")
    n = cur.fetchone()["n"]
    return f"L-{n:06d}"


def log_computation(
    conn: sqlite3.Connection,
    *,
    engine: str,
    operation: str,
    input_data: Any,
    output_data: Any,
    ok: bool,
    error: Optional[str],
    elapsed_ms: float,
    source: str,
    chart_path: Optional[str] = None,
) -> str:
    with _lock:
        cid = _next_id(conn)
        conn.execute(
            "INSERT INTO computations "
            "(id, engine, operation, input_json, output_json, ok, error, elapsed_ms, source, chart_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid,
                engine,
                operation,
                _capped_json(input_data),
                _capped_json(output_data) if output_data is not None else None,
                1 if ok else 0,
                error,
                elapsed_ms,
                source,
                chart_path,
                _now(),
            ),
        )
        conn.commit()
    return cid


def get_computation(conn: sqlite3.Connection, cid: str) -> Optional[dict]:
    with _lock:
        row = conn.execute("SELECT * FROM computations WHERE id = ?", (cid,)).fetchone()
    return _row_to_dict(row) if row else None


def list_computations(
    conn: sqlite3.Connection,
    *,
    limit: int = 10,
    engine: Optional[str] = None,
    query: Optional[str] = None,
    source: Optional[str] = None,
    exclude_engine: Optional[str] = None,
) -> list[dict]:
    sql = "SELECT * FROM computations WHERE 1=1"
    params: list[Any] = []
    if engine:
        sql += " AND engine = ?"
        params.append(engine)
    if exclude_engine:
        sql += " AND engine != ?"
        params.append(exclude_engine)
    if source:
        sql += " AND source = ?"
        params.append(source)
    if query:
        sql += " AND (operation LIKE ? OR input_json LIKE ? OR id = ?)"
        like = f"%{query}%"
        params.extend([like, like, query])
    sql += " ORDER BY seq DESC LIMIT ?"
    params.append(max(1, min(limit, 200)))
    with _lock:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def _clip(value: Any, n: int) -> Optional[str]:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= n else text[:n] + "…"


def brief(item: dict, full: bool = False) -> dict:
    """A work-log entry the way a model should see it: small, with its id."""
    n_in, n_out = (2000, 4000) if full else (200, 300)
    out = {
        "id": item["id"],
        "cite": f"[{item['id']}]",
        "engine": item["engine"],
        "operation": item["operation"],
        "ok": item["ok"],
        "source": item["source"],
        "created_at": item["created_at"],
        "input": _clip(item.get("input"), n_in),
    }
    if item["ok"]:
        out["result"] = _clip(item.get("output"), n_out)
    else:
        out["error"] = _clip(item.get("error"), 300)
    if item.get("chart_path"):
        # a small flag, never the path itself (that is a local filesystem
        # detail); the image is fetched by id through /api/charts/<id>
        out["has_chart"] = True
    return out


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("input_json", "output_json"):
        if d.get(key):
            try:
                d[key[: -len("_json")]] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                d[key[: -len("_json")]] = None
        else:
            d[key[: -len("_json")]] = None
        d.pop(key, None)
    d["ok"] = bool(d["ok"])
    return d


# --- notebook cells -------------------------------------------------------

def list_cells(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM cells ORDER BY position ASC").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["result"] = json.loads(d["result_json"]) if d.get("result_json") else None
        d.pop("result_json", None)
        out.append(d)
    return out


def get_cell(conn: sqlite3.Connection, cell_id: int) -> Optional[dict]:
    with _lock:
        row = conn.execute("SELECT * FROM cells WHERE id = ?", (cell_id,)).fetchone()
    if row is None:
        return None
    cell = dict(row)
    cell["result"] = json.loads(cell.pop("result_json")) if cell.get("result_json") else None
    cell.pop("result_json", None)
    return cell


def patch_cell(conn: sqlite3.Connection, cell_id: int, patch: dict) -> Optional[dict]:
    """Update supplied fields in one SQL statement, preserving other writers' fields."""
    fields = {key: value for key, value in patch.items() if key in ("engine", "input")}
    if not fields:
        return get_cell(conn, cell_id)
    with _lock:
        assignments = ", ".join(key + " = ?" for key in fields)
        conn.execute("UPDATE cells SET " + assignments + ", result_json = NULL, updated_at = ? WHERE id = ?",
                     [*fields.values(), _now(), cell_id])
        conn.commit()
    return get_cell(conn, cell_id)


def finish_cell(conn: sqlite3.Connection, cell: dict, result: Any) -> Optional[dict]:
    """A slow evaluation cannot replace the result of a later cell edit."""
    with _lock:
        conn.execute("UPDATE cells SET result_json = ?, updated_at = ? WHERE id = ? AND engine = ? AND input = ?",
                     (json.dumps(result, default=str), _now(), cell["id"], cell["engine"], cell["input"]))
        conn.commit()
    return get_cell(conn, cell["id"])


def add_cell(conn: sqlite3.Connection, *, engine: str, input_text: str) -> dict:
    with _lock:
        pos_row = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM cells").fetchone()
        pos = pos_row["p"]
        now = _now()
        cur = conn.execute(
            "INSERT INTO cells (engine, input, result_json, position, created_at, updated_at) "
            "VALUES (?, ?, NULL, ?, ?, ?)",
            (engine, input_text, pos, now, now),
        )
        conn.commit()
        cell_id = cur.lastrowid
    return {"id": cell_id, "engine": engine, "input": input_text, "result": None, "position": pos}


def update_cell(conn: sqlite3.Connection, cell_id: int, *, result: Any) -> None:
    with _lock:
        conn.execute(
            "UPDATE cells SET result_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(result, default=str), _now(), cell_id),
        )
        conn.commit()


def delete_cell(conn: sqlite3.Connection, cell_id: int) -> bool:
    with _lock:
        cur = conn.execute("DELETE FROM cells WHERE id = ?", (cell_id,))
        conn.commit()
        return cur.rowcount > 0


# --- settings --------------------------------------------------------------

def get_setting(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with _lock:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
