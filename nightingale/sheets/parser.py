"""Excel formula text → small AST (plain tuples, JSON-serialisable as lists).

Nodes::

    ("num", x) ("str", s) ("bool", b) ("err", code) ("missing",)
    ("ref", sheet, row, col)  ("range", sheet, r1, c1, r2, c2)
    ("name", text)            # defined name or structured reference, resolved later
    ("array", [[node, ...], ...])
    ("call", NAME, [args])    ("un", "-", x)  ("pct", x)  ("bin", op, a, b)
    ("bad", reason)           # something this compiler does not handle
"""
from __future__ import annotations

import re

from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.utils.cell import column_index_from_string

MAX_ROW, MAX_COL = 1048576, 16384
_INFIX_BP = {"=": 1, "<>": 1, "<": 1, ">": 1, "<=": 1, ">=": 1, "&": 2, "+": 3, "-": 3, "*": 4, "/": 4, "^": 5}
_PREFIX_BP = 7
_POSTFIX_BP = 6

_CELL = r"\$?([A-Za-z]{1,3})\$?(\d+)"
_REF_RE = re.compile(
    r"^(?:(?P<sheet>'(?:[^']|'')+'|[^'!:\[\]]+)!)?"
    r"(?:(?P<c1>\$?[A-Za-z]{1,3}\$?\d+)(?::(?P<c2>\$?[A-Za-z]{1,3}\$?\d+))?"
    r"|(?P<col1>\$?[A-Za-z]{1,3}):(?P<col2>\$?[A-Za-z]{1,3})"
    r"|(?P<row1>\$?\d+):(?P<row2>\$?\d+))$"
)


class FormulaError(ValueError):
    pass


def _cell(text):
    m = re.fullmatch(_CELL, text)
    col = column_index_from_string(m.group(1).upper())
    row = int(m.group(2))
    if col > MAX_COL or row > MAX_ROW:
        raise FormulaError(f"reference out of range: {text}")
    return row, col


def parse_reference(text: str, current_sheet: str):
    """A1-style reference (optionally sheet-qualified) → ref/range node, or None if it is not one."""
    m = _REF_RE.match(text.strip())
    if not m:
        return None
    sheet = m.group("sheet")
    if sheet:
        if sheet.startswith("["):
            return ("bad", "external workbook reference " + text)
        sheet = sheet[1:-1].replace("''", "'") if sheet.startswith("'") else sheet
        if sheet.startswith("[") or re.match(r"^\[\d+\]", sheet):
            return ("bad", "external workbook reference " + text)
    else:
        sheet = current_sheet
    if m.group("c1"):
        r1, c1 = _cell(m.group("c1"))
        if not m.group("c2"):
            return ("ref", sheet, r1, c1)
        r2, c2 = _cell(m.group("c2"))
    elif m.group("col1"):
        c1 = column_index_from_string(m.group("col1").replace("$", "").upper())
        c2 = column_index_from_string(m.group("col2").replace("$", "").upper())
        r1, r2 = 1, MAX_ROW
    else:
        r1, r2 = int(m.group("row1").replace("$", "")), int(m.group("row2").replace("$", ""))
        c1, c2 = 1, MAX_COL
    r1, r2 = sorted((r1, r2))
    c1, c2 = sorted((c1, c2))
    return ("range", sheet, r1, c1, r2, c2)


def _literal(tok: Token, sheet: str):
    v, sub = tok.value, tok.subtype
    if sub == Token.NUMBER:
        return ("num", float(v))
    if sub == Token.TEXT:
        return ("str", v[1:-1].replace('""', '"'))
    if sub == Token.LOGICAL:
        return ("bool", v.upper() == "TRUE")
    if sub == Token.ERROR:
        return ("err", v.upper())
    ref = parse_reference(v, sheet)
    if ref is not None:
        return ref
    return ("name", v)


class _Parser:
    def __init__(self, tokens, sheet):
        self.toks = [t for t in tokens if t.type != Token.WSPACE]
        self.ws = any(t.type == Token.WSPACE for t in tokens)
        self.i = 0
        self.sheet = sheet

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def take(self):
        t = self.peek()
        self.i += 1
        return t

    def expr(self, min_bp=0):
        t = self.take()
        if t is None:
            raise FormulaError("unexpected end of formula")
        if t.type == Token.OP_PRE:
            x = self.expr(_PREFIX_BP)
            left = ("un", "-", x) if t.value == "-" else x
        elif t.type == Token.OPERAND:
            left = _literal(t, self.sheet)
        elif t.type == Token.FUNC and t.subtype == Token.OPEN:
            left = self.call(t.value[:-1])
        elif t.type == Token.PAREN and t.subtype == Token.OPEN:
            left = self.expr(0)
            close = self.take()
            if close is None or close.type != Token.PAREN:
                raise FormulaError("missing )")
        elif t.type == Token.ARRAY and t.subtype == Token.OPEN:
            left = self.array()
        else:
            raise FormulaError(f"unexpected {t.value!r}")
        while True:
            t = self.peek()
            if t is None:
                break
            if t.type == Token.OP_POST:
                if _POSTFIX_BP < min_bp:
                    break
                self.take()
                left = ("pct", left)
                continue
            if t.type == Token.OP_IN:
                if t.value == ":":
                    return ("bad", "range operator between expressions")
                bp = _INFIX_BP.get(t.value)
                if bp is None:
                    raise FormulaError(f"unknown operator {t.value!r}")
                if bp <= min_bp:
                    break
                self.take()
                right = self.expr(bp)
                left = ("bin", t.value, left, right)
                continue
            break
        return left

    def call(self, name):
        if ":" in name or "!" in name:
            self.skip_call()
            return ("bad", "range operator between expressions")
        name = name.upper()
        for prefix in ("_XLFN._XLWS.", "_XLFN.", "_XLWS."):
            if name.startswith(prefix):
                name = name[len(prefix):]
        args = []
        t = self.peek()
        if t is not None and t.type == Token.FUNC and t.subtype == Token.CLOSE:
            self.take()
            return ("call", name, args)
        while True:
            t = self.peek()
            if t is not None and (t.type == Token.SEP and t.subtype == Token.ARG or t.type == Token.FUNC and t.subtype == Token.CLOSE):
                args.append(("missing",))
            else:
                args.append(self.expr(0))
            t = self.take()
            if t is None:
                raise FormulaError(f"missing ) after {name}")
            if t.type == Token.FUNC and t.subtype == Token.CLOSE:
                return ("call", name, args)
            if not (t.type == Token.SEP and t.subtype == Token.ARG):
                raise FormulaError(f"unexpected {t.value!r} in {name}")

    def skip_call(self):
        depth = 1
        while depth:
            t = self.take()
            if t is None:
                return
            if t.type in (Token.FUNC, Token.PAREN):
                depth += 1 if t.subtype == Token.OPEN else -1

    def array(self):
        rows, row = [], []
        while True:
            t = self.take()
            if t is None:
                raise FormulaError("unterminated array constant")
            if t.type == Token.ARRAY and t.subtype == Token.CLOSE:
                rows.append(row)
                break
            if t.type == Token.SEP:
                if t.subtype == Token.ROW:
                    rows.append(row)
                    row = []
                continue
            if t.type == Token.OP_PRE and t.value == "-":
                n = self.take()
                row.append(("num", -float(n.value)))
                continue
            row.append(_literal(t, self.sheet))
        if len({len(r) for r in rows}) != 1:
            raise FormulaError("ragged array constant")
        return ("array", rows)


def parse(formula: str, sheet: str):
    """Parse ``=...`` text. Returns an AST node; syntax problems become ``("bad", reason)``."""
    try:
        tok = Tokenizer(formula)
        p = _Parser(tok.items, sheet)
        items = tok.items
        for k, t in enumerate(items):
            if t.type == Token.WSPACE and 0 < k < len(items) - 1:
                a, b = items[k - 1], items[k + 1]
                left = a.type == Token.OPERAND and a.subtype == Token.RANGE or a.subtype == Token.CLOSE
                right = b.type == Token.OPERAND and b.subtype == Token.RANGE or (b.subtype == Token.OPEN and b.type != Token.ARRAY)
                if left and right:
                    return ("bad", "intersection operator (space between references)")
        node = p.expr(0)
        if p.peek() is not None:
            raise FormulaError(f"unexpected {p.peek().value!r}")
        return node
    except FormulaError as e:
        return ("bad", str(e))
    except Exception as e:  # tokenizer errors
        return ("bad", f"cannot parse formula: {e}")


def walk(node):
    yield node
    kind = node[0]
    if kind == "call":
        for a in node[2]:
            yield from walk(a)
    elif kind in ("un", "pct"):
        yield from walk(node[-1])
    elif kind == "bin":
        yield from walk(node[2])
        yield from walk(node[3])
    elif kind == "array":
        for row in node[1]:
            for x in row:
                yield from walk(x)
