"""SQLite metadata store: sources, datasets, versions/steps, quality rules and
results, charts, dashboards, models and the analysis log.

Adapted from Laplace's Hoard's `db.py` pattern (same author, MIT): stdlib
sqlite3 only, WAL mode, row access by name. The actual columnar data lives in
DuckDB (`workbench.py`); this file only ever stores small JSON metadata plus
the analysis log, so it is safe to hit from any request without touching the
single DuckDB connection.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '{}',
    incremental_key TEXT,
    created_at TEXT NOT NULL,
    last_refreshed_at TEXT
);

CREATE TABLE IF NOT EXISTS datasets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    source_id INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    current_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    parent_version INTEGER,
    op TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    sql TEXT NOT NULL DEFAULT '',
    table_name TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    columns_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(dataset_id, version)
);

CREATE TABLE IF NOT EXISTS quality_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quality_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL REFERENCES quality_rules(id) ON DELETE CASCADE,
    dataset_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    checked INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    sample_json TEXT NOT NULL DEFAULT '[]',
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS charts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    dataset_id INTEGER REFERENCES datasets(id) ON DELETE CASCADE,
    spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dashboards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    dataset_id INTEGER REFERENCES datasets(id) ON DELETE CASCADE,
    dataset_version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    target TEXT,
    features_json TEXT NOT NULL DEFAULT '[]',
    params_json TEXT NOT NULL DEFAULT '{}',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    seed INTEGER NOT NULL DEFAULT 42,
    artifact_path TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    op_id TEXT UNIQUE NOT NULL,
    op TEXT NOT NULL,
    source TEXT NOT NULL,
    dataset TEXT,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_summary TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL,
    error TEXT,
    elapsed_ms REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_op ON log(op);
CREATE INDEX IF NOT EXISTS idx_log_source ON log(source);
"""

_lock = threading.Lock()
MAX_LOG_JSON = 20_000


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / "nightingale-meta.sqlite", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    with _lock:
        conn.executescript(_SCHEMA)
        conn.commit()
    return conn


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def capped_json(value: Any) -> str:
    text = json.dumps(value, default=str, ensure_ascii=False)
    if len(text) <= MAX_LOG_JSON:
        return text
    return json.dumps({"truncated": True, "preview": text[: MAX_LOG_JSON - 200]}, ensure_ascii=False)


class _Rows:
    """A statement's result, fetched while the connection lock was held.

    A cursor fetched after the lock is released shares the connection with
    whatever another request thread is executing, which is exactly the race
    that raised ``sqlite3.InterfaceError: bad parameter or other API misuse``
    when the browser loaded a dataset's recipe, lineage and profile at once.
    """

    __slots__ = ("_rows", "lastrowid", "rowcount", "description")

    def __init__(self, cursor: sqlite3.Cursor):
        self._rows = cursor.fetchall()
        self.lastrowid = cursor.lastrowid
        self.rowcount = cursor.rowcount
        self.description = cursor.description

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _SerializedConnection:
    """One sqlite connection shared by the request threads, one statement at
    a time. Reads used to go straight to the shared connection while only
    writes took the lock."""

    def __init__(self, conn: sqlite3.Connection, lock: "threading.RLock"):
        self._conn = conn
        self._lock = lock

    def execute(self, sql: str, params: Any = ()) -> _Rows:
        with self._lock:
            return _Rows(self._conn.execute(sql, params))

    def executemany(self, sql: str, seq: Any) -> _Rows:
        with self._lock:
            return _Rows(self._conn.executemany(sql, seq))

    def executescript(self, script: str) -> None:
        with self._lock:
            self._conn.executescript(script)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


class Meta:
    """Thin, lock-protected wrapper around the metadata database."""

    def __init__(self, conn: sqlite3.Connection):
        # Re-entrant: the write methods hold it across several statements
        # and every statement takes it again.
        self._lock = threading.RLock()
        self.conn = _SerializedConnection(conn, self._lock)

    def close(self) -> None:
        self.conn.close()

    # ---- sources ----
    def add_source(self, name: str, kind: str, path: str, options: dict, incremental_key: str | None = None) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO sources (name, kind, path, options_json, incremental_key, created_at) VALUES (?,?,?,?,?,?)",
                (name, kind, path, json.dumps(options), incremental_key, now_iso()),
            )
            self.conn.commit()
            return cur.lastrowid

    def touch_source(self, source_id: int) -> None:
        with self._lock:
            self.conn.execute("UPDATE sources SET last_refreshed_at=? WHERE id=?", (now_iso(), source_id))
            self.conn.commit()

    def get_source(self, source_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()

    def list_sources(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM sources ORDER BY id").fetchall()

    # ---- datasets ----
    def add_dataset(self, name: str, source_id: int | None) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO datasets (name, source_id, current_version, created_at) VALUES (?,?,0,?)",
                (name, source_id, now_iso()),
            )
            self.conn.commit()
            return cur.lastrowid

    def get_dataset(self, name: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM datasets WHERE name=?", (name,)).fetchone()

    def get_dataset_by_id(self, dataset_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()

    def list_datasets(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM datasets ORDER BY name").fetchall()

    def set_current_version(self, dataset_id: int, version: int) -> None:
        with self._lock:
            self.conn.execute("UPDATE datasets SET current_version=? WHERE id=?", (version, dataset_id))
            self.conn.commit()

    def delete_dataset(self, dataset_id: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM datasets WHERE id=?", (dataset_id,))
            self.conn.commit()

    # ---- versions ----
    def add_version(self, dataset_id: int, version: int, parent_version: int | None, op: str,
                     params: dict, sql: str, table_name: str, row_count: int, columns: list[dict]) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO versions (dataset_id, version, parent_version, op, params_json, sql, table_name, "
                "row_count, columns_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (dataset_id, version, parent_version, op, json.dumps(params), sql, table_name, row_count,
                 json.dumps(columns), now_iso()),
            )
            self.conn.commit()
            return cur.lastrowid

    def get_version(self, dataset_id: int, version: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM versions WHERE dataset_id=? AND version=?", (dataset_id, version)
        ).fetchone()

    def list_versions(self, dataset_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM versions WHERE dataset_id=? ORDER BY version", (dataset_id,)
        ).fetchall()

    def delete_all_versions(self, dataset_id: int) -> list[sqlite3.Row]:
        """Remove (and return) every version of a dataset — used by refresh, which rebuilds
        the whole version history from a freshly re-ingested source."""
        with self._lock:
            rows = self.conn.execute("SELECT * FROM versions WHERE dataset_id=?", (dataset_id,)).fetchall()
            self.conn.execute("DELETE FROM versions WHERE dataset_id=?", (dataset_id,))
            self.conn.commit()
            return rows

    def delete_versions_after(self, dataset_id: int, version: int) -> list[sqlite3.Row]:
        """Remove (and return) every version strictly greater than `version` — used when a new
        step branches off after an undo, discarding the redo stack it replaces."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM versions WHERE dataset_id=? AND version>?", (dataset_id, version)
            ).fetchall()
            self.conn.execute("DELETE FROM versions WHERE dataset_id=? AND version>?", (dataset_id, version))
            self.conn.commit()
            return rows

    # ---- quality ----
    def add_rule(self, dataset_id: int, name: str, kind: str, params: dict) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO quality_rules (dataset_id, name, kind, params_json, created_at) VALUES (?,?,?,?,?)",
                (dataset_id, name, kind, json.dumps(params), now_iso()),
            )
            self.conn.commit()
            return cur.lastrowid

    def list_rules(self, dataset_id: int) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM quality_rules WHERE dataset_id=? ORDER BY id", (dataset_id,)).fetchall()

    def get_rule(self, rule_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM quality_rules WHERE id=?", (rule_id,)).fetchone()

    def delete_rule(self, rule_id: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM quality_rules WHERE id=?", (rule_id,))
            self.conn.commit()

    def add_result(self, rule_id: int, dataset_id: int, version: int, passed: bool, checked: int,
                    failed: int, sample: list, message: str) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO quality_results (rule_id, dataset_id, version, passed, checked, failed, "
                "sample_json, message, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (rule_id, dataset_id, version, int(passed), checked, failed, capped_json(sample), message, now_iso()),
            )
            self.conn.commit()
            return cur.lastrowid

    def list_results(self, dataset_id: int, rule_id: int | None = None, limit: int = 50) -> list[sqlite3.Row]:
        if rule_id:
            return self.conn.execute(
                "SELECT * FROM quality_results WHERE dataset_id=? AND rule_id=? ORDER BY id DESC LIMIT ?",
                (dataset_id, rule_id, limit),
            ).fetchall()
        return self.conn.execute(
            "SELECT * FROM quality_results WHERE dataset_id=? ORDER BY id DESC LIMIT ?", (dataset_id, limit)
        ).fetchall()

    # ---- charts ----
    def add_chart(self, name: str, dataset_id: int | None, spec: dict) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO charts (name, dataset_id, spec_json, created_at) VALUES (?,?,?,?)",
                (name, dataset_id, json.dumps(spec), now_iso()),
            )
            self.conn.commit()
            return cur.lastrowid

    def get_chart(self, chart_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM charts WHERE id=?", (chart_id,)).fetchone()

    def list_charts(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM charts ORDER BY id DESC").fetchall()

    def delete_chart(self, chart_id: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM charts WHERE id=?", (chart_id,))
            self.conn.commit()

    # ---- dashboards ----
    def add_dashboard(self, name: str, spec: dict) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO dashboards (name, spec_json, created_at, updated_at) VALUES (?,?,?,?)",
                (name, json.dumps(spec), now_iso(), now_iso()),
            )
            self.conn.commit()
            return cur.lastrowid

    def get_dashboard(self, dashboard_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM dashboards WHERE id=?", (dashboard_id,)).fetchone()

    def list_dashboards(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM dashboards ORDER BY id DESC").fetchall()

    def update_dashboard(self, dashboard_id: int, spec: dict) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE dashboards SET spec_json=?, updated_at=? WHERE id=?", (json.dumps(spec), now_iso(), dashboard_id)
            )
            self.conn.commit()

    # ---- models ----
    def add_model(self, name: str, dataset_id: int | None, dataset_version: int, kind: str, target: str | None,
                  features: list[str], params: dict, metrics: dict, seed: int, artifact_path: str | None) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO models (name, dataset_id, dataset_version, kind, target, features_json, params_json, "
                "metrics_json, seed, artifact_path, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (name, dataset_id, dataset_version, kind, target, json.dumps(features), json.dumps(params),
                 json.dumps(metrics), seed, artifact_path, now_iso()),
            )
            self.conn.commit()
            return cur.lastrowid

    def get_model(self, model_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM models WHERE id=?", (model_id,)).fetchone()

    def list_models(self, dataset_id: int | None = None) -> list[sqlite3.Row]:
        if dataset_id:
            return self.conn.execute("SELECT * FROM models WHERE dataset_id=? ORDER BY id DESC", (dataset_id,)).fetchall()
        return self.conn.execute("SELECT * FROM models ORDER BY id DESC").fetchall()

    def delete_model(self, model_id: int) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM models WHERE id=?", (model_id,))
            self.conn.commit()

    def set_model_artifact_path(self, model_id: int, path: str) -> None:
        with self._lock:
            self.conn.execute("UPDATE models SET artifact_path=? WHERE id=?", (path, model_id))
            self.conn.commit()

    # ---- analysis log ----
    def next_log_id(self) -> str:
        row = self.conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM log").fetchone()
        return f"N-{row['n']:06d}"

    def log(self, op: str, source: str, dataset: str | None, input_data: Any, output_summary: str,
             ok: bool, error: str | None, elapsed_ms: float) -> str:
        with self._lock:
            op_id = self.next_log_id()
            self.conn.execute(
                "INSERT INTO log (op_id, op, source, dataset, input_json, output_summary, ok, error, "
                "elapsed_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (op_id, op, source, dataset, capped_json(input_data), output_summary[:2000], int(ok), error,
                 elapsed_ms, now_iso()),
            )
            self.conn.commit()
            return op_id

    def get_log(self, op_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM log WHERE op_id=?", (op_id,)).fetchone()

    def search_log(self, q: str | None = None, source: str | None = None, dataset: str | None = None,
                    limit: int = 50) -> list[sqlite3.Row]:
        clauses, params = [], []
        if q:
            clauses.append("(op LIKE ? OR output_summary LIKE ? OR input_json LIKE ? OR op_id = ?)")
            like = f"%{q}%"
            params += [like, like, like, q]
        if source:
            clauses.append("source=?")
            params.append(source)
        if dataset:
            clauses.append("dataset=?")
            params.append(dataset)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        return self.conn.execute(f"SELECT * FROM log {where} ORDER BY seq DESC LIMIT ?", params).fetchall()


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    return dict(row) if row is not None else None


def rows_to_list(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]
