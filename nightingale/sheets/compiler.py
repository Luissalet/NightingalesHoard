"""Workbook → executable calculation model.

Reads formulas and cached values from an .xlsx, resolves defined names, builds
the dependency graph, separates parameters (cells formulas read), lookup tables
(cells only read through ranges) and results (formulas nobody else reads),
orders the formulas and generates Python code for each one. The same generated
code runs inside Nightingale and in the exported standalone module.

Anything outside the supported subset is *frozen*: the cell keeps the value
Excel cached and is listed with the reason, so nothing is guessed silently.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils.cell import get_column_letter
from openpyxl.worksheet.formula import ArrayFormula

from . import runtime as rt
from .parser import parse, parse_reference, walk

Key = tuple  # (sheet, row, col)

_OPS = {"+": "op_add", "-": "op_sub", "*": "op_mul", "/": "op_div", "^": "op_pow", "&": "op_cat",
        "=": "op_eq", "<>": "op_ne", "<": "op_lt", "<=": "op_le", ">": "op_gt", ">=": "op_ge"}


def quote_sheet(name: str) -> str:
    if re.fullmatch(r"[^\W\d]\w*", name) and not re.fullmatch(r"[A-Za-z]{1,3}\d+", name):
        return name
    return "'" + name.replace("'", "''") + "'"


def addr(key: Key) -> str:
    sheet, row, col = key
    return f"{quote_sheet(sheet)}!{get_column_letter(col)}{row}"


def parse_addr(text: str, default_sheet: str | None = None) -> Key:
    node = parse_reference(text, default_sheet or "")
    if not node or node[0] != "ref" or not node[1]:
        raise ValueError(f"not a cell address: {text!r}")
    return (node[1], node[2], node[3])


def is_date_format(fmt: str) -> bool:
    f = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", fmt or "").lower()
    return bool(re.search(r"[dy]", f)) or ("m" in f and not re.search(r"[0#?]", f))


def _plain(v, date_system="1900"):
    """Cell value as stored by openpyxl → runtime value."""
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return rt.to_serial(v, date_system)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        if v in rt.ERRORS:
            return rt.ERRORS[v]
        return v
    return v


def to_json(v):
    if isinstance(v, rt.XlError):
        return {"error": v.code}
    if isinstance(v, float) and v.is_integer() and abs(v) < 2**53:
        return int(v)
    return v


def from_json(v):
    if isinstance(v, dict) and "error" in v:
        return rt.ERRORS.get(v["error"], rt.XlError(v["error"]))
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    raise ValueError(f"unsupported value {v!r}")


@dataclass
class Cell:
    key: Key
    formula: str | None = None
    value: Any = None  # constant value, or cached value for formulas
    ast: Any = None
    frozen: str | None = None  # reason, when the formula is not executed
    volatile: bool = False
    number_format: str = "General"
    literal_formula: bool = False


@dataclass
class Model:
    name: str
    sha256: str
    sheets: dict[str, tuple[int, int]]
    cells: dict[Key, Cell]
    order: list[Key]
    deps: dict[Key, set]
    parameters: list[Key]
    tables: list[Key]
    results: list[Key]
    labels: dict[Key, str]
    options: dict[Key, list]
    names: dict[str, str]
    diagnostics: list[dict] = field(default_factory=list)
    cycles: list[list[Key]] = field(default_factory=list)
    source: str = ""
    date_system: str = "1900"

    def summary(self) -> dict:
        formulas = [k for k in self.order]
        return {
            "name": self.name,
            "sha256": self.sha256,
            "date_system": self.date_system,
            "sheets": [{"name": s, "rows": r, "cols": c} for s, (r, c) in self.sheets.items()],
            "formulas": len([c for c in self.cells.values() if c.formula]),
            "executed": len([k for k in formulas if not self.cells[k].frozen]),
            "frozen": [{"cell": addr(k), "formula": c.formula, "reason": c.frozen} for k, c in self.cells.items() if c.frozen],
            "volatile": [addr(k) for k, c in self.cells.items() if c.volatile],
            "cycles": [[addr(k) for k in cyc] for cyc in self.cycles],
            "parameters": [self.describe(k) for k in self.parameters],
            "tables": len(self.tables),
            "results": [self.describe(k) for k in self.results],
            "names": self.names,
            "functions": sorted({n[1] for c in self.cells.values() if c.ast for n in walk(c.ast) if n[0] == "call"}),
            "diagnostics": self.diagnostics,
        }

    def describe(self, key: Key) -> dict:
        c = self.cells.get(key)
        d = {"cell": addr(key), "label": self.labels.get(key), "value": to_json(c.value) if c else None}
        if c and c.formula:
            d["formula"] = c.formula
        if key in self.options:
            d["options"] = [to_json(o) for o in self.options[key]]
        if c and c.number_format and c.number_format != "General":
            d["number_format"] = c.number_format
            if is_date_format(c.number_format) and isinstance(c.value, float):
                d["display"] = rt.from_serial(c.value, self.date_system).date().isoformat()
        return d


def _resolve_names(wb) -> tuple[dict[str, Any], dict[str, str]]:
    out, text = {}, {}
    for name, dn in wb.defined_names.items():
        if dn.localSheetId is not None or not dn.attr_text:
            continue
        text[name] = dn.attr_text
        out[name.upper()] = dn.attr_text
    for ws in wb.worksheets:
        for name, dn in ws.defined_names.items():
            text[f"{ws.title}!{name}"] = dn.attr_text
            out[f"{ws.title.upper()}!{name.upper()}"] = dn.attr_text
    return out, text


def load(path: str | Path, *, name: str | None = None) -> Model:
    path = Path(path)
    raw = path.read_bytes()
    wb_f = openpyxl.load_workbook(path, data_only=False)
    wb_v = openpyxl.load_workbook(path, data_only=True)
    date_system = "1904" if wb_f.epoch.year == 1904 else "1900"
    diagnostics: list[dict] = []
    if path.suffix.lower() == ".xlsm" or getattr(wb_f, "vba_archive", None):
        diagnostics.append({"kind": "macros", "message": "El libro contiene macros VBA: no se ejecutan; solo se compilan las fórmulas."})
    names_raw, names_text = _resolve_names(wb_f)
    cells: dict[Key, Cell] = {}
    sheets: dict[str, tuple[int, int]] = {}
    options: dict[Key, list] = {}
    x14 = _x14_list_validations(path)
    for ws in wb_f.worksheets:
        sheets[ws.title] = (ws.max_row, ws.max_column)
        vs = wb_v[ws.title]
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if v is None:
                    continue
                key = (ws.title, c.row, c.column)
                cached = _plain(vs.cell(c.row, c.column).value, date_system)
                is_formula = c.data_type == "f"
                if isinstance(v, ArrayFormula):
                    if v.ref and ":" in v.ref and v.ref.split(":")[0] != v.ref.split(":")[1]:
                        cells[key] = Cell(key, formula=v.text, value=cached, frozen="fórmula matricial de varias celdas", number_format=c.number_format)
                        continue
                    v = v.text
                    is_formula = True
                if is_formula and isinstance(v, str) and v.startswith("=") and len(v) > 1:
                    cells[key] = Cell(key, formula=v, value=cached, number_format=c.number_format)
                elif not isinstance(v, (str, int, float, bool, dt.datetime, dt.date, dt.time)):
                    cells[key] = Cell(key, formula=str(v), value=cached, frozen=f"tipo de fórmula no soportado ({type(v).__name__})")
                else:
                    cells[key] = Cell(key, value=_plain(v, date_system), number_format=c.number_format)
        rules = [(dv.formula1, str(dv.sqref)) for dv in ws.data_validations.dataValidation if dv.type == "list" and dv.formula1]
        rules += x14.get(ws.title, [])
        for formula1, sqref in rules:
            if formula1:
                opts = _validation_options(formula1, ws.title, wb_v)
                if opts:
                    for rng in sqref.split():
                        node = parse_reference(rng, ws.title)
                        if node and node[0] == "ref":
                            options[(node[1], node[2], node[3])] = opts
                        elif node and node[0] == "range":
                            for r in range(node[2], min(node[4], ws.max_row) + 1):
                                for col in range(node[3], min(node[5], ws.max_column) + 1):
                                    options[(node[1], r, col)] = opts
    # parse formulas, resolve names
    for key, cell in cells.items():
        if not cell.formula or cell.frozen:
            continue
        ast = _resolve(parse(cell.formula, key[0]), key[0], names_raw, 0)
        cell.ast = ast
        problems = []
        for n in walk(ast):
            if n[0] == "bad":
                problems.append(n[1])
            elif n[0] == "call":
                if n[1] in rt.AS_OF:
                    cell.volatile = True
                elif n[1] in rt.VOLATILE:
                    problems.append(f"función volátil {n[1]}")
                elif n[1] not in rt.FUNCTIONS:
                    problems.append(f"función no soportada {n[1]}")
            elif n[0] == "range" or n[0] == "ref":
                if n[1] not in sheets:
                    problems.append(f"hoja inexistente {n[1]!r}")
        if problems:
            cell.frozen = "; ".join(sorted(set(problems)))
        elif not any(n[0] in ("ref", "range") for n in walk(ast)) and not cell.volatile:
            # =0,21 or =TRUE(): a constant typed as a formula is a parameter, not a calculation
            from .engine import literal_value
            lit = literal_value(ast, date_system=date_system)
            if lit is not None:
                cell.value, cell.formula, cell.ast = lit, None, None
                cell.literal_formula = True
    model = _graph(name or path.stem, hashlib.sha256(raw).hexdigest(), sheets, cells, options, names_text, diagnostics)
    model.date_system = date_system
    _label(model, wb_v)
    return model


def _x14_list_validations(path: Path) -> dict[str, list[tuple[str, str]]]:
    """Excel writes list validations that point to another sheet in an x14 extension openpyxl ignores."""
    import posixpath
    import zipfile
    import xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
          "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
          "x14": "http://schemas.microsoft.com/office/spreadsheetml/2009/9/main",
          "xm": "http://schemas.microsoft.com/office/excel/2006/main"}
    out: dict[str, list[tuple[str, str]]] = {}
    try:
        with zipfile.ZipFile(path) as z:
            wb = ET.fromstring(z.read("xl/workbook.xml"))
            rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            target = {r.get("Id"): r.get("Target") for r in rels.findall("rel:Relationship", ns)}
            for sh in wb.findall("m:sheets/m:sheet", ns):
                t = target.get(sh.get(f"{{{ns['r']}}}id"), "")
                part = posixpath.normpath(t.lstrip("/") if t.startswith("/") else posixpath.join("xl", t))
                if part not in z.namelist():
                    continue
                root = ET.fromstring(z.read(part))
                for dv in root.iter(f"{{{ns['x14']}}}dataValidation"):
                    if dv.get("type") != "list":
                        continue
                    f = dv.find("x14:formula1/xm:f", ns)
                    ref = dv.find("xm:sqref", ns)
                    if f is not None and ref is not None and f.text and ref.text:
                        out.setdefault(sh.get("name"), []).append((f.text, ref.text))
    except (KeyError, zipfile.BadZipFile, ET.ParseError):
        return {}
    return out


def _validation_options(formula1: str, sheet: str, wb_v) -> list | None:
    date_system = "1904" if wb_v.epoch.year == 1904 else "1900"
    f = formula1.strip()
    if f.startswith('"') and f.endswith('"'):
        return [_plain(x, date_system) for x in f[1:-1].split(",")]
    node = parse_reference(f.lstrip("="), sheet)
    if node and node[0] in ("ref", "range") and node[1] in wb_v.sheetnames:
        ws = wb_v[node[1]]
        if node[0] == "ref":
            return [_plain(ws.cell(node[2], node[3]).value, date_system)]
        vals = []
        for r in range(node[2], min(node[4], ws.max_row) + 1):
            for c in range(node[3], min(node[5], ws.max_column) + 1):
                v = ws.cell(r, c).value
                if v is not None:
                    vals.append(_plain(v, date_system))
        return vals
    return None


def _resolve(node, sheet, names, depth):
    kind = node[0]
    if kind == "name":
        text = node[1]
        if "[" in text:
            return ("bad", f"referencia estructurada o externa {text}")
        target = names.get(f"{sheet.upper()}!{text.upper()}") or names.get(text.upper())
        if target is None or depth > 8:
            return ("bad", f"nombre no definido {text}")
        sub = parse("=" + target, sheet)
        return _resolve(sub, sheet, names, depth + 1)
    if kind == "call":
        return ("call", node[1], [_resolve(a, sheet, names, depth) for a in node[2]])
    if kind in ("un",):
        return ("un", node[1], _resolve(node[2], sheet, names, depth))
    if kind == "pct":
        return ("pct", _resolve(node[1], sheet, names, depth))
    if kind == "bin":
        return ("bin", node[1], _resolve(node[2], sheet, names, depth), _resolve(node[3], sheet, names, depth))
    return node


def _refs(ast):
    for n in walk(ast):
        if n[0] in ("ref", "range"):
            yield n


def _graph(name, sha, sheets, cells, options, names_text, diagnostics) -> Model:
    formula_keys = [k for k, c in cells.items() if c.formula]
    index: dict[tuple, list[int]] = {}  # (sheet, col) -> sorted rows of non-empty cells
    for k in cells:
        index.setdefault((k[0], k[2]), []).append(k[1])
    for rows in index.values():
        rows.sort()
    cols_by_sheet: dict[str, list[int]] = {}
    for sh, col in index:
        cols_by_sheet.setdefault(sh, []).append(col)

    def in_range(sh, r1, c1, r2, c2):
        from bisect import bisect_left, bisect_right
        for col in cols_by_sheet.get(sh, ()):
            if c1 <= col <= c2:
                rows = index[(sh, col)]
                for r in rows[bisect_left(rows, r1):bisect_right(rows, r2)]:
                    yield (sh, r, col)
    deps: dict[Key, set] = {}
    read_direct: set = set()
    read_range: set = set()
    for k in formula_keys:
        c = cells[k]
        d = set()
        if c.ast is not None and not c.frozen:
            for n in _refs(c.ast):
                if n[0] == "ref":
                    key = (n[1], n[2], n[3])
                    d.add(key)
                    read_direct.add(key)
                else:
                    for vk in in_range(*n[1:]):
                        if cells[vk].formula:
                            d.add(vk)
                        else:
                            read_range.add(vk)
        deps[k] = {x for x in d if x in cells and cells[x].formula}
    order, cycles = _toposort(formula_keys, deps)
    for cyc in cycles:
        for k in cyc:
            if not cells[k].frozen:
                cells[k].frozen = "referencia circular"
    if cycles:
        diagnostics.append({"kind": "circular", "message": f"{len(cycles)} ciclo(s) de referencias: el cálculo iterativo no se ejecuta; esas celdas quedan congeladas."})
    readers = set()
    for k in formula_keys:
        readers |= deps[k]
    parameters = sorted((k for k in read_direct if k in cells and not cells[k].formula), key=_sort_key)
    tables = sorted((k for k in read_range - read_direct if not cells[k].formula), key=_sort_key)
    results = sorted((k for k in formula_keys if k not in readers), key=_sort_key)
    return Model(name=name, sha256=sha, sheets=sheets, cells=cells, order=order, deps=deps, parameters=parameters,
                 tables=tables, results=results, labels={}, options=options, names=names_text, diagnostics=diagnostics,
                 cycles=cycles)


def _sort_key(k):
    return (k[0], k[1], k[2])


def _toposort(keys, deps):
    """Iterative DFS; returns order and strongly connected cycles (Tarjan)."""
    index, low, onstack, stack, sccs = {}, {}, set(), [], []
    counter = [0]
    for root in keys:
        if root in index:
            continue
        work = [(root, iter(sorted(deps[root], key=_sort_key)))]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        onstack.add(root)
        while work:
            node, it = work[-1]
            advanced = False
            for nxt in it:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter[0]
                    counter[0] += 1
                    stack.append(nxt)
                    onstack.add(nxt)
                    work.append((nxt, iter(sorted(deps.get(nxt, ()), key=_sort_key))))
                    advanced = True
                    break
                if nxt in onstack:
                    low[node] = min(low[node], index[nxt])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                comp = []
                while True:
                    x = stack.pop()
                    onstack.discard(x)
                    comp.append(x)
                    if x == node:
                        break
                sccs.append(comp)
    # Tarjan emits each SCC after every SCC it reaches: dependencies come first
    cycles = [sorted(c, key=_sort_key) for c in sccs if len(c) > 1 or c[0] in deps.get(c[0], ())]
    members = {k for c in cycles for k in c}
    order = [k for c in sccs for k in c if k not in members]
    return order, cycles


def _label(model: Model, wb_v) -> None:
    """Nearest text to the left (same row) or above (same column) as a human label."""
    for key in list(model.parameters) + list(model.order):
        sheet, r, c = key
        label = None
        for cc in range(c - 1, max(0, c - 6), -1):
            v = model.cells.get((sheet, r, cc))
            if v and not v.formula and isinstance(v.value, str) and v.value.strip():
                label = v.value.strip()
                break
        if label is None:
            for rr in range(r - 1, max(0, r - 4), -1):
                v = model.cells.get((sheet, rr, c))
                if v and not v.formula and isinstance(v.value, str) and v.value.strip():
                    label = v.value.strip()
                    break
        if label:
            model.labels[key] = label
    for name, target in model.names.items():
        node = parse_reference(target, "")
        if node and node[0] == "ref" and (node[1], node[2], node[3]) in model.cells:
            model.labels[(node[1], node[2], node[3])] = name


# --- code generation ------------------------------------------------------

class _Gen:
    def __init__(self):
        self.consts: list = []

    def expr(self, n) -> str:
        k = n[0]
        if k == "num":
            return repr(float(n[1]))
        if k == "str":
            return repr(n[1])
        if k == "bool":
            return "True" if n[1] else "False"
        if k == "err":
            return f"raise_(ERRORS.get({n[1]!r}) or XlError({n[1]!r}))"
        if k == "missing":
            return "0.0"
        if k == "ref":
            return f"V.get({(n[1], n[2], n[3])!r})"
        if k == "range":
            return f"R({n[1]!r}, {n[2]}, {n[3]}, {n[4]}, {n[5]})"
        if k == "array":
            rows = ", ".join("[" + ", ".join(self.literal(x) for x in row) + "]" for row in n[1])
            return f"Area([{rows}])"
        if k == "un":
            return f"op_neg({self.arg(n[2])})"
        if k == "pct":
            return f"op_pct({self.arg(n[1])})"
        if k == "bin":
            return f"{_OPS[n[1]]}({self.arg(n[2])}, {self.arg(n[3])})"
        if k == "call":
            return self.call(n[1], n[2])
        if k == "bad":
            return f"unsupported({n[1]!r})"
        if k == "name":
            return "raise_(NAME)"
        raise ValueError(f"unknown node {k}")

    def literal(self, n) -> str:
        if n[0] == "err":
            return f"ERRORS.get({n[1]!r}) or XlError({n[1]!r})"
        return self.expr(n)

    def arg(self, n) -> str:
        """Operand that may raise: evaluate it to a value (errors included) so the
        caller checks its operands in order, as Excel does."""
        if n[0] in ("num", "str", "bool", "missing", "ref", "range", "array"):
            return self.expr(n)
        return f"guard({self.lam(n)})"

    def lam(self, n) -> str:
        return f"(lambda: {self.expr(n)})"

    def call(self, name, args) -> str:
        if name == "IF":
            cond = self.expr(args[0]) if args else "0.0"
            a = self.lam(args[1]) if len(args) > 1 else "(lambda: True)"
            b = self.lam(args[2]) if len(args) > 2 else "(lambda: False)"
            return f"lazy_if({cond}, {a}, {b})"
        if name in ("IFERROR", "IFNA"):
            if len(args) != 2:
                return "raise_(VALUE)"
            return f"lazy_iferror({self.lam(args[0])}, {self.lam(args[1])}, {name == 'IFNA'})"
        if name == "CHOOSE":
            return f"lazy_choose({self.expr(args[0])}, [{', '.join(self.lam(a) for a in args[1:])}])"
        if name == "IFS":
            pairs = ", ".join(f"({self.lam(args[i])}, {self.lam(args[i + 1])})" for i in range(0, len(args) - 1, 2))
            return f"lazy_ifs([{pairs}])"
        if name == "TODAY":
            return "C.today()"
        if name == "NOW":
            return "C.now()"
        if name in rt.ERROR_AWARE:
            return f"call({name!r}, [{', '.join(f'guard({self.lam(a)})' for a in args)}])"
        return f"call({name!r}, [{', '.join(self.arg(a) for a in args)}])"


def generate(model: Model) -> str:
    """Python source of the model's calculation (expects the runtime names in scope)."""
    g = _Gen()
    lines = ["# Generated by Nightingale's Hoard from workbook " + repr(model.name) + " (sha256 " + model.sha256[:12] + ")",
             f"DATE_SYSTEM = {model.date_system!r}",
             "CELLS = ["]
    for k in model.order:
        c = model.cells[k]
        if c.frozen:
            lines.append(f"    ({k!r}, None),  # {addr(k)} congelada: {c.frozen}")
        else:
            lines.append(f"    ({k!r}, lambda V, R, C: {g.expr(c.ast)}),  # {addr(k)} {c.formula}")
    for cyc in model.cycles:
        for k in cyc:
            lines.append(f"    ({k!r}, None),  # {addr(k)} referencia circular")
    lines.append("]")
    return "\n".join(lines) + "\n"


ENGINE = '''
class _Context:
    def __init__(self, as_of):
        import datetime as _d
        self.moment = as_of or _d.datetime.now()

    def today(self):
        import math as _m
        return float(_m.floor(to_serial(self.moment)))

    def now(self):
        return to_serial(self.moment)


def evaluate(values, as_of=None):
    """values: {(sheet, row, col): value} for every constant (and frozen) cell, already overridden.
    Returns the dict completed with every formula result."""
    V = dict(values)
    bounds = SHEETS

    def R(sheet, r1, c1, r2, c2):
        mr, mc = bounds.get(sheet, (0, 0))
        r2, c2 = min(r2, max(mr, r1)), min(c2, max(mc, c1))
        return Area([[V.get((sheet, r, c)) for c in range(c1, c2 + 1)] for r in range(r1, r2 + 1)])

    with date_context(DATE_SYSTEM):
        C = _Context(as_of)
        for key, fn in CELLS:
            if fn is None:
                continue
            V[key] = normalise(guard(lambda: fn(V, R, C)))
    return V
'''
