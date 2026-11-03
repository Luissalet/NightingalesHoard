"""Run a compiled model: evaluate with overrides, compare with Excel's cached values, export."""
from __future__ import annotations

import inspect
import math
from pathlib import Path
from typing import Any

from . import runtime as rt
from .compiler import ENGINE, Model, addr, from_json, generate, is_date_format, parse_addr, to_json


def _close(a, b, rel=1e-9, abs_=1e-9) -> bool:
    if isinstance(a, rt.XlError) or isinstance(b, rt.XlError):
        return a == b
    if a in (None, "") and b in (None, ""):
        return True
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b or a == b and type(a) is type(b)
    if isinstance(a, float) and isinstance(b, float):
        if math.isclose(a, b, rel_tol=rel, abs_tol=abs_):
            return True
        # Excel stores 15 significant digits
        return f"{a:.15g}" == f"{b:.15g}"
    return a == b


def literal_value(ast, *, date_system="1900"):
    """Value of a reference-free formula, or None when it raises or yields an area."""
    from .compiler import _Gen
    ns: dict[str, Any] = {}
    exec("from nightingale.sheets.runtime import *", ns)
    try:
        with rt.date_context(date_system):
            v = eval(_Gen().expr(ast), ns)
    except Exception:
        return None
    v = rt.normalise(v)
    return None if isinstance(v, rt.XlError) else v


class Compiled:
    def __init__(self, model: Model):
        self.model = model
        self.code = generate(model)
        ns: dict[str, Any] = {}
        exec(compile("from nightingale.sheets.runtime import *\n", "<runtime>", "exec"), ns)
        ns["SHEETS"] = dict(model.sheets)
        exec(compile(self.code, f"<workbook {model.name}>", "exec"), ns)
        exec(compile(ENGINE, "<engine>", "exec"), ns)
        self._evaluate = ns["evaluate"]
        self.base = {k: c.value for k, c in model.cells.items() if not c.formula or c.frozen}

    def resolve(self, ref: str):
        """Address ('Hoja!B3'), label or defined name → cell key."""
        m = self.model
        for k, label in m.labels.items():
            if label == ref:
                return k
        low = ref.strip().lower()
        for k, label in m.labels.items():
            if label.lower() == low:
                return k
        if ref in m.names:
            return parse_addr(m.names[ref])
        default = next(iter(m.sheets)) if len(m.sheets) == 1 else None
        return parse_addr(ref, default)

    def raw(self, overrides: dict | None = None, as_of=None) -> dict:
        """Evaluate with {cell key: runtime value} overrides; returns {cell key: value} for every cell (errors included)."""
        values = dict(self.base)
        values.update(overrides or {})
        return self._evaluate(values, as_of)

    def evaluate(self, overrides: dict[str, Any] | None = None, *, as_of=None, include: str = "results") -> dict:
        values = dict(self.base)
        applied = {}
        for ref, v in (overrides or {}).items():
            key = self.resolve(ref)
            cell = self.model.cells.get(key)
            if cell is not None and cell.formula and not cell.frozen:
                raise ValueError(f"{addr(key)} es una fórmula: solo se pueden cambiar parámetros y tablas")
            values[key] = from_json(v)
            applied[addr(key)] = to_json(values[key])
        out = self._evaluate(values, as_of)
        keys = self.model.results if include == "results" else self.model.order
        frozen_up = self.frozen_upstream()
        fmt = {}
        for k in keys:
            c = self.model.cells.get(k)
            v = out.get(k)
            if c is not None and isinstance(v, float) and is_date_format(c.number_format):
                fmt[k] = rt.from_serial(v, self.model.date_system).date().isoformat()
        return {
            "inputs": applied,
            "results": [
                {**self.model.describe(k), "value": to_json(out.get(k)), **({"display": fmt[k]} if k in fmt else {}),
                 **({"uses_frozen": True} if k in frozen_up else {})}
                for k in keys
            ],
            "values": out,
        }

    def frozen_upstream(self) -> set:
        m = self.model
        frozen = {k for k, c in m.cells.items() if c.formula and c.frozen}
        hit = set()
        for k in m.order:
            if m.deps.get(k, set()) & (frozen | hit):
                hit.add(k)
        return hit

    def check_cached(self) -> dict:
        """Recompute with the workbook as saved and compare each formula with the value Excel cached."""
        out = self._evaluate(dict(self.base), None)
        rows, missing = [], 0
        compared = agree = 0
        for k in self.model.order:
            c = self.model.cells[k]
            if c.frozen or c.volatile:
                continue
            if c.value is None and out.get(k) not in (None, ""):
                missing += 1
                continue
            compared += 1
            got = out.get(k)
            if _close(got, c.value):
                agree += 1
            else:
                rows.append({"cell": addr(k), "formula": c.formula, "excel": to_json(c.value), "nightingale": to_json(got)})
        return {"compared": compared, "agree": agree, "differences": rows[:200], "difference_count": len(rows),
                "without_cached_value": missing, "skipped_frozen": sum(1 for c in self.model.cells.values() if c.frozen),
                "skipped_volatile": sum(1 for c in self.model.cells.values() if c.volatile)}


def export_python(model: Model) -> str:
    """Standalone module: runtime + generated cells + engine + constants. Standard library only."""
    src = inspect.getsource(rt).replace("from __future__ import annotations\n", "")
    consts = {k: to_json(c.value) for k, c in model.cells.items() if not c.formula or c.frozen}
    params = {addr(k): k for k in model.parameters}
    results = {addr(k): k for k in model.results}
    labels = {addr(k): v for k, v in model.labels.items()}
    head = (
        f'"""Cálculo del libro {model.name!r} compilado por Nightingale\'s Hoard.\n\n'
        f"Origen sha256 {model.sha256}. Solo biblioteca estándar.\n\n"
        "    import calculo\n    calculo.compute({'Hoja!B3': 120})  # -> {celda: valor de cada resultado}\n\n"
        "Desde consola: python calculo.py '{\"Hoja!B3\": 120}'\n"
        f'Celdas congeladas (valor fijo de Excel): {len([c for c in model.cells.values() if c.frozen])}.\n"""\n'
    )
    body = [head, "from __future__ import annotations", "# ---- runtime ----", src, "# ---- workbook ----",
            f"SHEETS = {dict(model.sheets)!r}",
            "_CONSTANTS = " + repr({k: v for k, v in consts.items()}),
            f"PARAMETERS = {params!r}", f"RESULTS = {results!r}", f"LABELS = {labels!r}",
            "ADDRESSES = " + repr({addr(k): k for k in consts}),
            generate(model), ENGINE, _API]
    return "\n".join(body)


_API = '''

def _value(v):
    if isinstance(v, dict) and "error" in v:
        return ERRORS.get(v["error"], XlError(v["error"]))
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return v


def _key(ref):
    if isinstance(ref, tuple):
        return ref
    for a, lab in LABELS.items():
        if lab == ref:
            ref = a
    if ref in ADDRESSES:
        return ADDRESSES[ref]
    raise KeyError("celda desconocida o no modificable: " + str(ref))


def _plain(v):
    if isinstance(v, XlError):
        return {"error": v.code}
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)
    return v


def compute(overrides=None, as_of=None):
    """overrides: {address or label: value}. Returns {result address: value} (errors as {"error": code})."""
    values = {k: _value(v) for k, v in _CONSTANTS.items()}
    for ref, v in (overrides or {}).items():
        values[_key(ref)] = _value(v)
    out = evaluate(values, as_of)
    return {a: _plain(out.get(k)) for a, k in RESULTS.items()}


if __name__ == "__main__":
    import json
    import sys
    print(json.dumps(compute(json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}), ensure_ascii=False, indent=2))
'''
