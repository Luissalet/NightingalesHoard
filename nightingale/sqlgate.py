"""Read-only SQL gate: only a single SELECT-family statement may pass.

Adapted from Laplace's Hoard's `engines/data.py::gate_sql` (same author,
MIT). Used everywhere a person or the agent supplies raw SQL text: the
`sql` transform step (over `__prev__`, still gated so it can only read),
`data_query`, and quality's `custom_sql` rule. Every *other* mutation in the
workbench goes through `steps.py`'s controlled builders, never raw SQL.
"""

from __future__ import annotations

import re

import duckdb

__all__ = ["SQLGateError", "gate_sql"]

_ALLOWED_SOLO_TYPES = {"SELECT", "EXPLAIN"}
_BANNED_LEADING_KEYWORDS = {
    "ATTACH", "COPY", "INSTALL", "LOAD", "SET", "PRAGMA", "CREATE", "INSERT",
    "UPDATE", "DELETE", "EXPORT", "CALL", "DROP", "ALTER", "VACUUM",
    "ANALYZE", "BEGIN", "COMMIT", "ROLLBACK", "GRANT", "REVOKE",
    "CHECKPOINT", "USE", "RESET", "DETACH", "IMPORT", "TRUNCATE",
}


class SQLGateError(ValueError):
    pass


def _leading_keyword(sql: str) -> str:
    stripped = re.sub(r"^(\s*(--[^\n]*\n|/\*.*?\*/))*", "", sql, flags=re.S)
    m = re.match(r"\s*\(*\s*([A-Za-z]+)", stripped)
    return m.group(1).upper() if m else ""


def _first_lines(text: str, max_chars: int = 400) -> str:
    text = text.strip()
    return text if len(text) <= max_chars else text[:max_chars] + "…"


def gate_sql(sql: str) -> None:
    """Raise `SQLGateError` unless `sql` is exactly one read-only statement.

    Allowed: SELECT / WITH / DESCRIBE / SUMMARIZE / EXPLAIN, and a single
    PIVOT/UNPIVOT. Rejected explicitly: ATTACH, COPY, INSTALL, LOAD, SET,
    PRAGMA, CREATE, INSERT, UPDATE, DELETE and friends, plus multi-statement
    input — `__prev__` (or a dataset name) is a placeholder substituted
    afterwards, so this only ever inspects the user's own text.
    """
    if not sql or not sql.strip():
        raise SQLGateError("empty query")
    leading = _leading_keyword(sql)
    probe = sql.replace("__prev__", "t")
    try:
        statements = duckdb.extract_statements(probe)
    except Exception as exc:  # noqa: BLE001
        raise SQLGateError(f"could not parse SQL: {_first_lines(str(exc))}") from exc
    if not statements:
        raise SQLGateError("no statement found")
    types = [s.type.name for s in statements]

    if leading in ("PIVOT", "UNPIVOT"):
        if types in (["CREATE", "SELECT"], ["SELECT"]):
            return
        raise SQLGateError("PIVOT/UNPIVOT must be a single statement")

    if len(statements) != 1:
        raise SQLGateError(
            f"only one statement is allowed, found {len(statements)}; remove the ';' and send one query per call"
        )
    if leading in _BANNED_LEADING_KEYWORDS:
        raise SQLGateError(
            f"statement type not allowed: {leading}. Only read-only SELECT / WITH / DESCRIBE / "
            "SUMMARIZE / EXPLAIN / PIVOT queries can run here"
        )
    if types[0] not in _ALLOWED_SOLO_TYPES:
        raise SQLGateError(
            f"statement type not allowed: {types[0]}. Only read-only SELECT / WITH / DESCRIBE / "
            "SUMMARIZE / EXPLAIN / PIVOT queries can run here"
        )
