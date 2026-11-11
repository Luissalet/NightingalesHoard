"""Fabrication quotes by scenario: explicit parameters with units and sources, line items as formulas.

One quote = named parameters (value, unit, source: user / assumed / measured / rate card) plus *derived* quantities
(times, printed volume...) and *line items* in separate categories (geometric material, process material, setup,
labour, machine, waste, margin). Each one is a formula over names, evaluated with Pint units by a restricted `ast`
walker (no `eval`). The names a formula uses form a dependency graph, so changing one assumption recomputes only
the items that depend on it; the rest is copied from the base result.

Nothing is invented: a measurement that is missing stays missing and blocks every item that needs it. Machine
time is never derived from solid volume alone for concrete printing; for FDM a volume-only time is allowed only as a
labelled coarse estimate (or blocked by option). Geometry can come from Vulcan's library API (optional, with
provenance); costs are not written anywhere else: actual costs are kept here as facts for calibration, with an
optional reference to a movement of the books.
"""
from __future__ import annotations

import ast
import copy
import csv
import hashlib
import io
import json
import math
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .sheets.explain import parse_unit, registry
from .texts import t as tx

CATEGORIES = ("geometric_material", "process_material", "setup", "labour", "machine", "waste", "margin")
SOURCES = ("user", "assumed", "measured", "rate card", "calibrated")
PROCESSES = ("fdm", "cnc_laser", "concrete")
POLICIES = ("coarse", "block")
VULCAN_DEFAULT = "http://127.0.0.1:5186"
QUALITY_RANK = {"exact": 0, "coarse": 1}
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,59}$")
_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,59}$")
_FUNCS = {"min", "max", "abs", "ceil", "floor", "round", "sqrt"}


class FabQuoteError(ValueError):
    pass


# --------------------------------------------------------------------------------------------- safe expressions
def formula_names(formula: str) -> list[str]:
    """Names (parameters, derived quantities, items) a formula refers to, in order of appearance."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise FabQuoteError(tx("fq.formula_syntax", formula=formula, detail=exc.msg)) from None
    skip = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    out: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and id(n) not in skip and n.id not in out:
            out.append(n.id)
    return out


def _q(value: float, unit=None):
    r = registry()
    return r.Quantity(value, unit) if unit is not None else r.Quantity(value, "dimensionless")


def _dimless(q, what: str):
    try:
        return q.to("dimensionless").magnitude
    except Exception:  # noqa: BLE001
        raise FabQuoteError(tx("fq.need_dimensionless", what=what)) from None


def _call(name: str, args: list):
    if name in ("min", "max"):
        if not args:
            raise FabQuoteError(tx("fq.formula_forbidden", what=name))
        best = args[0]
        for a in args[1:]:
            best = (min if name == "min" else max)(best, a)
        return best
    if len(args) != 1:
        raise FabQuoteError(tx("fq.formula_forbidden", what=name))
    a = args[0]
    if name == "abs":
        return abs(a)
    if name == "sqrt":
        return a ** 0.5
    fn = {"ceil": math.ceil, "floor": math.floor, "round": round}[name]
    if a.dimensionless:
        return _q(float(fn(a.to("dimensionless").magnitude)))
    return _q(float(fn(a.magnitude)), a.units)


def _eval(node: ast.AST, env: dict):
    if isinstance(node, ast.Expression):
        return _eval(node.body, env)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return _q(float(node.value))
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise FabQuoteError(tx("fq.unknown_name", name=node.id))
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, env), _eval(node.right, env)
        op = node.op
        if isinstance(op, ast.Add):
            return a + b
        if isinstance(op, ast.Sub):
            return a - b
        if isinstance(op, ast.Mult):
            return a * b
        if isinstance(op, ast.Div):
            if b.magnitude == 0:
                raise FabQuoteError(tx("fq.div_zero"))
            return a / b
        if isinstance(op, ast.Pow):
            return a ** _dimless(b, "**")
        raise FabQuoteError(tx("fq.formula_forbidden", what=type(op).__name__))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        return _call(node.func.id, [_eval(a, env) for a in node.args])
    if isinstance(node, ast.IfExp):
        return _eval(node.body if _truth(node.test, env) else node.orelse, env)
    raise FabQuoteError(tx("fq.formula_forbidden", what=type(node).__name__))


def _truth(node: ast.AST, env: dict) -> bool:
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        a, b = _eval(node.left, env), _eval(node.comparators[0], env)
        op = node.ops[0]
        for cls, fn in ((ast.Lt, lambda x, y: x < y), (ast.LtE, lambda x, y: x <= y), (ast.Gt, lambda x, y: x > y),
                        (ast.GtE, lambda x, y: x >= y), (ast.Eq, lambda x, y: x == y), (ast.NotEq, lambda x, y: x != y)):
            if isinstance(op, cls):
                return bool(fn(a, b))
    raise FabQuoteError(tx("fq.formula_forbidden", what="condition"))


def evaluate_formula(formula: str, env: dict):
    """A Pint quantity from `formula` over `env` (name -> quantity). Raises FabQuoteError on anything unsafe."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise FabQuoteError(tx("fq.formula_syntax", formula=formula, detail=exc.msg)) from None
    try:
        return _eval(tree, env)
    except FabQuoteError:
        raise
    except Exception as exc:  # noqa: BLE001 - Pint dimensionality errors and friends
        raise FabQuoteError(tx("fq.eval_error", formula=formula, detail=str(exc).splitlines()[0])) from None


# --------------------------------------------------------------------------------------------- templates
def _p(value, unit, source="assumed", note=""):
    return {"value": value, "unit": unit, "source": source, "note": note}


def _alt(formula: str, quality: str = "exact", warning: str = "") -> dict:
    return {"formula": formula, "quality": quality, "warning": warning}


def _item(id_, category, label, formula, unit="{cur}", alts=None):
    return {"id": id_, "category": category, "label": label, "alternatives": alts or [_alt(formula)], "unit": unit}


def _der(id_, label, unit, alts):
    return {"id": id_, "label": label, "unit": unit, "alternatives": alts}


def build_template(process: str, currency: str = "EUR") -> dict:
    """The starting spec of a process. Measurements start missing; rates and process settings start as labelled
    assumptions to be replaced. Nothing here is a measured fact."""
    if process not in PROCESSES:
        raise FabQuoteError(tx("fq.unknown_process", process=process, valid=list(PROCESSES)))
    cur = currency
    note_rate = "example value: replace with your own rate"
    params: dict[str, dict] = {}
    derived: list[dict] = []
    items: list[dict] = []
    if process == "fdm":
        params = {
            "volume_cm3": _p(None, "cm**3", "measured"), "surface_cm2": _p(None, "cm**2", "measured"),
            "part_height_mm": _p(None, "mm", "measured"),
            "layer_height_mm": _p(0.2, "mm"), "line_width_mm": _p(0.45, "mm"), "perimeters": _p(2, "1"),
            "infill_pct": _p(20, "%"), "print_speed_mm_s": _p(60, "mm/s"), "layer_change_s": _p(2, "s"),
            "travel_overhead_pct": _p(15, "%"), "slicer_time_h": _p(None, "h", "user", "print time reported by a slicer"),
            "coarse_throughput_cm3_h": _p(12, "cm**3/h", "assumed", "only used for a coarse volume-only estimate"),
            "coarse_fill_fraction_pct": _p(35, "%", "assumed", "only used when the surface is not known"),
            "top_layers": _p(4, "1"), "bottom_layers": _p(4, "1"),
            "top_bottom_area_cm2": _p(None, "cm**2", "measured", "projected area of the faces that look up or down; with it the solid top and bottom layers are a line item of their own"),
            "slicer_extruded_cm3": _p(None, "cm**3", "measured", "filament volume of the part (without support, skirt or purge) from a slicer G-code"),
            "slicer_skin_cm3": _p(None, "cm**3", "measured", "volume of the solid top and bottom layers from a slicer G-code"),
            "slicer_support_cm3": _p(None, "cm**3", "measured", "volume of support material from a slicer G-code"),
            "density_g_cm3": _p(1.24, "g/cm**3"), "material_price_eur_kg": _p(22, f"{cur}/kg", "rate card", note_rate),
            "support_pct": _p(10, "%"), "purge_g": _p(5, "g"),
            "setup_time_h": _p(0.5, "h"), "postprocess_time_h": _p(0.25, "h"),
            "labour_rate_eur_h": _p(28, f"{cur}/h", "rate card", note_rate), "machine_rate_eur_h": _p(1.2, f"{cur}/h", "rate card", note_rate),
            "failure_rate_pct": _p(5, "%"), "margin_pct": _p(20, "%"),
        }
        derived = [
            _der("shell_cm3", "Perimeter shell volume", "cm**3",
                 [_alt("min(volume_cm3, surface_cm2 * perimeters * line_width_mm)")]),
            _der("skin_cm3", "Solid top and bottom layers (0 until the top/bottom area or a slicer value is given: "
                 "then the infill percentage covers the whole interior)", "cm**3", [
                _alt("slicer_skin_cm3"),
                _alt("min(top_bottom_area_cm2 * (top_layers + bottom_layers) * layer_height_mm, volume_cm3 - shell_cm3)"),
                _alt("0 * volume_cm3", "exact", "fq.warn_skin_not_separated")]),
            _der("extruded_cm3", "Printed volume (shell + skin + infill)", "cm**3", [
                _alt("slicer_extruded_cm3"),
                _alt("shell_cm3 + skin_cm3 + infill_pct * (volume_cm3 - shell_cm3 - skin_cm3)"),
                _alt("volume_cm3 * coarse_fill_fraction_pct", "coarse", "fq.warn_coarse_fill")]),
            _der("print_time_h", "Print time", "h", [
                _alt("slicer_time_h"),
                _alt("extruded_cm3 / (line_width_mm * layer_height_mm * print_speed_mm_s) * (1 + travel_overhead_pct)"
                     " + ceil(part_height_mm / layer_height_mm) * layer_change_s"),
                _alt("volume_cm3 / coarse_throughput_cm3_h", "coarse", "fq.warn_coarse_time")]),
        ]
        items = [
            _item("mat_geo", "geometric_material", "Material in walls and infill",
                  "(extruded_cm3 - skin_cm3) * density_g_cm3 * material_price_eur_kg", cur),
            _item("mat_skin", "geometric_material", "Material in the solid top and bottom layers",
                  "skin_cm3 * density_g_cm3 * material_price_eur_kg", cur),
            _item("mat_supports", "process_material", "Support material", None, cur,
                  alts=[_alt("slicer_support_cm3 * density_g_cm3 * material_price_eur_kg"),
                        _alt("(mat_geo + mat_skin) * support_pct")]),
            _item("mat_purge", "process_material", "Purge and priming", "purge_g * material_price_eur_kg", cur),
            _item("setup_prep", "setup", "Preparation and slicing", "setup_time_h * labour_rate_eur_h", cur),
            _item("labour_post", "labour", "Post-processing labour", "postprocess_time_h * labour_rate_eur_h", cur),
            _item("machine_time", "machine", "Machine time", "print_time_h * machine_rate_eur_h", cur),
            _item("waste_scrap", "waste", "Failed prints (expected scrap)",
                  "failure_rate_pct * (mat_geo + mat_skin + mat_supports + mat_purge + machine_time)", cur),
            _item("margin", "margin", "Margin on cost",
                  "margin_pct * (mat_geo + mat_skin + mat_supports + mat_purge + setup_prep + labour_post + machine_time + waste_scrap)", cur),
        ]
    elif process == "cnc_laser":
        params = {
            "cut_length_mm": _p(None, "mm", "measured"), "blank_area_cm2": _p(None, "cm**2", "measured"),
            "passes": _p(1, "1"), "feed_mm_min": _p(1500, "mm/min"), "pierce_count": _p(1, "1"), "pierce_s": _p(0.5, "s"),
            "material_price_eur_m2": _p(18, f"{cur}/m**2", "rate card", note_rate), "nesting_utilisation_pct": _p(70, "%"),
            "assist_cost_eur_h": _p(2, f"{cur}/h"), "setup_time_h": _p(0.4, "h"), "handling_time_h": _p(0.1, "h"),
            "labour_rate_eur_h": _p(28, f"{cur}/h", "rate card", note_rate), "machine_rate_eur_h": _p(45, f"{cur}/h", "rate card", note_rate),
            "failure_rate_pct": _p(3, "%"), "margin_pct": _p(20, "%"),
        }
        derived = [_der("cut_time_h", "Cutting time", "h",
                        [_alt("passes * cut_length_mm / feed_mm_min + pierce_count * pierce_s")])]
        items = [
            _item("mat_geo", "geometric_material", "Sheet area of the part", "blank_area_cm2 * material_price_eur_m2", cur),
            _item("mat_consumables", "process_material", "Assist gas and consumables", "assist_cost_eur_h * cut_time_h", cur),
            _item("setup_prep", "setup", "Programming and set-up", "setup_time_h * labour_rate_eur_h", cur),
            _item("labour_handling", "labour", "Handling labour", "handling_time_h * labour_rate_eur_h", cur),
            _item("machine_time", "machine", "Machine time", "cut_time_h * machine_rate_eur_h", cur),
            _item("waste_nesting", "waste", "Nesting off-cuts", "mat_geo * (1 / nesting_utilisation_pct - 1)", cur),
            _item("waste_scrap", "waste", "Scrap", "failure_rate_pct * (mat_geo + machine_time)", cur),
            _item("margin", "margin", "Margin on cost",
                  "margin_pct * (mat_geo + mat_consumables + setup_prep + labour_handling + machine_time + waste_nesting + waste_scrap)", cur),
        ]
    else:  # concrete
        params = {
            "volume_cm3": _p(None, "cm**3", "measured"), "part_height_mm": _p(None, "mm", "measured"),
            "toolpath_length_m": _p(None, "m", "user", "total printed path from the toolpath file"),
            "print_speed_mm_s": _p(None, "mm/s", "user", "head speed with the mix and pump in use"),
            "layer_height_mm": _p(None, "mm", "user"), "layer_count": _p(None, "1", "user", "optional: overrides height / layer height"),
            "layer_wait_s": _p(None, "s", "user", "time between layers: repositioning and open time of the mix"),
            "mix_price_eur_m3": _p(180, f"{cur}/m**3", "rate card", note_rate), "overextrusion_pct": _p(8, "%"),
            "prime_volume_l": _p(40, "l"), "setup_time_h": _p(2, "h"), "setup_crew": _p(2, "1"),
            "print_crew": _p(2, "1"), "cleanup_time_h": _p(1.5, "h"),
            "labour_rate_eur_h": _p(30, f"{cur}/h", "rate card", note_rate), "machine_rate_eur_h": _p(60, f"{cur}/h", "rate card", note_rate),
            "mix_waste_pct": _p(6, "%"), "failure_rate_pct": _p(3, "%"), "margin_pct": _p(20, "%"),
        }
        derived = [
            _der("layer_count_used", "Layer count", "1", [
                _alt("layer_count"), _alt("ceil(part_height_mm / layer_height_mm)")]),
            _der("print_time_h", "Print time", "h", [
                _alt("toolpath_length_m / print_speed_mm_s + layer_count_used * layer_wait_s")]),
        ]
        items = [
            _item("mat_geo", "geometric_material", "Mix in the part geometry", "volume_cm3 * mix_price_eur_m3", cur),
            _item("mat_overextrusion", "process_material", "Over-extrusion", "mat_geo * overextrusion_pct", cur),
            _item("mat_prime", "process_material", "Pump priming and hose fill", "prime_volume_l * mix_price_eur_m3", cur),
            _item("setup_prep", "setup", "Set-up crew", "setup_time_h * setup_crew * labour_rate_eur_h", cur),
            _item("labour_print", "labour", "Crew during printing and clean-up",
                  "(print_time_h * print_crew + cleanup_time_h * print_crew) * labour_rate_eur_h", cur),
            _item("machine_time", "machine", "Printer and pump time", "print_time_h * machine_rate_eur_h", cur),
            _item("waste_mix", "waste", "Mix left in hoses and rejected batches", "mat_geo * mix_waste_pct", cur),
            _item("waste_scrap", "waste", "Failed segments (expected scrap)",
                  "failure_rate_pct * (mat_geo + mat_overextrusion + machine_time)", cur),
            _item("margin", "margin", "Margin on cost",
                  "margin_pct * (mat_geo + mat_overextrusion + mat_prime + setup_prep + labour_print + machine_time"
                  " + waste_mix + waste_scrap)", cur),
        ]
    for it in items:
        it["unit"] = it["unit"].replace("{cur}", cur)
    return {"process": process, "currency": cur, "options": {"volume_only": "coarse"}, "parameters": params,
            "derived": derived, "line_items": items}


# --------------------------------------------------------------------------------------------- spec checks
def _unit_of(text: str):
    return parse_unit(text)


def _param_quantity(p: dict):
    """Pint quantity of a parameter, or None when its value is missing. '%' is stored as a fraction."""
    v = p.get("value")
    if v is None:
        return None
    unit = (p.get("unit") or "1").strip()
    if unit == "%":
        return _q(float(v) / 100.0)
    return registry().Quantity(float(v), _unit_of(unit))


def normalize_spec(spec: dict) -> dict:
    """Validate a spec and fill defaults. Raises FabQuoteError (translated) with the first problem found."""
    if not isinstance(spec, dict):
        raise FabQuoteError(tx("fq.bad_spec"))
    spec = copy.deepcopy(spec)
    process = spec.get("process")
    if process not in PROCESSES:
        raise FabQuoteError(tx("fq.unknown_process", process=process, valid=list(PROCESSES)))
    spec.setdefault("currency", "EUR")
    if spec["currency"] not in ("EUR", "USD"):
        raise FabQuoteError(tx("fq.bad_currency", currency=spec["currency"]))
    opts = spec.setdefault("options", {})
    opts.setdefault("volume_only", "coarse")
    if opts["volume_only"] not in POLICIES:
        raise FabQuoteError(tx("fq.bad_policy", value=opts["volume_only"], valid=list(POLICIES)))
    names: set[str] = set()
    for name, p in (spec.get("parameters") or {}).items():
        if not _ID.match(name):
            raise FabQuoteError(tx("fq.bad_param_name", name=name))
        if not isinstance(p, dict):
            raise FabQuoteError(tx("fq.bad_param", name=name, why="object expected"))
        p.setdefault("value", None), p.setdefault("unit", "1"), p.setdefault("source", "user"), p.setdefault("note", "")
        if p["source"] not in SOURCES:
            raise FabQuoteError(tx("fq.bad_source", source=p["source"], valid=list(SOURCES)))
        v = p["value"]
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)):
            raise FabQuoteError(tx("fq.bad_param", name=name, why=tx("fq.not_a_number")))
        if p["unit"].strip() != "%":
            _unit_of(p["unit"])  # raises with the unit message
        names.add(name)
    spec.setdefault("parameters", {})
    nodes: dict[str, set[str]] = {}
    for kind in ("derived", "line_items"):
        for node in spec.get(kind) or []:
            nid = node.get("id")
            if not isinstance(nid, str) or not _ID.match(nid):
                raise FabQuoteError(tx("fq.bad_param_name", name=nid))
            if nid in names or nid in nodes:
                raise FabQuoteError(tx("fq.dup_id", name=nid))
            if kind == "line_items" and node.get("category") not in CATEGORIES:
                raise FabQuoteError(tx("fq.bad_category", category=node.get("category"), valid=list(CATEGORIES)))
            if "alternatives" not in node:
                node["alternatives"] = [_alt(node.get("formula", ""))]
            if not node["alternatives"]:
                raise FabQuoteError(tx("fq.no_formula", name=nid))
            for a in node["alternatives"]:
                a.setdefault("quality", "exact"), a.setdefault("warning", "")
                if a["quality"] not in QUALITY_RANK:
                    raise FabQuoteError(tx("fq.bad_param", name=nid, why=a["quality"]))
                nodes.setdefault(nid, set()).update(formula_names(a["formula"]))
            node.setdefault("label", nid)
            node.setdefault("unit", spec["currency"] if kind == "line_items" else "1")
            if node["unit"].strip() != "%":
                _unit_of(node["unit"])
    known = names | set(nodes)
    for nid, deps in nodes.items():
        for d in deps:
            if d not in known:
                raise FabQuoteError(tx("fq.unknown_name_in_formula", item=nid, name=d))
    order_nodes(spec)  # cycles
    if process == "concrete":
        der = {d["id"]: d for d in spec.get("derived") or []}
        pt = der.get("print_time_h")
        if pt is None:
            raise FabQuoteError(tx("fq.concrete_rule"))
        for a in pt["alternatives"]:
            ns = set(formula_names(a["formula"]))
            if not {"toolpath_length_m", "print_speed_mm_s"} <= ns or a["quality"] != "exact":
                raise FabQuoteError(tx("fq.concrete_rule"))
    return spec


def graph(spec: dict) -> dict[str, list[str]]:
    """node id -> names it depends on (static, over every alternative)."""
    g: dict[str, list[str]] = {}
    for kind in ("derived", "line_items"):
        for node in spec.get(kind) or []:
            deps: list[str] = []
            for a in node["alternatives"]:
                for n in formula_names(a["formula"]):
                    if n not in deps:
                        deps.append(n)
            g[node["id"]] = deps
    return g


def order_nodes(spec: dict) -> list[str]:
    g = graph(spec)
    done: list[str] = []
    state: dict[str, int] = {}

    def visit(n: str, path: list[str]):
        if n not in g:
            return
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            raise FabQuoteError(tx("fq.cycle", path=" -> ".join(path[path.index(n):] + [n])))
        state[n] = 1
        for d in g[n]:
            visit(d, path + [n])
        state[n] = 2
        done.append(n)

    for n in g:
        visit(n, [])
    return done


def dependents(spec: dict, changed: set[str]) -> set[str]:
    """Every node that depends, directly or not, on any name in `changed`."""
    g = graph(spec)
    out: set[str] = set()
    grow = True
    while grow:
        grow = False
        for n, deps in g.items():
            if n not in out and (set(deps) & (changed | out)):
                out.add(n)
                grow = True
    return out


# --------------------------------------------------------------------------------------------- evaluation
def _fmt_value(q, unit_text: str) -> float:
    if unit_text.strip() == "%":
        return float(q.to("dimensionless").magnitude) * 100.0
    return float(q.to(_unit_of(unit_text)).magnitude)


def _apply_overrides(spec: dict, overrides: Optional[dict]) -> dict:
    """A copy of the spec with parameter overrides ({name: number | {value, unit, source, note}})."""
    out = copy.deepcopy(spec)
    for name, ov in (overrides or {}).items():
        if name not in out["parameters"]:
            raise FabQuoteError(tx("fq.unknown_param", name=name, known=sorted(out["parameters"])))
        p = out["parameters"][name]
        if not isinstance(ov, dict):
            ov = {"value": ov}
        if "unit" in ov and ov["unit"] and ov["unit"] != p["unit"] and ov.get("value") is not None and p["unit"] != "%":
            # convert to the parameter's own unit so the stored unit never changes under the formulas
            try:
                v = registry().Quantity(float(ov["value"]), _unit_of(ov["unit"])).to(_unit_of(p["unit"])).magnitude
            except Exception:  # noqa: BLE001
                raise FabQuoteError(tx("fq.unit_mismatch", name=name, unit=ov["unit"], expected=p["unit"])) from None
            ov = {**ov, "value": v}
        for k in ("value", "source", "note"):
            if k in ov:
                p[k] = ov[k]
        if p["source"] not in SOURCES:
            raise FabQuoteError(tx("fq.bad_source", source=p["source"], valid=list(SOURCES)))
        v = p["value"]
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)):
            raise FabQuoteError(tx("fq.bad_param", name=name, why=tx("fq.not_a_number")))
    return out


def evaluate(spec: dict, base: Optional[dict] = None, changed: Optional[set[str]] = None) -> dict:
    """Evaluate a normalized spec. With `base` (a previous result of the same structure) and `changed` (parameter
    names whose value differs from the base), only the nodes that depend on them are recomputed; the others are
    copied. `result["recomputed"]` lists what was evaluated."""
    g = graph(spec)
    order = order_nodes(spec)
    policy = spec["options"]["volume_only"]
    redo = set(order) if base is None or changed is None else dependents(spec, set(changed))
    params = spec["parameters"]
    env: dict[str, Any] = {}
    missing_p: dict[str, bool] = {}
    for name, p in params.items():
        q = _param_quantity(p)
        if q is None:
            missing_p[name] = True
        else:
            env[name] = q
    nodes_by_id = {n["id"]: n for k in ("derived", "line_items") for n in spec.get(k) or []}
    results: dict[str, dict] = {}
    prev = {r["id"]: r for r in (base["derived"] + base["items"])} if base else {}
    for nid in order:
        node = nodes_by_id[nid]
        is_item = "category" in node
        if nid not in redo and nid in prev:
            r = copy.deepcopy(prev[nid])
            results[nid] = r
            if r["status"] == "ok":
                env[nid] = registry().Quantity(r["_si"], r["_si_unit"])
            continue
        results[nid] = _eval_node(node, is_item, g[nid], env, results, missing_p, params, policy)
        r = results[nid]
        if r["status"] == "ok":
            env[nid] = r.pop("_q")
            r["_si"], r["_si_unit"] = float(env[nid].magnitude), str(env[nid].units)
        else:
            r.pop("_q", None)
    return _assemble(spec, results, [n for n in order if n in redo])


def _root_missing(deps: list[str], results: dict, missing_p: dict) -> list[str]:
    out: list[str] = []
    for d in deps:
        if d in missing_p:
            out.append(d)
        elif d in results and results[d]["status"] != "ok":
            for m in results[d].get("missing", []):
                if m not in out:
                    out.append(m)
    return out


def _eval_node(node, is_item, deps, env, results, missing_p, params, policy) -> dict:
    rec = {"id": node["id"], "label": node.get("label", node["id"]), "unit": node["unit"], "deps": list(deps),
           "kind": "item" if is_item else "derived", "status": "ok", "quality": "exact", "warnings": [],
           "missing": [], "formula": None, "reason": None}
    if is_item:
        rec["category"] = node["category"]
    chosen = None
    for alt in node["alternatives"]:
        need = formula_names(alt["formula"])
        if all((n in env) for n in need):
            chosen = alt
            break
    if chosen is None:
        rec["status"] = "blocked"
        best = None  # the alternative that is closest to being computable names what is really missing
        for alt in node["alternatives"]:
            m = _root_missing(formula_names(alt["formula"]), results, missing_p)
            if best is None or len(m) <= len(best[1]):
                best = (alt, m)
        rec["missing"] = best[1]
        rec["formula"] = best[0]["formula"]
        rec["reason"] = tx("fq.blocked_missing", missing=", ".join(rec["missing"]) or "?")
        if spec_is_concrete_time(node) and "volume_cm3" in env:
            rec["reason"] = tx("fq.blocked_concrete_volume", missing=", ".join(rec["missing"]))
        return rec
    rec["formula"] = chosen["formula"]
    q = chosen["quality"]
    warn = [chosen["warning"]] if chosen.get("warning") else []
    for d in deps:
        if d in results and results[d]["status"] == "ok" and QUALITY_RANK[results[d]["quality"]] > QUALITY_RANK[q] and d in formula_names(chosen["formula"]):
            q = results[d]["quality"]
        if d in results and results[d]["status"] == "ok" and d in formula_names(chosen["formula"]):
            for w in results[d]["warnings"]:
                if w not in warn:
                    warn.append(w)
    rec["quality"] = q
    rec["warnings"] = [tx(w) if w.startswith("fq.") else w for w in warn]
    if q == "coarse" and policy == "block":
        rec["status"] = "blocked"
        rec["reason"] = tx("fq.blocked_coarse_policy", id=node["id"])
        rec["warnings"] = []
        return rec
    try:
        val = evaluate_formula(chosen["formula"], env)
        unit = node["unit"].strip()
        if unit == "%":
            out_q = _q(float(val.to("dimensionless").magnitude))
        else:
            out_q = val.to(_unit_of(unit))
    except FabQuoteError as exc:
        rec["status"], rec["reason"] = "error", str(exc)
        return rec
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "error"
        rec["reason"] = tx("fq.eval_error", formula=chosen["formula"], detail=str(exc).splitlines()[0])
        return rec
    rec["value"] = _fmt_value(out_q, unit)
    rec["_q"] = out_q
    return rec


def spec_is_concrete_time(node) -> bool:
    return node["id"] == "print_time_h" and any("toolpath_length_m" in formula_names(a["formula"]) for a in node["alternatives"])


def _assemble(spec: dict, results: dict, recomputed: list[str]) -> dict:
    params = spec["parameters"]
    g = graph(spec)
    items = [results[n["id"]] for n in spec["line_items"]]
    derived = [results[n["id"]] for n in spec.get("derived") or []]
    used_by: dict[str, list[str]] = {p: [] for p in params}
    for nid, deps in g.items():
        for d in deps:
            if d in used_by:
                used_by[d].append(nid)
    ok_items = [i for i in items if i["status"] == "ok"]
    blocked = [i["id"] for i in items if i["status"] != "ok"]
    total = sum(i["value"] for i in ok_items)
    cats = {c: sum(i["value"] for i in ok_items if i["category"] == c) for c in CATEGORIES}
    quality = "coarse" if any(i["quality"] == "coarse" for i in ok_items) else "exact"
    warnings: list[str] = []
    for r in derived + items:
        for w in r["warnings"]:
            if w not in warnings:
                warnings.append(w)
    if blocked:
        warnings.append(tx("fq.warn_incomplete", items=", ".join(blocked)))
    assumed = [n for n, p in params.items() if p["source"] == "assumed" and used_by.get(n)]
    if assumed:
        warnings.append(tx("fq.warn_assumed", n=len(assumed), names=", ".join(assumed)))
    clean = lambda r: {k: v for k, v in r.items() if k not in ("_q",)}  # noqa: E731
    return {
        "process": spec["process"], "currency": spec["currency"], "complete": not blocked,
        "total": {"value": total, "unit": spec["currency"], "partial": bool(blocked)},
        "categories": cats, "quality": quality,
        "items": [clean(i) for i in items], "derived": [clean(d) for d in derived], "blocked": blocked,
        "parameters": [{"name": n, "value": p.get("value"), "unit": p["unit"], "source": p["source"], "note": p.get("note", ""),
                        "missing": p.get("value") is None, "used_by": used_by[n]} for n, p in params.items()],
        "graph": g, "recomputed": recomputed, "warnings": warnings, "caveat": tx("fq.caveat"),
    }


def public(result: dict) -> dict:
    """The result without the internal SI cache used to copy unchanged nodes."""
    out = dict(result)
    for k in ("items", "derived"):
        out[k] = [{kk: vv for kk, vv in r.items() if not kk.startswith("_si")} for r in result[k]]
    return out


def compute(spec: dict, overrides: Optional[dict] = None, base: Optional[dict] = None) -> tuple[dict, dict]:
    """(spec with overrides applied, result). Passing `base` (the result of `spec` alone) makes it incremental."""
    if not overrides:
        return spec, (base if base is not None else evaluate(spec))
    s2 = _apply_overrides(spec, overrides)
    if base is None:
        return s2, evaluate(s2)
    changed = {n for n in overrides
               if (s2["parameters"][n].get("value"), s2["parameters"][n].get("source"))
               != (spec["parameters"][n].get("value"), spec["parameters"][n].get("source"))}
    return s2, evaluate(s2, base, changed)


# --------------------------------------------------------------------------------------------- diff / sensitivity
def _closure(g: dict, node: str, params: set[str]) -> set[str]:
    out, stack = set(), list(g.get(node, []))
    while stack:
        n = stack.pop()
        if n in out:
            continue
        out.add(n)
        stack.extend(g.get(n, []))
    return out & params


def diff_results(spec_before: dict, before: dict, spec_after: dict, after: dict) -> dict:
    g = graph(spec_after)
    pnames = set(spec_after["parameters"])
    pchanges = {}
    for n in pnames:
        a, b = spec_before["parameters"][n], spec_after["parameters"][n]
        if (a.get("value"), a.get("source"), a.get("note")) != (b.get("value"), b.get("source"), b.get("note")):
            pchanges[n] = {"before": a.get("value"), "after": b.get("value"), "unit": b["unit"],
                           "source_before": a["source"], "source_after": b["source"]}
    changes = []
    bmap = {r["id"]: r for r in before["derived"] + before["items"]}
    for r in after["derived"] + after["items"]:
        o = bmap[r["id"]]
        ov, nv = o.get("value"), r.get("value")
        if o["status"] == r["status"] and ov == nv and o["formula"] == r["formula"]:
            continue
        because = sorted(_closure(g, r["id"], set(pchanges)))
        srcs = {p: spec_after["parameters"][p]["source"] for p in sorted(_closure(g, r["id"], pnames))}
        changes.append({"id": r["id"], "label": r["label"], "kind": r["kind"], "category": r.get("category"), "unit": r["unit"],
                        "before": ov, "after": nv, "delta": (nv - ov) if ov is not None and nv is not None else None,
                        "delta_pct": ((nv - ov) / ov * 100.0) if ov not in (None, 0) and nv is not None else None,
                        "status_before": o["status"], "status_after": r["status"],
                        "formula": r["formula"], "because": [{"parameter": p, **pchanges[p]} for p in because],
                        "sources": srcs})
    bt, at = before["total"]["value"], after["total"]["value"]
    return {"parameters_changed": pchanges, "changes": changes,
            "unchanged": [r["id"] for r in after["items"] if r["id"] not in {c["id"] for c in changes}],
            "total": {"before": bt, "after": at, "delta": at - bt, "delta_pct": ((at - bt) / bt * 100.0) if bt else None,
                      "unit": after["total"]["unit"], "partial_before": before["total"]["partial"],
                      "partial_after": after["total"]["partial"]},
            "recomputed": after["recomputed"]}


def sensitivity(spec: dict, base: dict, params: Optional[list[str]] = None, pct: float = 10.0) -> dict:
    if pct <= 0 or pct >= 100:
        raise FabQuoteError(tx("fq.bad_pct"))
    g_used = {p["name"]: p for p in base["parameters"]}
    for n in (params or []):
        if n not in spec["parameters"]:
            raise FabQuoteError(tx("fq.unknown_param", name=n, known=sorted(spec["parameters"])))
    cand = list(params or [n for n, p in g_used.items()
                           if p["used_by"] and not p["missing"] and spec["parameters"][n]["unit"] != "1"])
    rows = []
    for n in cand:
        p = spec["parameters"][n]
        if p.get("value") is None:
            rows.append({"parameter": n, "skipped": tx("fq.sens_missing")})
            continue
        v0 = p["value"]
        pts = {}
        for tag, f in (("low", 1 - pct / 100.0), ("high", 1 + pct / 100.0)):
            s2, r = compute(spec, {n: {"value": v0 * f, "source": p["source"]}}, base)
            pts[tag] = {"value": v0 * f, "total": r["total"]["value"], "recomputed": r["recomputed"]}
        lo, hi = pts["low"]["total"], pts["high"]["total"]
        bt = base["total"]["value"]
        rows.append({"parameter": n, "unit": p["unit"], "source": p["source"], "base_value": v0, "base_total": bt,
                     "low": pts["low"], "high": pts["high"], "swing": abs(hi - lo),
                     "swing_pct_of_total": (abs(hi - lo) / bt * 100.0) if bt else None,
                     "recomputed": r["recomputed"]})
    ranked = sorted([r for r in rows if "swing" in r], key=lambda r: -r["swing"])
    skipped = [r for r in rows if "skipped" in r]
    if not ranked and not skipped:
        raise FabQuoteError(tx("fq.sens_no_params"))
    return {"pct": pct, "method": tx("fq.sens_method", pct=pct), "base_total": base["total"], "ranking": ranked,
            "skipped": skipped, "partial": base["total"]["partial"]}


# --------------------------------------------------------------------------------------------- Vulcan
def vulcan_base_url(url: Optional[str] = None) -> str:
    return (url or os.environ.get("NIGHTINGALE_VULCAN_URL") or VULCAN_DEFAULT).rstrip("/")


def _get_json(url: str, timeout: float) -> tuple[Any, bytes]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-chosen local service
        raw = resp.read(8_000_000)
    return json.loads(raw.decode("utf-8")), raw


def fetch_vulcan(model_id: Any, base_url: Optional[str] = None, target: str = "fdm-print", timeout: float = 4.0,
                 now: Optional[datetime] = None) -> dict:
    """Measurements of a library model from Vulcan, with provenance. Missing fields stay missing."""
    base = vulcan_base_url(base_url)
    mid = str(model_id).strip()
    if not mid or not re.match(r"^[A-Za-z0-9_.\-]{1,80}$", mid):
        raise FabQuoteError(tx("fq.vulcan_model_id"))
    url = f"{base}/library/api/models/{mid}"
    try:
        data, raw = _get_json(url, timeout)
    except urllib.error.HTTPError as exc:
        raise FabQuoteError(tx("fq.vulcan_http", url=url, code=exc.code)) from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise FabQuoteError(tx("fq.vulcan_unreachable", url=base, detail=str(getattr(exc, "reason", exc)))) from None
    except ValueError:
        raise FabQuoteError(tx("fq.vulcan_bad_payload", url=url)) from None
    if not isinstance(data, dict):
        raise FabQuoteError(tx("fq.vulcan_bad_payload", url=url))
    model = data.get("model") if isinstance(data.get("model"), dict) else data
    when = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    prov = {"source": "vulcan", "model_id": mid, "base_url": base, "endpoint": url, "fetched_at": when,
            "response_sha256": hashlib.sha256(raw).hexdigest()}
    if model.get("sha256"):
        prov["model_sha256"] = model["sha256"]
    warnings: list[str] = []
    meas: dict[str, dict] = {}

    def num(key):
        v = model.get(key)
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None

    for key, name, unit in (("volume_cm3", "volume_cm3", "cm**3"), ("surface_cm2", "surface_cm2", "cm**2")):
        v = num(key)
        meas[name] = {"value": v, "unit": unit, "source": "measured", "note": tx("fq.note_vulcan_model", mid=mid)}
        if v is None:
            warnings.append(tx("fq.vulcan_field_missing", field=key))
    ug = model.get("units_guess")
    unit_ok = isinstance(ug, str) and ug.strip().lower() in ("mm", "cm", "m", "in", "inch", "inches")
    ug_unit = {"inch": "in", "inches": "in"}.get((ug or "").strip().lower(), (ug or "").strip().lower())
    bbox = {}
    for axis in "xyz":
        v = num(f"bbox_{axis}")
        if v is not None and unit_ok:
            bbox[axis] = registry().Quantity(v, ug_unit).to("mm").magnitude
        elif v is None:
            warnings.append(tx("fq.vulcan_field_missing", field=f"bbox_{axis}"))
    if not unit_ok and any(num(f"bbox_{a}") is not None for a in "xyz"):
        warnings.append(tx("fq.vulcan_units_unknown", value=ug))
    meas["part_height_mm"] = {"value": bbox.get("z"), "unit": "mm", "source": "measured", "note": tx("fq.note_vulcan_bbox", axis="z", units=ug)}
    meas["bbox_x_mm"] = {"value": bbox.get("x"), "unit": "mm", "source": "measured", "note": tx("fq.note_vulcan_bbox", axis="x", units=ug)}
    meas["bbox_y_mm"] = {"value": bbox.get("y"), "unit": "mm", "source": "measured", "note": tx("fq.note_vulcan_bbox", axis="y", units=ug)}
    if "x" in bbox and "y" in bbox:
        meas["blank_area_cm2"] = {"value": bbox["x"] * bbox["y"] / 100.0, "unit": "cm**2", "source": "measured",
                                  "note": tx("fq.note_blank_envelope")}
    if model.get("watertight") is False:
        warnings.append(tx("fq.vulcan_not_watertight"))
    prov["geometry"] = {k: model.get(k) for k in ("triangles", "bodies", "watertight", "units_guess") if k in model}
    insp = None
    try:
        insp, iraw = _get_json(f"{url}/inspection?target={target}", timeout)
        prov["inspection"] = {"target": target, "sha256": hashlib.sha256(iraw).hexdigest(),
                              "report": _short(insp)}
    except Exception as exc:  # noqa: BLE001 - the report is optional
        warnings.append(tx("fq.vulcan_no_inspection", detail=str(getattr(exc, "reason", exc))))
    return {"measurements": meas, "provenance": prov, "warnings": warnings}


def _short(obj: Any, limit: int = 4000) -> Any:
    text = json.dumps(obj, ensure_ascii=False)
    return obj if len(text) <= limit else {"truncated": True, "text": text[:limit]}


# --------------------------------------------------------------------------------------------- storage / operations
def _dir(services) -> Path:
    d = Path(services.config.data_dir) / "fabquotes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(services, name: str) -> Path:
    if not isinstance(name, str) or not _NAME.match(name):
        raise FabQuoteError(tx("fq.name"))
    return _dir(services) / (name + ".json")


def _now(services) -> str:
    return datetime.fromtimestamp(services.clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load(services, name: str) -> dict:
    f = _file(services, name)
    if not f.exists():
        raise LookupError(tx("fq.unknown_quote", name=name, saved=[q["name"] for q in list_quotes(services)["quotes"]]))
    return json.loads(f.read_text(encoding="utf-8"))


def _save(services, name: str, doc: dict) -> None:
    _file(services, name).write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def templates() -> dict:
    out = []
    for p in PROCESSES:
        s = build_template(p)
        out.append({"process": p, "parameters": {n: {k: v for k, v in d.items()} for n, d in s["parameters"].items()},
                    "derived": [d["id"] for d in s["derived"]], "line_items": [i["id"] for i in s["line_items"]],
                    "categories": CATEGORIES, "sources": SOURCES})
    return {"templates": out, "categories": list(CATEGORIES), "sources": list(SOURCES)}


def list_quotes(services) -> dict:
    out = []
    for f in sorted(_dir(services).glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append({"name": f.stem, "process": d["spec"]["process"], "scenarios": sorted(d.get("scenarios", {})),
                    "actuals": len(d.get("actuals", [])), "updated": f.stat().st_mtime})
    return {"quotes": out, "count": len(out)}


def _view(services, name: str, doc: dict, extra: Optional[dict] = None) -> dict:
    spec = doc["spec"]
    res = evaluate(spec)
    out = {"name": name, "process": spec["process"], "currency": spec["currency"], "options": spec["options"],
           "provenance": doc.get("provenance"), "scenarios": doc.get("scenarios", {}), "actuals": doc.get("actuals", []),
           "result": public(res), "warnings": res["warnings"] + doc.get("input_warnings", []) + (doc.get("inspection") or {}).get("texts", []),
           "inspection": doc.get("inspection"), "calibrations": doc.get("calibrations", [])}
    if extra:
        out.update(extra)
    return out


def create_quote(services, name: str, process: Optional[str] = None, measurements: Optional[dict] = None,
                 parameters: Optional[dict] = None, rate_card: Optional[dict] = None, vulcan: Optional[dict] = None,
                 volume_only: Optional[str] = None, currency: str = "EUR", spec: Optional[dict] = None,
                 replace: bool = False, source: str = "ui") -> dict:
    def do():
        f = _file(services, name)
        if f.exists() and not replace:
            raise FabQuoteError(tx("fq.exists", name=name))
        base_spec = spec if spec is not None else build_template(process or "", currency)
        base_spec = normalize_spec(base_spec)
        if volume_only:
            base_spec["options"]["volume_only"] = volume_only
        prov: dict[str, Any] = {"created_at": _now(services), "measurements": {}}
        warn: list[str] = []
        if vulcan:
            got = fetch_vulcan(vulcan.get("model_id"), vulcan.get("base_url"), vulcan.get("target") or "fdm-print")
            prov["vulcan"] = got["provenance"]
            warn += got["warnings"]
            for pname, m in got["measurements"].items():
                if pname in base_spec["parameters"] and m["value"] is not None:
                    base_spec["parameters"][pname].update({"value": m["value"], "source": "measured", "note": m["note"]})
                    prov["measurements"][pname] = "vulcan"
        for group, src in ((measurements, "measured"), (rate_card, "rate card"), (parameters, None)):
            if not group:
                continue
            ov = {}
            for k, v in group.items():
                d = dict(v) if isinstance(v, dict) else {"value": v}
                d.setdefault("source", src or "user")
                ov[k] = d
            base_spec = _apply_overrides(base_spec, ov)
            if src == "measured":
                for k in group:
                    prov["measurements"][k] = "explicit"
        base_spec = normalize_spec(base_spec)
        doc = {"spec": base_spec, "provenance": prov, "scenarios": {}, "actuals": [], "input_warnings": warn}
        _save(services, name, doc)
        view = _view(services, name, doc)
        view["_log_summary"] = f"created fabrication quote {name!r} ({base_spec['process']})"
        return view
    return services._log("fabquote_create", source, None, {"name": name, "process": process}, do)


def get_quote(services, name: str) -> dict:
    return _view(services, name, _load(services, name))


def delete_quote(services, name: str, source: str = "ui") -> dict:
    def do():
        f = _file(services, name)
        if not f.exists():
            raise LookupError(tx("fq.unknown_quote", name=name, saved=[q["name"] for q in list_quotes(services)["quotes"]]))
        f.unlink()
        return {"name": name, "deleted": True, "_log_summary": f"deleted fabrication quote {name!r}"}
    return services._log("fabquote_delete", source, None, {"name": name}, do)


def set_parameters(services, name: str, parameters: dict, source: str = "ui") -> dict:
    """Change the base assumptions of a stored quote; reports which items changed and why (incremental recompute)."""
    def do():
        doc = _load(services, name)
        spec = doc["spec"]
        before = evaluate(spec)
        if not parameters:
            raise FabQuoteError(tx("fq.nothing_to_set"))
        ov = {k: (dict(v) if isinstance(v, dict) else {"value": v}) for k, v in parameters.items()}
        for d in ov.values():
            d.setdefault("source", "user")
        new_spec, after = compute(spec, ov, before)
        new_spec = normalize_spec(new_spec)
        doc["spec"] = new_spec
        _save(services, name, doc)
        out = {"name": name, "diff": diff_results(spec, before, new_spec, after), "result": public(after),
               "_log_summary": f"changed {len(ov)} parameters of fabrication quote {name!r}"}
        return out
    return services._log("fabquote_set", source, None, {"name": name, "parameters": list(parameters or {})}, do)


def set_options(services, name: str, volume_only: str, source: str = "ui") -> dict:
    def do():
        if volume_only not in POLICIES:
            raise FabQuoteError(tx("fq.bad_policy", value=volume_only, valid=list(POLICIES)))
        doc = _load(services, name)
        before_spec = doc["spec"]
        before = evaluate(before_spec)
        spec = copy.deepcopy(before_spec)
        spec["options"]["volume_only"] = volume_only
        after = evaluate(spec)
        doc["spec"] = spec
        _save(services, name, doc)
        return {"name": name, "diff": diff_results(before_spec, before, spec, after), "result": public(after),
                "_log_summary": f"set volume-only policy {volume_only!r} on {name!r}"}
    return services._log("fabquote_options", source, None, {"name": name, "volume_only": volume_only}, do)


_SCN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.\-]{0,39}$")


def save_scenario(services, name: str, scenario: str, overrides: dict, description: str = "", source: str = "ui") -> dict:
    def do():
        if not isinstance(scenario, str) or not _SCN.match(scenario) or scenario.strip().lower() == "base":
            raise FabQuoteError(tx("fq.scenario_name"))
        if not overrides:
            raise FabQuoteError(tx("fq.scenario_empty"))
        doc = _load(services, name)
        clean = {k: (dict(v) if isinstance(v, dict) else {"value": v}) for k, v in overrides.items()}
        for d in clean.values():
            d.setdefault("source", "assumed")
        _apply_overrides(doc["spec"], clean)  # validate names, units and sources
        doc.setdefault("scenarios", {})[scenario] = {"overrides": clean, "description": description}
        _save(services, name, doc)
        return {"name": name, "scenario": scenario, "saved": True, "_log_summary": f"saved scenario {scenario!r} on {name!r}"}
    return services._log("fabquote_scenario_save", source, None, {"name": name, "scenario": scenario}, do)


def delete_scenario(services, name: str, scenario: str, source: str = "ui") -> dict:
    def do():
        doc = _load(services, name)
        if scenario not in doc.get("scenarios", {}):
            raise LookupError(tx("fq.scenario_unknown", scenario=scenario, known=sorted(doc.get("scenarios", {}))))
        del doc["scenarios"][scenario]
        _save(services, name, doc)
        return {"name": name, "scenario": scenario, "deleted": True, "_log_summary": f"deleted scenario {scenario!r} on {name!r}"}
    return services._log("fabquote_scenario_delete", source, None, {"name": name, "scenario": scenario}, do)


def _scenario_names(doc: dict, names: Optional[list[str]]) -> list[str]:
    avail = doc.get("scenarios", {})
    if not avail and not names:
        raise FabQuoteError(tx("fq.scenario_none"))
    use = names or sorted(avail)
    for n in use:
        if n not in avail:
            raise LookupError(tx("fq.scenario_unknown", scenario=n, known=sorted(avail)))
    return use


def compare_scenarios(services, name: str, names: Optional[list[str]] = None, source: str = "ui") -> dict:
    def do():
        doc = _load(services, name)
        spec = doc["spec"]
        base = evaluate(spec)
        rows = []
        for sn in _scenario_names(doc, names):
            sc = doc["scenarios"][sn]
            s2, res = compute(spec, sc["overrides"], base)
            rows.append({"scenario": sn, "description": sc.get("description", ""), "total": res["total"],
                         "categories": res["categories"], "warnings": res["warnings"],
                         "diff": diff_results(spec, base, s2, res)})
        return {"name": name, "base": {"total": base["total"], "categories": base["categories"]}, "scenarios": rows,
                "caveat": tx("fq.caveat"), "_log_summary": f"compared {len(rows)} scenarios on {name!r}"}
    return services._log("fabquote_compare", source, None, {"name": name, "scenarios": names}, do)


def run_sensitivity(services, name: str, params: Optional[list[str]] = None, pct: float = 10.0,
                    scenario: Optional[str] = None, source: str = "ui") -> dict:
    def do():
        doc = _load(services, name)
        spec = doc["spec"]
        if scenario:
            _scenario_names(doc, [scenario])
            spec = _apply_overrides(spec, doc["scenarios"][scenario]["overrides"])
        out = sensitivity(spec, evaluate(spec), params, pct)
        out.update({"name": name, "scenario": scenario, "_log_summary": f"sensitivity ±{pct}% on {name!r}"})
        return out
    return services._log("fabquote_sensitivity", source, None, {"name": name, "pct": pct}, do)


# ---- actuals ------------------------------------------------------------------------------------
def record_actual(services, name: str, item: str, value: float, unit: Optional[str] = None, ledger_ref: Optional[str] = None,
                  note: str = "", source: str = "ui") -> dict:
    """Store a fact after the job (what it really cost or took) for one line item or derived quantity."""
    def do():
        doc = _load(services, name)
        spec = doc["spec"]
        node = next((n for k in ("line_items", "derived") for n in spec.get(k) or [] if n["id"] == item), None)
        if node is None:
            raise LookupError(tx("fq.actual_unknown_item", item=item, known=[n["id"] for k in ("line_items", "derived") for n in spec.get(k) or []]))
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise FabQuoteError(tx("fq.actual_value"))
        use_unit = (unit or node["unit"]).strip()
        try:
            v = float(value) if use_unit == "%" else registry().Quantity(float(value), _unit_of(use_unit)).to(_unit_of(node["unit"])).magnitude
        except FabQuoteError:
            raise
        except Exception:  # noqa: BLE001
            raise FabQuoteError(tx("fq.actual_unit", item=item, unit=use_unit, expected=node["unit"])) from None
        rec = {"id": len(doc.get("actuals", [])) + 1, "item": item, "value": v, "unit": node["unit"], "entered_as": {"value": value, "unit": use_unit},
               "ledger_ref": ledger_ref, "note": note, "recorded_at": _now(services)}
        doc.setdefault("actuals", []).append(rec)
        _save(services, name, doc)
        return {"name": name, "actual": rec, "_log_summary": f"recorded actual for {item!r} on {name!r}"}
    return services._log("fabquote_actual", source, None, {"name": name, "item": item}, do)


def delete_actual(services, name: str, actual_id: int, source: str = "ui") -> dict:
    def do():
        doc = _load(services, name)
        keep = [a for a in doc.get("actuals", []) if a["id"] != actual_id]
        if len(keep) == len(doc.get("actuals", [])):
            raise LookupError(tx("fq.actual_unknown", id=actual_id))
        doc["actuals"] = keep
        _save(services, name, doc)
        return {"name": name, "deleted": actual_id, "_log_summary": f"deleted actual {actual_id} on {name!r}"}
    return services._log("fabquote_actual_delete", source, None, {"name": name, "id": actual_id}, do)


def compare_actuals(services, name: str, scenario: Optional[str] = None) -> dict:
    doc = _load(services, name)
    spec = doc["spec"]
    if scenario:
        _scenario_names(doc, [scenario])
        spec = _apply_overrides(spec, doc["scenarios"][scenario]["overrides"])
    res = evaluate(spec)
    by = {r["id"]: r for r in res["items"] + res["derived"]}
    acts: dict[str, list[dict]] = {}
    for a in doc.get("actuals", []):
        acts.setdefault(a["item"], []).append(a)
    rows, quoted_cmp, actual_cmp = [], 0.0, 0.0
    for nid, r in by.items():
        got = acts.get(nid)
        if not got:
            if r["kind"] == "item":
                rows.append({"item": nid, "label": r["label"], "unit": r["unit"], "quoted": r.get("value"), "actual": None,
                             "status": "no_actual"})
            continue
        actual = sum(a["value"] for a in got)
        q = r.get("value")
        row = {"item": nid, "label": r["label"], "kind": r["kind"], "unit": r["unit"], "quoted": q, "actual": actual,
               "n_records": len(got), "ledger_refs": [a["ledger_ref"] for a in got if a.get("ledger_ref")],
               "status": "ok" if q is not None else "quote_blocked", "formula": r["formula"],
               "deps": r["deps"]}
        if q is not None:
            row["delta"] = actual - q
            row["delta_pct"] = (actual - q) / q * 100.0 if q else None
            row["ratio_actual_to_quote"] = actual / q if q else None
            if r["kind"] == "item":
                quoted_cmp += q
                actual_cmp += actual
        rows.append(row)
    n_items = len([r for r in res["items"]])
    compared = [r for r in rows if r.get("kind") == "item" and r["status"] == "ok"]
    return {"name": name, "scenario": scenario, "rows": rows,
            "totals": {"quoted_compared": quoted_cmp, "actual": actual_cmp, "delta": actual_cmp - quoted_cmp,
                       "delta_pct": ((actual_cmp - quoted_cmp) / quoted_cmp * 100.0) if quoted_cmp else None,
                       "items_compared": len(compared), "items_total": n_items, "unit": spec["currency"],
                       "note": tx("fq.actuals_subset")},
            "note": tx("fq.calibration_note"), "facts": doc.get("actuals", [])}


# ---- export -------------------------------------------------------------------------------------
def export_quote(services, name: str, fmt: str = "json", scenario: Optional[str] = None) -> dict:
    fmt = (fmt or "json").lower()
    if fmt not in ("json", "csv", "markdown", "md"):
        raise FabQuoteError(tx("fq.export_format", fmt=fmt))
    doc = _load(services, name)
    spec = doc["spec"]
    label = "Base"
    if scenario:
        _scenario_names(doc, [scenario])
        spec = _apply_overrides(spec, doc["scenarios"][scenario]["overrides"])
        label = scenario
    res = public(evaluate(spec))
    src = {p["name"]: p["source"] for p in res["parameters"]}

    def srcs(r):
        return "; ".join(f"{n} ({src[n]})" for n in sorted(_closure(res["graph"], r["id"], set(src))))

    if fmt == "json":
        content = json.dumps({"name": name, "scenario": label, "spec": spec, "result": res, "provenance": doc.get("provenance"),
                              "actuals": doc.get("actuals", [])}, indent=2, ensure_ascii=False)
        return {"format": "json", "filename": f"{name}.json", "mime": "application/json", "content": content}
    rows = res["derived"] + res["items"]
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["kind", "category", "id", "label", "value", "unit", "status", "quality", "formula", "depends_on", "sources", "notes"])
        for p in res["parameters"]:
            w.writerow(["parameter", "", p["name"], "", "" if p["value"] is None else p["value"], p["unit"],
                        "missing" if p["missing"] else "ok", "", "", "", p["source"], p["note"]])
        for r in rows:
            w.writerow([r["kind"], r.get("category", ""), r["id"], r["label"], "" if r.get("value") is None else r["value"], r["unit"],
                        r["status"], r["quality"], r["formula"], " ".join(r["deps"]), srcs(r),
                        " | ".join(r["warnings"] + ([r["reason"]] if r.get("reason") else []))])
        w.writerow(["total", "", "total", "", res["total"]["value"], res["currency"],
                    "partial" if res["total"]["partial"] else "ok", res["quality"], "sum of line items", "", "", ""])
        return {"format": "csv", "filename": f"{name}.csv", "mime": "text/csv", "content": buf.getvalue()}
    md = [f"# {name} — {label}", "", f"_{res['caveat']}_", "", f"## {tx('fq.md_params')}", "",
          "| name | value | unit | source | note |", "|---|---:|---|---|---|"]
    for p in res["parameters"]:
        md.append(f"| `{p['name']}` | {'—' if p['value'] is None else p['value']} | {p['unit']} | {p['source']} | {p['note']} |")
    md += ["", f"## {tx('fq.md_items')}", "", "| category | id | value | unit | status | formula | depends on | sources |",
           "|---|---|---:|---|---|---|---|---|"]
    for r in rows:
        v = "—" if r.get("value") is None else f"{r['value']:.4g}" if r["kind"] == "derived" else f"{r['value']:.2f}"
        md.append(f"| {r.get('category', 'derived')} | `{r['id']}` | {v} | {r['unit']} | {r['status']}{' (' + r['quality'] + ')' if r['quality'] == 'coarse' else ''} "
                  f"| `{r['formula']}` | {' '.join(r['deps'])} | {srcs(r)} |")
    md += ["", f"**{tx('fq.md_total')}: {res['total']['value']:.2f} {res['currency']}**" + (f" ({tx('fq.md_partial')})" if res["total"]["partial"] else "")]
    if res["warnings"]:
        md += ["", f"## {tx('fq.md_warnings')}", ""] + [f"- {w}" for w in res["warnings"]]
    return {"format": "markdown", "filename": f"{name}.md", "mime": "text/markdown", "content": "\n".join(md) + "\n"}
