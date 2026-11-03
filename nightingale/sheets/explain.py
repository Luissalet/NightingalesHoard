"""Explain a compiled workbook: sensitivity (tornado), scenario comparison and unit checking.

Everything runs on the same compiled engine (``Compiled``) and the same AST the
calculation uses; nothing here re-implements a formula.
"""
from __future__ import annotations

import math
import re
from typing import Any

from . import runtime as rt
from .compiler import Model, addr, from_json, is_date_format, to_json
from .engine import Compiled
from ..texts import t as tx

MAX_TARGETS = 40
MAX_PARAMETERS = 200


# --- sensitivity ----------------------------------------------------------------

def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _out_number(v):
    return float(v) if _is_number(v) else None


def _labelled_targets(model: Model) -> list:
    labelled = [k for k in model.results if k in model.labels]
    return labelled or list(model.results)


def sensitivity(c: Compiled, output: str | None = None, delta: float = 0.1,
                steps: dict[str, float] | None = None) -> dict:
    """One-at-a-time perturbation of every numeric parameter; ranked per output cell (tornado)."""
    m = c.model
    if not (_is_number(delta) and delta > 0):
        raise ValueError(tx("wb.delta"))
    abs_steps: dict = {}
    for ref, st in (steps or {}).items():
        key = c.resolve(ref)
        if key not in m.cells or (m.cells[key].formula and not m.cells[key].frozen):
            raise ValueError(tx("wb.step_input", ref=ref))
        if not (_is_number(st) and st > 0):
            raise ValueError(tx("wb.step_positive", ref=ref))
        abs_steps[key] = float(st)
    if output:
        okey = c.resolve(output)
        if okey not in m.cells:
            raise LookupError(tx("wb.cell_empty", ref=output))
        targets = [okey]
    else:
        targets = _labelled_targets(m)
    truncated = max(0, len(targets) - MAX_TARGETS)
    targets = targets[:MAX_TARGETS]
    base_out = c.raw()

    skipped, cases = [], []
    params = list(m.parameters)
    for key in params[:MAX_PARAMETERS]:
        cell = m.cells[key]
        v = cell.value
        info = {"parameter": addr(key), "label": m.labels.get(key)}
        if key in m.options:
            skipped.append({**info, "reason": tx("wb.skip_options")})
        elif not _is_number(v):
            kind = tx("wb.kind_boolean" if isinstance(v, bool) else "wb.kind_empty" if v in (None, "")
                      else "wb.kind_text" if isinstance(v, str) else "wb.kind_other")
            skipped.append({**info, "reason": tx("wb.skip_nonnumeric", kind=kind)})
        elif is_date_format(cell.number_format) and key not in abs_steps:
            skipped.append({**info, "reason": tx("wb.skip_date")})
        else:
            base = float(v)
            if key in abs_steps:
                lo, hi = base - abs_steps[key], base + abs_steps[key]
            elif base == 0:
                skipped.append({**info, "reason": tx("wb.skip_zero")})
                continue
            else:
                lo, hi = sorted((base * (1 - delta), base * (1 + delta)))
            cases.append((key, info, base, lo, hi))
    if len(params) > MAX_PARAMETERS:
        skipped.append({"parameter": None, "label": None, "reason": tx("wb.skip_more", n=len(params) - MAX_PARAMETERS, max=MAX_PARAMETERS)})

    runs = {key: (c.raw({key: lo}), c.raw({key: hi})) for key, _info, _b, lo, hi in cases}
    out_targets = []
    for t in targets:
        d = m.describe(t)
        bv = base_out.get(t)
        tgt = {"output": d["cell"], "label": d["label"], "base": to_json(bv), "rows": [], "no_effect": []}
        if _out_number(bv) is None:
            tgt["status"] = "error" if isinstance(bv, rt.XlError) else "not numeric"
            tgt["note"] = tx("wb.no_rank")
            out_targets.append(tgt)
            continue
        rows, bad = [], []
        for key, info, base, lo, hi in cases:
            olo, ohi = (r.get(t) for r in runs[key])
            row = {**info, "base": to_json(base), "low": to_json(lo), "high": to_json(hi),
                   "output_low": to_json(olo), "output_high": to_json(ohi)}
            nlo, nhi = _out_number(olo), _out_number(ohi)
            if nlo is None or nhi is None:
                row["status"] = "error" if isinstance(olo, rt.XlError) or isinstance(ohi, rt.XlError) else "not numeric"
                bad.append(row)
                continue
            row["swing"] = abs(nhi - nlo)
            row["status"] = "ok"
            rows.append(row)
        total = sum(r["swing"] for r in rows)
        live = sorted((r for r in rows if r["swing"] > 0), key=lambda r: -r["swing"])
        for r in live:
            r["share"] = r["swing"] / total if total else 0.0
        tgt["no_effect"] = [r["label"] or r["parameter"] for r in rows if r["swing"] == 0]
        tgt["rows"] = live + bad
        tgt["total_swing"] = total
        tgt["status"] = "ok"
        if live:
            top = live[0]
            tgt["dominant"] = {"parameter": top["parameter"], "label": top["label"], "share": top["share"]}
        out_targets.append(tgt)
    return {"delta": delta, "steps": {addr(k): s for k, s in abs_steps.items()}, "targets": out_targets,
            "skipped": skipped, "targets_not_shown": truncated}


# --- scenarios ------------------------------------------------------------------

def compare_scenarios(c: Compiled, scenarios: dict[str, dict]) -> dict:
    """Every labelled result for the base values and for each scenario, with deltas against the base."""
    m = c.model
    names = list(scenarios)
    cols = {"Base": c.raw()}
    problems = {}
    for name in names:
        try:
            over = {}
            for ref, v in scenarios[name].items():
                key = c.resolve(ref)
                cell = m.cells.get(key)
                if cell is not None and cell.formula and not cell.frozen:
                    raise ValueError(tx("wb.is_formula", addr=addr(key)))
                over[key] = from_json(v)
            cols[name] = c.raw(over)
        except Exception as e:  # noqa: BLE001 - a broken scenario is reported, never hidden
            problems[name] = str(e)
    targets = _labelled_targets(m)
    rows = []
    for t in targets:
        d = m.describe(t)
        base = cols["Base"].get(t)
        vals = {}
        for name in cols:
            v = cols[name].get(t)
            e = {"value": to_json(v)}
            if name != "Base" and _is_number(v) and _is_number(base):
                e["delta"] = float(v) - float(base)
                e["delta_pct"] = (float(v) - float(base)) / abs(float(base)) if float(base) != 0 else None
            vals[name] = e
        rows.append({"cell": d["cell"], "label": d["label"], "values": vals})
    return {"columns": ["Base"] + [n for n in names if n in cols], "rows": rows, "failed": problems}


# --- units ----------------------------------------------------------------------

_UREG = None
SCALAR = "scalar"  # a literal number: takes the unit of whatever it is added to


def registry():
    global _UREG
    if _UREG is None:
        import pint
        r = pint.UnitRegistry()
        r.define("EUR = [currency_eur]")
        r.define("USD = [currency_usd]")
        _UREG = r
    return _UREG


def parse_unit(text: str):
    t = (text or "").strip().replace("€", "EUR").replace("$", "USD")
    if t in ("", "1", "dimensionless", "-", "%", "percent", "ratio"):
        return registry().Unit("dimensionless")
    try:
        return registry().Unit(t)
    except Exception as e:  # noqa: BLE001
        raise ValueError(tx("wb.unknown_unit", text=text, detail=str(e).splitlines()[0])) from None


def unit_text(u) -> str | None:
    if u is None:
        return None
    if u == SCALAR:
        return tx("wb.unit_number")
    s = f"{u:~}".strip()
    s = s.replace(" ** ", "^").replace(" / ", "/").replace(" * ", "*")
    return s or "1"


def format_unit(fmt: str | None):
    """Currency or percent implied by an Excel number format, or None."""
    if not fmt or fmt == "General":
        return None
    f = fmt
    symbols = [m.group(1) for m in re.finditer(r"\[\$([^\]\-]*)(?:-[^\]]*)?\]", f)]
    f = re.sub(r"\[[^\]]*\]", "", f)
    quoted = re.findall(r'"([^"]*)"', f)
    f_unq = re.sub(r'"[^"]*"|\\.', "", f)
    text = " ".join(symbols + quoted) + " " + f_unq
    if "€" in text or re.search(r"\bEUR\b", text):
        return parse_unit("EUR")
    if "$" in text or re.search(r"\bUSD\b", text):
        return parse_unit("USD")
    if "%" in f_unq:
        return parse_unit("dimensionless")
    return None


def _compatible(a, b) -> bool:
    return a.dimensionality == b.dimensionality


def _safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - offset units, overflow...: unknown, never an error
        return None


class _Infer:
    KEEP = {"SUM", "MIN", "MAX", "AVERAGE", "MEDIAN", "ROUND", "ROUNDUP", "ROUNDDOWN", "ABS", "AVERAGEA", "MAXA", "MINA"}
    FIRST_ARG_ONLY = {"ROUND", "ROUNDUP", "ROUNDDOWN", "ABS"}

    def __init__(self, c: Compiled, declared: dict):
        self.c = c
        self.m = c.model
        self.units: dict = {}
        self.source: dict = {}
        self.errors: list[dict] = []
        self._range_cache: dict = {}
        self.cur = None
        for key, text in declared.items():
            if key in self.m.cells:
                self.units[key] = parse_unit(text)
                self.source[key] = "declared"
        for key, cell in self.m.cells.items():
            if key in self.units:
                continue
            if not cell.formula or cell.frozen:
                u = format_unit(cell.number_format)
                if u is not None:
                    self.units[key] = u
                    self.source[key] = "format"

    def run(self):
        for key in self.m.order:
            cell = self.m.cells[key]
            if cell.frozen or cell.ast is None:
                continue
            self.cur = key
            u = self.node(cell.ast)
            if key in self.units:
                continue  # declared by the user
            if u is None or u == SCALAR:
                fu = format_unit(cell.number_format)
                if fu is not None:
                    self.units[key], self.source[key] = fu, "format"
                elif u == SCALAR:
                    self.units[key], self.source[key] = SCALAR, "inferred"
            else:
                self.units[key], self.source[key] = u, "inferred"

    def err(self, op: str, a, b, message: str | None = None):
        cell = self.m.cells[self.cur]
        la, lb = unit_text(a), unit_text(b)
        self.errors.append({"cell": addr(self.cur), "label": self.m.labels.get(self.cur), "formula": cell.formula,
                            "operation": op, "left_unit": la, "right_unit": lb,
                            "message": message or tx("wb.cannot_combine", a=la, b=lb, op=op)})

    # -- helpers
    def range_unit(self, n):
        key = n[1:]
        if key in self._range_cache:
            return self._range_cache[key]
        _, sheet, r1, c1, r2, c2 = n
        found = []
        for k, u in self.units.items():
            if k[0] == sheet and r1 <= k[1] <= r2 and c1 <= k[2] <= c2 and u != SCALAR:
                found.append(u)
        res = None
        if found and all(_compatible(found[0], u) for u in found[1:]):
            res = found[0]
        self._range_cache[key] = res
        return res

    def add(self, op, a, b):
        if a is None or b is None:
            return None
        if a == SCALAR:
            return b
        if b == SCALAR:
            return a
        if _compatible(a, b):
            return a
        self.err(op, a, b)
        return None

    def node(self, n):
        t = n[0]
        if t == "num":
            return SCALAR
        if t == "ref":
            return self.units.get((n[1], n[2], n[3]))
        if t == "range":
            return self.range_unit(n)
        if t in ("un", "pct"):
            return self.node(n[-1] if t == "un" else n[1])
        if t == "bin":
            return self.binary(n)
        if t == "call":
            return self.call(n)
        if t == "array":
            for row in n[1]:
                for x in row:
                    self.node(x)
        return None

    def binary(self, n):
        op = n[1]
        a, b = self.node(n[2]), self.node(n[3])
        if op in ("+", "-"):
            return self.add(op, a, b)
        if op in ("=", "<>", "<", "<=", ">", ">="):
            self.add(op, a, b)
            return None
        if op in ("*", "/"):
            if a is None or b is None:
                return None
            if a == SCALAR and b == SCALAR:
                return SCALAR
            if op == "*":
                return a if b == SCALAR else b if a == SCALAR else _safe(lambda: a * b)
            if b == SCALAR:
                return a
            return _safe(lambda: (1 / b) if a == SCALAR else a / b)
        if op == "^":
            if a is None:
                return None
            if a == SCALAR:
                return SCALAR if b == SCALAR else None
            exp = n[3]
            if exp[0] == "num":
                return _safe(lambda: a ** exp[1])
            return a if a.dimensionless else None
        return None

    def call(self, n):
        name, args = n[1], n[2]
        units = [self.node(a) if a[0] != "missing" else "missing" for a in args]
        if name in ("IF", "IFERROR", "IFNA"):
            branches = units[1:3] if name == "IF" else units[:2]
            branches = [u for u in branches if u != "missing"]
            if len(branches) == 2:
                a, b = branches
                if a is not None and b is not None and a != SCALAR and b != SCALAR and not _compatible(a, b):
                    self.err(name, a, b, tx("wb.branches", name=name, a=unit_text(a), b=unit_text(b)))
                    return None
                if a is None or b is None:
                    return None
                return b if a == SCALAR else a
            return branches[0] if branches else None
        if name in self.KEEP:
            use = units[:1] if name in self.FIRST_ARG_ONLY else units
            res = SCALAR
            for u in use:
                if u == "missing":
                    continue
                if u is None:
                    return None
                if res == SCALAR:
                    res = u
                elif u != SCALAR:
                    if not _compatible(res, u):
                        self.err(name, res, u, tx("wb.mixes", name=name, a=unit_text(res), b=unit_text(u)))
                        return None
            return res
        return None


def _declared(c: Compiled, units: dict[str, str]) -> dict:
    out = {}
    for ref, text in (units or {}).items():
        out[c.resolve(ref)] = text
    return out


def check_units(c: Compiled, units: dict[str, str] | None = None) -> dict:
    """Infer a unit for every formula cell from the declared / format-detected input units."""
    m = c.model
    inf = _Infer(c, _declared(c, units or {}))
    inf.run()
    inputs, cells = [], []
    for key in sorted(inf.units, key=lambda k: (list(m.sheets).index(k[0]) if k[0] in m.sheets else 0, k[1], k[2])):
        cell = m.cells[key]
        u = inf.units[key]
        row = {"cell": addr(key), "label": m.labels.get(key), "unit": unit_text(u), "source": inf.source[key]}
        if cell.formula and not cell.frozen:
            cells.append({**row, "formula": cell.formula})
        else:
            inputs.append(row)
    formulas = [k for k in m.order if not m.cells[k].frozen]
    unknown = [k for k in formulas if k not in inf.units]
    return {"inputs": inputs, "cells": cells, "errors": inf.errors, "formula_cells": len(formulas),
            "unit_unknown": len(unknown),
            "note": tx("wb.units_note")}
