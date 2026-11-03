"""Reconcile two tables that should describe the same things.

Nothing is guessed silently: column correspondences and key candidates are
*suggestions* with their evidence; a reconciliation runs only on keys the
caller names, with the normalisation the caller asks for. Every row ends in
exactly one bucket (paired, only on one side, ambiguous key, missing key)
and every compared cell in exactly one category, so lost rows, duplicates and
"zero versus missing" stay visible instead of disappearing in a join.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from typing import Any, Optional

SEP = "\x1f"
NUMERIC = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT",
           "FLOAT", "DOUBLE", "REAL", "DECIMAL")
CELL_CATEGORIES = ("equal", "equal_within_tolerance", "different", "zero_vs_missing", "empty_vs_missing",
                   "only_left_value", "only_right_value", "both_missing")


class ReconcileError(ValueError):
    pass


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _columns(conn, table: str) -> list[dict]:
    return [{"name": r[0], "type": str(r[1])} for r in conn.execute(f"DESCRIBE {q(table)}").fetchall()]


def _family(typ: str) -> str:
    t = typ.upper()
    if t.startswith(NUMERIC) or t.startswith("DECIMAL"):
        return "number"
    if t.startswith(("DATE", "TIMESTAMP", "TIME")):
        return "date"
    if t.startswith("BOOL"):
        return "bool"
    return "text"


def _norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def _key_expr(alias: str, cols: list[str], opts: dict) -> str:
    """Composite key text; NULL when any component is NULL (a missing key never matches)."""
    parts = []
    for c in cols:
        e = f"CAST({alias}.{q(c)} AS VARCHAR)"
        if opts.get("trim"):
            e = f"trim({e})"
        if opts.get("case_insensitive"):
            e = f"lower({e})"
        if opts.get("numeric"):
            e = f"CAST(TRY_CAST({e} AS DOUBLE) AS VARCHAR)"
        if opts.get("empty_as_missing"):
            e = f"NULLIF({e}, '')"
        parts.append(e)
    if len(parts) == 1:
        return parts[0]
    nulls = " OR ".join(f"({p}) IS NULL" for p in parts)
    return f"CASE WHEN {nulls} THEN NULL ELSE concat_ws('{SEP}', {', '.join(parts)}) END"


def profile(conn, table: str, sample: int = 3) -> list[dict]:
    out = []
    total = conn.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0]
    for col in _columns(conn, table):
        c = q(col["name"])
        fam = _family(col["type"])
        zero = f"SUM(CASE WHEN {c} = 0 THEN 1 ELSE 0 END)" if fam == "number" else \
            f"SUM(CASE WHEN trim(CAST({c} AS VARCHAR)) IN ('0', '0.0', '0,0') THEN 1 ELSE 0 END)"
        empty = f"SUM(CASE WHEN CAST({c} AS VARCHAR) = '' THEN 1 ELSE 0 END)" if fam == "text" else "0"
        nulls, distinct, zeros, empties = conn.execute(
            f"SELECT SUM(CASE WHEN {c} IS NULL THEN 1 ELSE 0 END), COUNT(DISTINCT {c}), {zero}, {empty} FROM {q(table)}").fetchone()
        samples = [r[0] for r in conn.execute(
            f"SELECT DISTINCT CAST({c} AS VARCHAR) FROM {q(table)} WHERE {c} IS NOT NULL LIMIT {int(sample)}").fetchall()]
        out.append({"name": col["name"], "type": col["type"], "family": fam, "rows": int(total), "missing": int(nulls or 0),
                    "empty_text": int(empties or 0), "zeros": int(zeros or 0), "distinct": int(distinct or 0),
                    "unique": bool(total) and int(distinct or 0) == int(total) - int(nulls or 0) and not nulls,
                    "sample": samples})
    return out


def _distinct_values(conn, table, col, limit, family="text"):
    # Numbers are compared by value (1200.0 and 1200.00 are the same amount).
    expr = f"CAST(CAST({q(col)} AS DOUBLE) AS VARCHAR)" if family == "number" else f"lower(trim(CAST({q(col)} AS VARCHAR)))"
    rows = conn.execute(
        f"SELECT DISTINCT {expr} FROM {q(table)} WHERE {q(col)} IS NOT NULL "
        f"AND trim(CAST({q(col)} AS VARCHAR)) <> '' LIMIT {int(limit)}").fetchall()
    return {r[0] for r in rows}


def suggest(conn, left: str, right: str, max_values: int = 5000, top: int = 3) -> dict:
    """Ranked column correspondences and key candidates, each with its evidence.
    These are proposals: nothing here is applied."""
    lp, rp = profile(conn, left), profile(conn, right)
    rvals = {c["name"]: _distinct_values(conn, right, c["name"], max_values, c["family"]) for c in rp}
    mapping = []
    for lc in lp:
        lv = _distinct_values(conn, left, lc["name"], max_values, lc["family"])
        cands = []
        for rc in rp:
            name_sim = difflib.SequenceMatcher(None, _norm_name(lc["name"]), _norm_name(rc["name"])).ratio()
            rv = rvals[rc["name"]] if lc["family"] == rc["family"] or "number" not in (lc["family"], rc["family"]) else set()
            inter = len(lv & rv)
            containment = inter / min(len(lv), len(rv)) if lv and rv else 0.0
            same_family = lc["family"] == rc["family"]
            score = 0.4 * name_sim + 0.5 * containment + (0.1 if same_family else 0.0)
            if score < 0.25:
                continue
            cands.append({"right": rc["name"], "score": round(score, 3), "name_similarity": round(name_sim, 3),
                          "shared_values": inter, "containment": round(containment, 3),
                          "types": [lc["type"], rc["type"]], "same_family": same_family,
                          "sampled": len(lv) >= max_values or len(rv) >= max_values})
        cands.sort(key=lambda c: -c["score"])
        mapping.append({"left": lc["name"], "candidates": cands[:top]})
    keys = []
    for m in mapping:
        lc = next(c for c in lp if c["name"] == m["left"])
        for cand in m["candidates"]:
            rc = next(c for c in rp if c["name"] == cand["right"])
            if cand["containment"] >= 0.5 and lc["family"] != "number":
                strong = lc["unique"] and rc["unique"]
                issues = [f"{side} side: {c['missing']} missing, {c['rows'] - c['missing'] - c['distinct']} repeated"
                          for side, c in (("left", lc), ("right", rc)) if not c["unique"]]
                keys.append({"left": lc["name"], "right": rc["name"], "containment": cand["containment"],
                             "shared_values": cand["shared_values"], "strength": "strong" if strong else "weak",
                             "issues": issues,
                             "note": "unique and complete on both sides; confirm before using it" if strong else
                             "shares values but has missing or repeated keys: those rows will stay unpaired and listed"})
    # Shared values first; repeated or missing keys cost a quarter of the score,
    # they do not disqualify (those rows stay listed as ambiguous or keyless).
    keys.sort(key=lambda k: -(k["containment"] - (0.25 if k["strength"] == "weak" else 0.0)))
    return {"left": {"table": left, "columns": lp}, "right": {"table": right, "columns": rp},
            "mapping": mapping, "key_candidates": keys,
            "note": "Suggestions only. A shared value is evidence, not proof that two columns mean the same thing; "
                    "no key is applied until you choose it."}


def _cat_sql(lv: str, rv: str, numeric: bool, tol: float) -> str:
    lt, rt = f"CAST({lv} AS VARCHAR)", f"CAST({rv} AS VARCHAR)"
    zero_l = f"TRY_CAST({lt} AS DOUBLE) = 0"
    zero_r = f"TRY_CAST({rt} AS DOUBLE) = 0"
    numeq = (f"WHEN TRY_CAST({lt} AS DOUBLE) = TRY_CAST({rt} AS DOUBLE) THEN 'equal' " if numeric else "")
    within = (f"WHEN TRY_CAST({lt} AS DOUBLE) IS NOT NULL AND TRY_CAST({rt} AS DOUBLE) IS NOT NULL "
              f"AND abs(TRY_CAST({lt} AS DOUBLE) - TRY_CAST({rt} AS DOUBLE)) <= {float(tol)} THEN 'equal_within_tolerance' ") \
        if (numeric or tol) else ""
    return (f"CASE WHEN {lv} IS NULL AND {rv} IS NULL THEN 'both_missing' "
            f"WHEN {lv} IS NULL AND {zero_r} THEN 'zero_vs_missing' "
            f"WHEN {rv} IS NULL AND {zero_l} THEN 'zero_vs_missing' "
            f"WHEN {lv} IS NULL AND {rt} = '' THEN 'empty_vs_missing' "
            f"WHEN {rv} IS NULL AND {lt} = '' THEN 'empty_vs_missing' "
            f"WHEN {lv} IS NULL THEN 'only_right_value' WHEN {rv} IS NULL THEN 'only_left_value' "
            f"WHEN {lt} = {rt} THEN 'equal' {numeq}{within}ELSE 'different' END")


def reconcile(conn, left: str, right: str, keys: list[dict], compare: Optional[list[dict]] = None,
              options: Optional[dict] = None, sample: int = 10) -> dict:
    """keys: [{"left": col, "right": col}, ...]; compare: same shape, defaults to
    same-named non-key columns; options: trim, case_insensitive, numeric,
    empty_as_missing (all off unless asked) and tolerance (numbers)."""
    if not keys:
        raise ReconcileError("choose at least one key pair; keys are never guessed")
    opts = dict(options or {})
    tol = float(opts.get("tolerance") or 0.0)
    if tol < 0:
        raise ReconcileError("tolerance must be >= 0")
    lcols = {c["name"]: c for c in _columns(conn, left)}
    rcols = {c["name"]: c for c in _columns(conn, right)}
    for k in keys:
        if k.get("left") not in lcols:
            raise ReconcileError(f"left table has no column {k.get('left')!r}")
        if k.get("right") not in rcols:
            raise ReconcileError(f"right table has no column {k.get('right')!r}")
    key_l = [k["left"] for k in keys]
    key_r = [k["right"] for k in keys]
    if compare is None:
        compare = [{"left": n, "right": n} for n in lcols if n in rcols and n not in key_l and n not in key_r]
    for c in compare:
        if c.get("left") not in lcols or c.get("right") not in rcols:
            raise ReconcileError(f"cannot compare {c.get('left')!r} with {c.get('right')!r}: column missing")
    sample = max(0, min(int(sample), 200))
    lk, rk = _key_expr("l", key_l, opts), _key_expr("r", key_r, opts)
    base = (f"WITH L AS (SELECT l.*, row_number() OVER () AS __row, {lk} AS __k FROM {q(left)} l), "
            f"R AS (SELECT r.*, row_number() OVER () AS __row, {rk} AS __k FROM {q(right)} r), "
            f"LC AS (SELECT __k, COUNT(*) AS n FROM L WHERE __k IS NOT NULL GROUP BY __k), "
            f"RC AS (SELECT __k, COUNT(*) AS n FROM R WHERE __k IS NOT NULL GROUP BY __k), "
            f"LS AS (SELECT L.*, CASE WHEN L.__k IS NULL THEN 'missing_key' WHEN RC.n IS NULL THEN 'only_left' "
            f"WHEN LC.n > 1 OR RC.n > 1 THEN 'ambiguous' ELSE 'paired' END AS __status "
            f"FROM L LEFT JOIN LC USING (__k) LEFT JOIN RC USING (__k)), "
            f"RS AS (SELECT R.*, CASE WHEN R.__k IS NULL THEN 'missing_key' WHEN LC.n IS NULL THEN 'only_right' "
            f"WHEN LC.n > 1 OR RC.n > 1 THEN 'ambiguous' ELSE 'paired' END AS __status "
            f"FROM R LEFT JOIN RC USING (__k) LEFT JOIN LC USING (__k)) ")

    def counts(side):
        rows = conn.execute(base + f"SELECT __status, COUNT(*) FROM {side} GROUP BY __status").fetchall()
        return {s: int(n) for s, n in rows}

    lcount, rcount = counts("LS"), counts("RS")
    total_l = sum(lcount.values())
    total_r = sum(rcount.values())
    dups = {}
    for side, cnt in (("left", "LC"), ("right", "RC")):
        g, rws = conn.execute(base + f"SELECT COUNT(*), COALESCE(SUM(n), 0) FROM {cnt} WHERE n > 1").fetchone()
        dups[side] = {"duplicate_keys": int(g), "rows_in_duplicate_keys": int(rws), "extra_copies": int(rws) - int(g)}
    paired = lcount.get("paired", 0)
    cells = []
    for c in compare:
        lfam, rfam = _family(lcols[c["left"]]["type"]), _family(rcols[c["right"]]["type"])
        numeric = lfam == "number" or rfam == "number"
        cat = _cat_sql(f"LS.{q(c['left'])}", f"RS.{q(c['right'])}", numeric, tol)
        rows = conn.execute(base + f"SELECT {cat} AS cat, COUNT(*) FROM LS JOIN RS USING (__k) "
                                   f"WHERE LS.__status = 'paired' AND RS.__status = 'paired' GROUP BY cat").fetchall()
        dist = {k: 0 for k in CELL_CATEGORIES}
        dist.update({k: int(n) for k, n in rows})
        ex = []
        if sample:
            ex_rows = conn.execute(
                base + f"SELECT {', '.join('LS.' + q(k) for k in key_l)}, CAST(LS.{q(c['left'])} AS VARCHAR), "
                       f"CAST(RS.{q(c['right'])} AS VARCHAR), {cat} AS cat FROM LS JOIN RS USING (__k) "
                       f"WHERE LS.__status = 'paired' AND RS.__status = 'paired' AND {cat} NOT IN ('equal', 'both_missing', 'equal_within_tolerance') "
                       f"ORDER BY LS.__row LIMIT {sample}").fetchall()
            for r in ex_rows:
                ex.append({"key": {k: _js(v) for k, v in zip(key_l, r[:len(key_l)])}, "left": r[len(key_l)],
                           "right": r[len(key_l) + 1], "category": r[len(key_l) + 2]})
        cells.append({"left": c["left"], "right": c["right"], "categories": dist,
                      "mismatches": paired - dist["equal"] - dist["both_missing"] - dist["equal_within_tolerance"],
                      "examples": ex})

    def rows_of(side, status):
        if not sample:
            return []
        cols = list(lcols if side == "LS" else rcols)
        sel = ", ".join(f"{side}.{q(c)}" for c in cols)
        res = conn.execute(base + f"SELECT __row, {sel} FROM {side} WHERE __status = '{status}' ORDER BY __row LIMIT {sample}").fetchall()
        return [{"row": int(r[0]), **{c: _js(v) for c, v in zip(cols, r[1:])}} for r in res]

    explanation = [
        f"Key: left {key_l} = right {key_r}"
        + (f", normalised with {', '.join(k for k in ('trim', 'case_insensitive', 'numeric', 'empty_as_missing') if opts.get(k))}"
           if any(opts.get(k) for k in ('trim', 'case_insensitive', 'numeric', 'empty_as_missing')) else ", compared exactly as text")
        + ". A missing key component never matches.",
        "A key present more than once on either side is ambiguous: its rows are not paired with each other, so no "
        "duplicate is multiplied by the comparison.",
        "Rows with a key on one side only are kept and listed; nothing is dropped.",
        "Cell categories keep zero and missing apart (zero_vs_missing), and empty text apart from missing.",
    ]
    if tol:
        explanation.append(f"Numbers within ±{tol} count as equal_within_tolerance, separately from exact equality.")
    balance_l = total_l == sum(lcount.get(s, 0) for s in ("paired", "only_left", "ambiguous", "missing_key"))
    balance_r = total_r == sum(rcount.get(s, 0) for s in ("paired", "only_right", "ambiguous", "missing_key"))
    return {
        "left": {"table": left, "rows": total_l, **{s: lcount.get(s, 0) for s in ("paired", "only_left", "ambiguous", "missing_key")},
                 **dups["left"]},
        "right": {"table": right, "rows": total_r, **{s: rcount.get(s, 0) for s in ("paired", "only_right", "ambiguous", "missing_key")},
                  **dups["right"]},
        "pairs": paired, "keys": keys, "options": opts, "columns": cells,
        "samples": {"only_left": rows_of("LS", "only_left"), "only_right": rows_of("RS", "only_right"),
                    "ambiguous_left": rows_of("LS", "ambiguous"), "ambiguous_right": rows_of("RS", "ambiguous"),
                    "missing_key_left": rows_of("LS", "missing_key"), "missing_key_right": rows_of("RS", "missing_key")},
        "accounted": bool(balance_l and balance_r),
        "explanation": explanation,
        "_sql_base": base,
    }


def rows_table_sql(result: dict, lcols: list[str], rcols: list[str]) -> str:
    """Every row of both sides with its bucket, for export or saving."""
    base = result["_sql_base"]
    lsel = ", ".join(f"CAST(LS.{q(c)} AS VARCHAR) AS {q('left_' + c)}" for c in lcols)
    rsel = ", ".join(f"CAST(RS.{q(c)} AS VARCHAR) AS {q('right_' + c)}" for c in rcols)
    lnull = ", ".join(f"NULL AS {q('left_' + c)}" for c in lcols)
    rnull = ", ".join(f"NULL AS {q('right_' + c)}" for c in rcols)
    return (base + f"SELECT 'paired' AS status, LS.__row AS left_row, RS.__row AS right_row, {lsel}, {rsel} "
                   f"FROM LS JOIN RS ON LS.__k = RS.__k AND LS.__status = 'paired' AND RS.__status = 'paired' "
                   f"UNION ALL SELECT LS.__status, LS.__row, NULL, {lsel}, {rnull} FROM LS WHERE LS.__status <> 'paired' "
                   f"UNION ALL SELECT RS.__status, NULL, RS.__row, {lnull}, {rsel} FROM RS WHERE RS.__status <> 'paired' "
                   f"ORDER BY status, left_row NULLS LAST, right_row NULLS LAST")


def _js(v: Any) -> Any:
    from decimal import Decimal
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    return str(v)


# ---------------------------------------------------------------- service layer
def _tables(services, left: str, right: str):
    return services._resolve_table_by_name(left), services._resolve_table_by_name(right)


def suggest_datasets(services, left: str, right: str, source: str = "ui") -> dict:
    def do():
        lt, rt = _tables(services, left, right)
        with services.engine.lock() as conn:
            out = suggest(conn, lt, rt)
        out["left"]["dataset"], out["right"]["dataset"] = left, right
        out["_log_summary"] = f"suggested correspondences for {left!r} and {right!r}"
        return out

    return services._log("reconcile_suggest", source, left, {"right": right}, do)


def reconcile_datasets(services, left: str, right: str, keys: list[dict], compare: Optional[list[dict]] = None,
                       options: Optional[dict] = None, sample: int = 10, save_as: Optional[str] = None,
                       source: str = "ui") -> dict:
    params = {"right": right, "keys": keys, "compare": compare, "options": options, "save_as": save_as}

    def do():
        lt, rt = _tables(services, left, right)
        with services.engine.lock() as conn:
            res = reconcile(conn, lt, rt, keys, compare, options, sample)
            lcols = [c["name"] for c in _columns(conn, lt)]
            rcols = [c["name"] for c in _columns(conn, rt)]
            csv_text = _csv(conn, rows_table_sql(res, lcols, rcols)) if save_as else None
        res.pop("_sql_base", None)
        res["left"]["dataset"], res["right"]["dataset"] = left, right
        if save_as:
            saved = services.ingest_text(csv_text, save_as, "csv", None, source=source)
            res["saved_dataset"] = saved.get("name", save_as)
        res["_log_summary"] = (f"reconciled {left!r} with {right!r}: {res['pairs']} pairs, "
                               f"{res['left']['only_left']} only left, {res['right']['only_right']} only right, "
                               f"{res['left']['ambiguous'] + res['right']['ambiguous']} ambiguous rows")
        return res

    return services._log("reconcile", source, left, params, do)


def export_csv(services, left: str, right: str, keys: list[dict], options: Optional[dict] = None) -> str:
    lt, rt = _tables(services, left, right)
    with services.engine.lock() as conn:
        res = reconcile(conn, lt, rt, keys, [], options, 0)
        lcols = [c["name"] for c in _columns(conn, lt)]
        rcols = [c["name"] for c in _columns(conn, rt)]
        return _csv(conn, rows_table_sql(res, lcols, rcols))


def _csv(conn, sql: str) -> str:
    import csv
    import io
    cur = conn.execute(sql)
    names = [d[0] for d in cur.description]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(names)
    for row in cur.fetchall():
        w.writerow(["" if v is None else v for v in row])
    return buf.getvalue()
