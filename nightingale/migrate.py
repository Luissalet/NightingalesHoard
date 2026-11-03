"""Migration rehearsal: load datasets into a target schema in a scratch SQLite file before the real migration.

Nothing is invented. The spec names every target table, column, expression,
key and check; a suggestion helper may propose a first draft from a dataset
profile and says so. A rehearsal never touches the source datasets: it reads
them, writes one fresh SQLite file per migration name, and reports for every
table where each source row went (loaded, or rejected with its reason) so that
source = loaded + rejected holds exactly.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
import sqlite3
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

TYPES = ("INTEGER", "REAL", "TEXT", "DATE", "BOOLEAN")
KINDS = ("representation", "correction")
REASONS = ("cast_failure", "missing_required", "duplicate_primary_key", "duplicate_unique_key", "foreign_key_not_found")
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")


class MigrateError(ValueError):
    pass


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


# ------------------------------------------------------------------ spec
def check_name(name: Any) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise MigrateError("a migration name uses letters, digits, '_' or '-' (1 to 60 characters)")
    return name


def normalize_spec(spec: dict) -> dict:
    """Validate the structure and fill only literal defaults (same-name expr, kind=representation)."""
    if not isinstance(spec, dict):
        raise MigrateError("the spec must be a JSON object")
    name = check_name(spec.get("name"))
    tables_in = spec.get("tables")
    if not isinstance(tables_in, list) or not tables_in:
        raise MigrateError("the spec needs at least one table")
    tables, seen = [], set()
    for t in tables_in:
        tn = t.get("name")
        if not isinstance(tn, str) or not tn.strip():
            raise MigrateError("every table needs a name")
        if tn in seen:
            raise MigrateError(f"table {tn!r} is listed twice")
        seen.add(tn)
        if bool(t.get("source")) == bool(t.get("source_sql")):
            raise MigrateError(f"table {tn!r}: give exactly one of 'source' (dataset name) or 'source_sql'")
        cols_in = t.get("columns")
        if not isinstance(cols_in, list) or not cols_in:
            raise MigrateError(f"table {tn!r} needs columns")
        cols, cn = [], set()
        for c in cols_in:
            n = c.get("name")
            if not isinstance(n, str) or not n.strip():
                raise MigrateError(f"table {tn!r}: every column needs a name")
            if n in cn:
                raise MigrateError(f"table {tn!r}: column {n!r} is listed twice")
            cn.add(n)
            typ = str(c.get("type", "")).upper()
            if typ not in TYPES:
                raise MigrateError(f"table {tn!r}, column {n!r}: type must be one of {', '.join(TYPES)}")
            kind = c.get("kind") or "representation"
            if kind not in KINDS:
                raise MigrateError(f"table {tn!r}, column {n!r}: kind must be 'representation' or 'correction'")
            cols.append({"name": n, "type": typ, "required": bool(c.get("required")), "expr": c.get("expr") or None,
                         "kind": kind, "source_column": c.get("source_column") or None})
        pk = list(t.get("primary_key") or [])
        uniques = [list(u) for u in (t.get("unique") or [])]
        for group in [pk] + uniques:
            for col in group:
                if col not in cn:
                    raise MigrateError(f"table {tn!r}: key column {col!r} is not a column of the table")
        for c in cols:
            if c["name"] in pk:
                c["required"] = True
        fks = []
        for fk in t.get("foreign_keys") or []:
            fcols, rcols = list(fk.get("columns") or []), list(fk.get("ref_columns") or [])
            if not fcols or len(fcols) != len(rcols) or not fk.get("ref_table"):
                raise MigrateError(f"table {tn!r}: a foreign key needs columns, ref_table and the same number of ref_columns")
            for col in fcols:
                if col not in cn:
                    raise MigrateError(f"table {tn!r}: foreign key column {col!r} is not a column of the table")
            fks.append({"columns": fcols, "ref_table": fk["ref_table"], "ref_columns": rcols})
        tables.append({"name": tn, "source": t.get("source") or None, "source_sql": t.get("source_sql") or None,
                       "columns": cols, "primary_key": pk, "unique": uniques, "foreign_keys": fks})
    by_name = {t["name"]: t for t in tables}
    for t in tables:
        for fk in t["foreign_keys"]:
            ref = by_name.get(fk["ref_table"])
            if ref is None:
                raise MigrateError(f"table {t['name']!r}: foreign key refers to unknown table {fk['ref_table']!r}")
            rn = {c["name"] for c in ref["columns"]}
            missing = [c for c in fk["ref_columns"] if c not in rn]
            if missing:
                raise MigrateError(f"table {t['name']!r}: {fk['ref_table']!r} has no column {missing[0]!r}")
            keys = [ref["primary_key"]] + ref["unique"]
            if not any(sorted(k) == sorted(fk["ref_columns"]) and k for k in keys):
                raise MigrateError(f"table {t['name']!r}: ref_columns {fk['ref_columns']} must be the primary key "
                                   f"or a unique key of {fk['ref_table']!r}")
    checks = spec.get("checks") or {}
    totals = []
    for c in checks.get("totals") or []:
        if c.get("table") not in by_name:
            raise MigrateError("every total check needs 'table' naming a table of the spec")
        if not c.get("source_expr") or not c.get("target_column"):
            raise MigrateError("a total check needs source_expr and target_column")
        if c["target_column"] not in {x["name"] for x in by_name[c["table"]]["columns"]}:
            raise MigrateError(f"total check: {c['table']!r} has no column {c['target_column']!r}")
        tol = float(c.get("tolerance") or 0)
        if tol < 0:
            raise MigrateError("tolerance must be >= 0")
        totals.append({"name": c.get("name") or f"{c['table']}.{c['target_column']}", "table": c["table"],
                       "source_expr": c["source_expr"], "target_column": c["target_column"],
                       "target_expr": c.get("target_expr") or None, "tolerance": tol})
    queries = []
    for c in checks.get("queries") or []:
        if not c.get("name") or not c.get("source_sql") or not c.get("target_sql"):
            raise MigrateError("a functional check needs name, source_sql and target_sql")
        queries.append({"name": c["name"], "source_sql": c["source_sql"], "target_sql": c["target_sql"]})
    return {"name": name, "tables": tables, "checks": {"totals": totals, "queries": queries}}


def load_order(spec: dict) -> list[dict]:
    """Parents before children; self references do not count. A cycle between tables is an error."""
    tables = {t["name"]: t for t in spec["tables"]}
    deps = {n: {fk["ref_table"] for fk in t["foreign_keys"] if fk["ref_table"] != n} for n, t in tables.items()}
    out, done = [], set()
    while len(out) < len(tables):
        ready = [n for n in tables if n not in done and deps[n] <= done]
        if not ready:
            left = [n for n in tables if n not in done]
            raise MigrateError(f"foreign keys form a cycle among {left}; break it before rehearsing")
        out.append(tables[ready[0]])
        done.add(ready[0])
    return out


def ddl_statements(spec: dict) -> list[str]:
    out = []
    for t in load_order(spec):
        parts = [f"  {q(c['name'])} {c['type']}{' NOT NULL' if c['required'] else ''}" for c in t["columns"]]
        if t["primary_key"]:
            parts.append(f"  PRIMARY KEY ({', '.join(q(c) for c in t['primary_key'])})")
        for u in t["unique"]:
            parts.append(f"  UNIQUE ({', '.join(q(c) for c in u)})")
        for fk in t["foreign_keys"]:
            parts.append(f"  FOREIGN KEY ({', '.join(q(c) for c in fk['columns'])}) REFERENCES {q(fk['ref_table'])} "
                         f"({', '.join(q(c) for c in fk['ref_columns'])})")
        out.append(f"CREATE TABLE {q(t['name'])} (\n" + ",\n".join(parts) + "\n);")
    return out


def ddl_for(spec: dict) -> str:
    head = f"-- Target schema for migration {spec['name']!r}, in load order (parents first).\n\n"
    return head + "\n\n".join(ddl_statements(spec)) + "\n"


# ------------------------------------------------------------------ casting
def cast_sql(e: str, typ: str) -> str:
    """Strict cast: text that is not a clean value of the type becomes NULL (and is reported), never a guess."""
    if typ == "TEXT":
        return f"CAST(({e}) AS VARCHAR)"
    if typ == "REAL":
        return f"TRY_CAST(({e}) AS DOUBLE)"
    if typ == "DATE":
        return f"TRY_CAST(({e}) AS DATE)"
    if typ == "BOOLEAN":
        return f"TRY_CAST(({e}) AS BOOLEAN)"
    txt = f"trim(CAST(({e}) AS VARCHAR))"
    d = f"TRY_CAST(({e}) AS DOUBLE)"
    return (f"CASE WHEN {txt} ~ '^[+-]?[0-9]+$' THEN TRY_CAST({txt} AS BIGINT) "
            f"WHEN {d} IS NOT NULL AND {d} = floor({d}) THEN TRY_CAST({d} AS BIGINT) END")


def _js(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return str(v)


def _lite(v: Any) -> Any:
    """Python value to the form SQLite stores."""
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def _norm(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float, Decimal)):
        f = round(float(v), 9)
        return int(f) if f == int(f) and abs(f) < 1e15 else f
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return str(v)


def _source_columns(conn, stage: str) -> list[str]:
    return [r[0] for r in conn.execute(f"DESCRIBE {q(stage)}").fetchall() if r[0] != "__row"]


def _column_plan(conn, t: dict, stage: str) -> list[dict]:
    src = set(_source_columns(conn, stage))
    plan = []
    for c in t["columns"]:
        expr = c["expr"]
        if expr is None:
            if c["name"] not in src:
                raise MigrateError(f"table {t['name']!r}: the source has no column {c['name']!r}; give an expr "
                                   f"(source columns: {sorted(src)})")
            expr = q(c["name"])
        ref = c["source_column"] or (c["name"] if c["name"] in src else None)
        if ref is not None and ref not in src:
            raise MigrateError(f"table {t['name']!r}, column {c['name']!r}: source_column {ref!r} is not in the source")
        plan.append({**c, "expr": expr, "ref": ref, "val": cast_sql(expr, c["type"]), "raw": f"CAST(({expr}) AS VARCHAR)"})
    return plan


# ------------------------------------------------------------------ loading
def _fetch_chunks(cur, n=5000):
    while True:
        rows = cur.fetchmany(n)
        if not rows:
            return
        yield from rows


def _process_table(conn, lite: sqlite3.Connection, t: dict, plan: list[dict], stage: str, mode: str,
                   sample: int) -> tuple[dict, list[dict]]:
    names = [c["name"] for c in plan]
    pos = {n: i for i, n in enumerate(names)}
    sel = ", ".join(f"{c['raw']}, {c['val']}" for c in plan)
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM {q(stage)}").fetchone()[0]
        cur = conn.execute(f"SELECT __row, {sel} FROM {q(stage)} ORDER BY __row")
        staged = list(_fetch_chunks(cur))
    except Exception as exc:  # noqa: BLE001
        raise MigrateError(f"table {t['name']!r}: an expression failed on the source ({exc}). "
                           f"Use TRY_CAST or CASE in expr for values that cannot convert.") from exc
    required = [c["name"] for c in plan if c["required"]]
    rejected: list[dict] = []

    def reject(row, reason, column=None, value=None, detail=""):
        rejected.append({"table": t["name"], "row": int(row), "reason": reason, "column": column,
                         "value": _js(value), "detail": detail})

    cands = []  # rows that passed casting and required checks: (row, values-in-sqlite-form)
    for rec in staged:
        row = rec[0]
        raws = rec[1::2]
        vals = rec[2::2]
        bad = [(plan[i]["name"], raws[i]) for i in range(len(plan)) if raws[i] is not None and vals[i] is None]
        if bad:
            detail = "; ".join(f"{n}: {r!r} is not a valid {plan[pos[n]]['type']}" for n, r in bad)
            reject(row, "cast_failure", bad[0][0], bad[0][1], detail)
            continue
        miss = [n for n in required if vals[pos[n]] is None]
        if miss:
            reject(row, "missing_required", miss[0], None, f"required column(s) empty: {', '.join(miss)}")
            continue
        cands.append((row, [_lite(v) for v in vals]))

    # Parent key sets for foreign keys
    parents: list[Optional[set]] = []
    for fk in t["foreign_keys"]:
        if fk["ref_table"] == t["name"]:
            idx = [pos[c] for c in fk["ref_columns"]]
            parents.append({tuple(v[i] for i in idx) for _, v in cands})
        else:
            cols = ", ".join(q(c) for c in fk["ref_columns"])
            parents.append({tuple(r) for r in lite.execute(f"SELECT {cols} FROM {q(fk['ref_table'])}").fetchall()})
    pk_idx = [pos[c] for c in t["primary_key"]]
    uq_idx = [[pos[c] for c in u] for u in t["unique"]]
    fk_idx = [[pos[c] for c in fk["columns"]] for fk in t["foreign_keys"]]
    seen_pk: set = set()
    seen_uq: list[set] = [set() for _ in uq_idx]
    accepted = []
    for row, v in cands:
        if pk_idx:
            k = tuple(v[i] for i in pk_idx)
            if k in seen_pk:
                reject(row, "duplicate_primary_key", ", ".join(t["primary_key"]), k[0] if len(k) == 1 else list(k),
                       f"primary key {k} already used by an earlier row")
                continue
        dup = None
        for j, idx in enumerate(uq_idx):
            k = tuple(v[i] for i in idx)
            if None not in k and k in seen_uq[j]:
                dup = (j, k)
                break
        if dup:
            cols = t["unique"][dup[0]]
            reject(row, "duplicate_unique_key", ", ".join(cols), dup[1][0] if len(dup[1]) == 1 else list(dup[1]),
                   f"unique key {dup[1]} already used by an earlier row")
            continue
        nofound = None
        for fk, idx, ps in zip(t["foreign_keys"], fk_idx, parents):
            k = tuple(v[i] for i in idx)
            if None in k:
                continue  # a NULL foreign key references nothing
            if k not in ps:
                nofound = (fk, k)
                break
        if nofound:
            fk, k = nofound
            reject(row, "foreign_key_not_found", ", ".join(fk["columns"]), k[0] if len(k) == 1 else list(k),
                   f"{k} not found in {fk['ref_table']}({', '.join(fk['ref_columns'])})")
            continue
        if pk_idx:
            seen_pk.add(tuple(v[i] for i in pk_idx))
        for j, idx in enumerate(uq_idx):
            k = tuple(v[i] for i in idx)
            if None not in k:
                seen_uq[j].add(k)
        accepted.append(v)

    cols_sql = ", ".join(q(n) for n in names)
    ph = ", ".join("?" for _ in names)
    sql = f"INSERT INTO {q(t['name'])} ({cols_sql}) VALUES ({ph})"
    if mode == "upsert":
        sets = [n for n in names if n not in t["primary_key"]]
        conflict = ", ".join(q(c) for c in t["primary_key"])
        sql += (f" ON CONFLICT ({conflict}) DO UPDATE SET " + ", ".join(f"{q(n)} = excluded.{q(n)}" for n in sets)
                if sets else f" ON CONFLICT ({conflict}) DO NOTHING")
    try:
        lite.executemany(sql, accepted)
    except sqlite3.IntegrityError as exc:
        raise MigrateError(f"table {t['name']!r}: the target refused a row ({exc})") from exc
    by_reason = Counter(r["reason"] for r in rejected)
    samples = {}
    for r in rejected:
        lst = samples.setdefault(r["reason"], [])
        if len(lst) < sample:
            lst.append({k: r[k] for k in ("row", "column", "value", "detail")})
    return ({"table": t["name"], "source_rows": int(total), "loaded": len(accepted), "rejected": len(rejected),
             "by_reason": {k: by_reason.get(k, 0) for k in REASONS}, "samples": samples,
             "reconciled": int(total) == len(accepted) + len(rejected)}, rejected)


def _table_hash(lite: sqlite3.Connection, t: dict) -> tuple[int, str]:
    order = ", ".join(q(c) for c in (t["primary_key"] or [c["name"] for c in t["columns"]]))
    rows = lite.execute(f"SELECT * FROM {q(t['name'])} ORDER BY {order}").fetchall()
    return len(rows), hashlib.sha256(repr(rows).encode()).hexdigest()


# ------------------------------------------------------------------ column semantics
def _column_semantics(conn, t: dict, plan: list[dict], stage: str, sample: int) -> list[dict]:
    out = []
    for c in plan:
        val, raw = c["val"], c["raw"]
        ok = f"(({val}) IS NOT NULL OR ({raw}) IS NULL)"
        item = {"column": c["name"], "type": c["type"], "kind": c["kind"], "expr": c["expr"], "reference": c["ref"]}
        if c["ref"] is None:
            item.update({"considered": None, "changed": None,
                         "note": "no source column to compare against (name a source_column to enable the check)"})
            out.append(item)
            continue
        ref = q(c["ref"])
        typed_ref = cast_sql(ref, c["type"])
        changed = f"({typed_ref}) IS DISTINCT FROM ({val})"
        considered, n_changed = conn.execute(
            f"SELECT COUNT(*), COUNT(*) FILTER (WHERE {changed}) FROM {q(stage)} WHERE {ok}").fetchone()
        examples = conn.execute(
            f"SELECT __row, CAST({ref} AS VARCHAR), CAST(({val}) AS VARCHAR) FROM {q(stage)} WHERE {ok} AND {changed} "
            f"ORDER BY __row LIMIT {int(sample)}").fetchall()
        ex = [{"row": int(r[0]), "source": r[1], "target": r[2]} for r in examples]
        item.update({"considered": int(considered), "changed": int(n_changed), "changed_examples": ex})
        if c["kind"] == "correction":
            item["note"] = ("declared correction; nothing was changed" if not n_changed
                            else f"{n_changed} value(s) differ from the source column {c['ref']!r} by design")
        else:
            fmt_where = (f"{ok} AND NOT ({changed}) AND {ref} IS NOT NULL AND ({val}) IS NOT NULL "
                         f"AND CAST({ref} AS VARCHAR) IS DISTINCT FROM CAST(({val}) AS VARCHAR)")
            n_fmt = conn.execute(f"SELECT COUNT(*) FROM {q(stage)} WHERE {fmt_where}").fetchone()[0]
            fmt_ex = conn.execute(
                f"SELECT __row, CAST({ref} AS VARCHAR), CAST(({val}) AS VARCHAR) FROM {q(stage)} WHERE {fmt_where} "
                f"ORDER BY __row LIMIT {int(sample)}").fetchall()
            item.update({
                "flagged": bool(n_changed),
                "roundtrip_exact": int(considered) - int(n_changed) - int(n_fmt),
                "roundtrip_format_only": int(n_fmt),
                "format_only_examples": [{"row": int(r[0]), "source": r[1], "target": r[2]} for r in fmt_ex],
                "note": (f"representation column changes {n_changed} value(s): declare it a correction or fix the expression"
                         if n_changed else
                         "values survive the round trip" + (f"; {n_fmt} are written differently (same value)" if n_fmt else ""))})
        out.append(item)
    return out


# ------------------------------------------------------------------ checks
def _scalar(v):
    return None if v is None else (float(v) if isinstance(v, (Decimal, int, float)) and not isinstance(v, bool) else v)


def _eval_totals(conn, ro: sqlite3.Connection, spec: dict, stages: dict) -> list[dict]:
    out = []
    for c in spec["checks"]["totals"]:
        item = {"name": c["name"], "table": c["table"], "source_expr": c["source_expr"],
                "target_column": c["target_column"], "tolerance": c["tolerance"]}
        try:
            src = _scalar(conn.execute(f"SELECT {c['source_expr']} FROM {q(stages[c['table']])}").fetchone()[0])
            texpr = c["target_expr"] or f"SUM({q(c['target_column'])})"
            tgt = _scalar(ro.execute(f"SELECT {texpr} FROM {q(c['table'])}").fetchone()[0])
        except Exception as exc:  # noqa: BLE001
            item.update({"passed": False, "error": str(exc)})
            out.append(item)
            continue
        if src is None or tgt is None:
            passed, diff = src is None and tgt is None, None
        else:
            diff = tgt - src
            passed = abs(diff) <= c["tolerance"] + 1e-9
        item.update({"source": src, "target": tgt, "difference": diff, "passed": bool(passed)})
        out.append(item)
    return out


def _eval_queries(conn, ro: sqlite3.Connection, spec: dict, sample: int) -> list[dict]:
    from .sqlgate import gate_sql
    out = []
    for c in spec["checks"]["queries"]:
        item = {"name": c["name"]}
        try:
            gate_sql(c["source_sql"])
            if not re.match(r"^\s*(select|with)\b", c["target_sql"], re.I):
                raise MigrateError("target_sql must be a single SELECT")
            s_rows = [tuple(_norm(v) for v in r) for r in conn.execute(c["source_sql"]).fetchall()]
            t_rows = [tuple(_norm(v) for v in r) for r in ro.execute(c["target_sql"]).fetchall()]
        except Exception as exc:  # noqa: BLE001
            item.update({"passed": False, "error": str(exc)})
            out.append(item)
            continue
        sc, tc = Counter(s_rows), Counter(t_rows)
        only_s, only_t = sc - tc, tc - sc
        item.update({"passed": not only_s and not only_t, "source_rows": len(s_rows), "target_rows": len(t_rows),
                     "only_in_source": [list(k) for k in list(only_s)[:sample]],
                     "only_in_target": [list(k) for k in list(only_t)[:sample]]})
        out.append(item)
    return out


# ------------------------------------------------------------------ rehearsal
def rehearse(conn, spec: dict, sources: dict[str, str], path: Path, sample: int = 10) -> dict:
    """spec: normalised; sources: table name -> SQL relation over the (read-only) datasets;
    path: the SQLite file, rebuilt from scratch."""
    order = load_order(spec)
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    stages, plans = {}, {}
    try:
        for i, t in enumerate(order):
            stage = f"__mig_{i}"
            conn.execute(f"CREATE OR REPLACE TEMP TABLE {q(stage)} AS SELECT row_number() OVER () AS __row, * FROM ({sources[t['name']]})")
            stages[t["name"]] = stage
            plans[t["name"]] = _column_plan(conn, t, stage)
        lite = sqlite3.connect(str(path))
        for stmt in ddl_statements(spec):
            lite.execute(stmt)
        tables, rejected_all = [], []
        for t in order:
            rep, rej = _process_table(conn, lite, t, plans[t["name"]], stages[t["name"]], "insert", sample)
            rep["target_rows"] = lite.execute(f"SELECT COUNT(*) FROM {q(t['name'])}").fetchone()[0]
            rep["columns"] = _column_semantics(conn, t, plans[t["name"]], stages[t["name"]], sample)
            tables.append(rep)
            rejected_all += rej
        lite.commit()
        fk_violations = [list(r) for r in lite.execute("PRAGMA foreign_key_check").fetchall()]
        hashes = {t["name"]: _table_hash(lite, t) for t in order}
        lite.close()

        ro = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        totals = _eval_totals(conn, ro, spec, stages)
        queries = _eval_queries(conn, ro, spec, sample)
        ro.close()

        lite = sqlite3.connect(str(path))
        idem = []
        for t in order:
            if not t["primary_key"]:
                idem.append({"table": t["name"], "provable": False,
                             "note": "no primary key: a second load cannot be matched row by row, so idempotency is not proved"})
                continue
            before = hashes[t["name"]]
            first = next(x for x in tables if x["table"] == t["name"])
            rep, _ = _process_table(conn, lite, t, plans[t["name"]], stages[t["name"]], "upsert", sample)
            after = _table_hash(lite, t)
            pk = ", ".join(q(c) for c in t["primary_key"])
            dups = lite.execute(f"SELECT COUNT(*) FROM (SELECT 1 FROM {q(t['name'])} GROUP BY {pk} HAVING COUNT(*) > 1)").fetchone()[0]
            idem.append({"table": t["name"], "provable": True, "rows_before": before[0], "rows_after": after[0],
                         "duplicate_keys": int(dups), "unchanged": before == after,
                         "same_rejections": rep["rejected"] == first["rejected"],
                         "passed": before == after and dups == 0 and rep["rejected"] == first["rejected"]})
        lite.commit()
        lite.close()
    finally:
        for stage in stages.values():
            conn.execute(f"DROP TABLE IF EXISTS {q(stage)}")
    provable = [x for x in idem if x["provable"]]
    summary = {
        "source_rows": sum(x["source_rows"] for x in tables),
        "loaded": sum(x["loaded"] for x in tables),
        "rejected": sum(x["rejected"] for x in tables),
        "reconciled": all(x["reconciled"] and x["loaded"] == x["target_rows"] for x in tables),
        "foreign_key_violations": len(fk_violations),
        "idempotent": bool(provable) and all(x["passed"] for x in provable),
        "idempotency_provable_tables": len(provable),
        "flagged_columns": sum(1 for x in tables for c in x["columns"] if c.get("flagged")),
        "totals_failed": sum(1 for x in totals if not x["passed"]),
        "queries_failed": sum(1 for x in queries if not x["passed"]),
    }
    return {"name": spec["name"], "file": str(path), "load_order": [t["name"] for t in order], "summary": summary,
            "tables": tables, "idempotency": idem, "totals": totals, "queries": queries,
            "foreign_key_violations": fk_violations, "explanation": explain(spec, summary, tables),
            "_rejected": rejected_all}


def explain(spec: dict, summary: dict, tables: list[dict]) -> list[str]:
    lines = [
        "The source datasets were only read. The target is a new SQLite file rebuilt from scratch on every run.",
        "Tables are loaded parents first, so a foreign key can only point at a row that was actually loaded.",
        f"Every source row is either loaded or rejected with its reason: {summary['source_rows']} source = "
        f"{summary['loaded']} loaded + {summary['rejected']} rejected" + (" (counts reconcile)." if summary["reconciled"] else " (COUNTS DO NOT RECONCILE)."),
        "Reasons, in the order they are tested: a value that does not convert to the declared type, a required value "
        "missing, a repeated primary key, a repeated unique key, a foreign key without a parent. The earliest row keeps the key.",
        "Text that is not a clean value of the target type is rejected, never rounded or guessed; use an expression "
        "(TRY_CAST, NULLIF, CASE) in the spec when you decide how it should convert.",
        "A representation column may change only how a value is written; if its values differ from the source column it is flagged. "
        "A correction column is expected to change values and reports how many.",
        "The second load upserts by primary key into the same file; the report compares row counts and a content hash before and after.",
    ]
    if summary["rejected"]:
        lines.append("Rejected rows are listed with column and value; nothing rejected reaches the target.")
    return lines


# ------------------------------------------------------------------ suggestion
def suggest_table(conn, relation: str, table_name: str, source_name: str) -> dict:
    """A first draft from the data. It is a proposal: review every column, key and kind."""
    conn.execute(f"CREATE OR REPLACE TEMP TABLE __mig_suggest AS SELECT * FROM ({relation})")
    try:
        total = conn.execute("SELECT COUNT(*) FROM __mig_suggest").fetchone()[0]
        cols = []
        unique_complete = []
        for name, typ, *_ in conn.execute("DESCRIBE __mig_suggest").fetchall():
            c = q(name)
            nulls, distinct = conn.execute(f"SELECT COUNT(*) FILTER (WHERE {c} IS NULL), COUNT(DISTINCT {c}) FROM __mig_suggest").fetchone()
            t = str(typ).upper()
            kind, evidence = "representation", "native type"
            if t.startswith(("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT")):
                target = "INTEGER"
            elif t.startswith(("DOUBLE", "FLOAT", "DECIMAL", "REAL")):
                target = "REAL"
            elif t.startswith("DATE") or t.startswith("TIMESTAMP"):
                target = "DATE"
            elif t.startswith("BOOL"):
                target = "BOOLEAN"
            else:
                target = "TEXT"
                filled = total - nulls
                if filled:
                    for cand in ("INTEGER", "REAL", "DATE", "BOOLEAN"):
                        good = conn.execute(f"SELECT COUNT(*) FROM __mig_suggest WHERE {c} IS NOT NULL AND ({cast_sql(c, cand)}) IS NOT NULL").fetchone()[0]
                        if good == filled:
                            target, evidence = cand, f"all {filled} non-empty text values convert to {cand}"
                            break
            cols.append({"name": name, "type": target, "required": nulls == 0 and total > 0, "kind": kind,
                         "evidence": f"{evidence}; {nulls} missing, {distinct} distinct of {total}"})
            if nulls == 0 and total and distinct == total:
                unique_complete.append(name)
        pk = []
        if unique_complete:
            pk = [next((n for n in unique_complete if n.lower() in ("id", "code", "codigo", "key")), unique_complete[0])]
    finally:
        conn.execute("DROP TABLE IF EXISTS __mig_suggest")
    return {"name": table_name, "source": source_name,
            "columns": [{k: v for k, v in c.items() if k != "evidence"} for c in cols],
            "primary_key": pk, "unique": [], "foreign_keys": [],
            "_evidence": {c["name"]: c["evidence"] for c in cols}}


# ---------------------------------------------------------------- service layer
def _dir(services, *parts) -> Path:
    p = services.config.data_dir / "migrations"
    for x in parts:
        p = p / x
    return p


def _spec_path(services, name: str) -> Path:
    return _dir(services, "specs", check_name(name) + ".json")


def rehearsal_path(services, name: str) -> Path:
    return _dir(services, check_name(name) + ".sqlite")


def _load_spec(services, spec: Optional[dict], name: Optional[str]) -> dict:
    if spec is not None:
        return normalize_spec(spec)
    if not name:
        raise MigrateError("give a saved spec name or an inline spec")
    return normalize_spec(get_spec(services, name)["spec"])


def save_spec(services, name: str, spec: dict, source: str = "ui") -> dict:
    def do():
        full = normalize_spec({**spec, "name": name})
        p = _spec_path(services, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({**spec, "name": name}, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"name": name, "tables": [t["name"] for t in full["tables"]], "saved": True,
                "_log_summary": f"saved migration spec {name!r}"}

    return services._log("migrate_save", source, None, {"name": name}, do)


def list_specs(services) -> dict:
    d = _dir(services, "specs")
    out = []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            spec = json.loads(p.read_text(encoding="utf-8"))
            out.append({"name": p.stem, "tables": [t.get("name") for t in spec.get("tables", [])],
                        "rehearsed": rehearsal_path(services, p.stem).exists()})
        except (OSError, ValueError):
            out.append({"name": p.stem, "tables": [], "rehearsed": False, "unreadable": True})
    return {"specs": out}


def get_spec(services, name: str) -> dict:
    p = _spec_path(services, name)
    if not p.exists():
        raise MigrateError(f"no saved migration spec named {name!r}")
    return {"name": name, "spec": json.loads(p.read_text(encoding="utf-8")), "rehearsal_file": rehearsal_path(services, name).exists()}


def delete_spec(services, name: str, source: str = "ui") -> dict:
    def do():
        p = _spec_path(services, name)
        if not p.exists():
            raise MigrateError(f"no saved migration spec named {name!r}")
        p.unlink()
        return {"name": name, "deleted": True, "rehearsal_file_kept": rehearsal_path(services, name).exists(),
                "_log_summary": f"deleted migration spec {name!r}"}

    return services._log("migrate_delete", source, None, {"name": name}, do)


def ddl(services, spec: Optional[dict] = None, name: Optional[str] = None) -> dict:
    full = _load_spec(services, spec, name)
    return {"name": full["name"], "load_order": [t["name"] for t in load_order(full)], "ddl": ddl_for(full)}


def suggest_spec(services, dataset: str, table: Optional[str] = None, name: Optional[str] = None, source: str = "ui") -> dict:
    def do():
        rel = services._resolve_table_by_name(dataset)
        with services.engine.lock() as conn:
            t = suggest_table(conn, f"SELECT * FROM {q(rel)}", table or dataset, dataset)
        evidence = t.pop("_evidence")
        spec = {"name": name or (table or dataset), "tables": [t]}
        normalize_spec(spec)
        return {"suggestion": True,
                "note": "This is a suggestion drawn from the data, not a mapping. Check every type, required flag, key and "
                        "kind before saving; nothing has been applied or saved.",
                "spec": spec, "evidence": evidence, "_log_summary": f"suggested a target table for {dataset!r}"}

    return services._log("migrate_suggest", source, dataset, {"table": table}, do)


def rehearse_spec(services, spec: Optional[dict] = None, name: Optional[str] = None, save_rejected: Optional[str] = None,
                  sample: int = 10, source: str = "ui") -> dict:
    def do():
        from .sqlgate import gate_sql
        full = _load_spec(services, spec, name)
        sources = {}
        for t in full["tables"]:
            if t["source"]:
                sources[t["name"]] = f"SELECT * FROM {q(services._resolve_table_by_name(t['source']))}"
            else:
                gate_sql(t["source_sql"])
                sources[t["name"]] = t["source_sql"].strip().rstrip(";")
        with services.engine.lock() as conn:
            res = rehearse(conn, full, sources, rehearsal_path(services, full["name"]), sample)
            rejected = res.pop("_rejected")
        res["rejected_total"] = len(rejected)
        if save_rejected:
            import csv
            import io
            buf = io.StringIO()
            w = csv.writer(buf, lineterminator="\n")
            w.writerow(["migration_table", "source_row", "reason", "column", "value", "detail"])
            for r in rejected:
                w.writerow([r["table"], r["row"], r["reason"], r["column"] or "",
                            "" if r["value"] is None else r["value"], r["detail"]])
            saved = services.ingest_text(buf.getvalue(), save_rejected, "csv", None, source=source)
            res["saved_rejected_dataset"] = saved.get("name", save_rejected)
        s = res["summary"]
        res["_log_summary"] = (f"rehearsed migration {full['name']!r}: {s['loaded']} loaded, {s['rejected']} rejected, "
                               f"idempotent={s['idempotent']}, {s['totals_failed']} total and {s['queries_failed']} query checks failed")
        return res

    return services._log("migrate_rehearse", source, None, {"name": name or (spec or {}).get("name"), "save_rejected": save_rejected}, do)
