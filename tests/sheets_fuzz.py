"""Random formula workbooks, recalculated by LibreOffice, compared with the compiled model."""
import random
import openpyxl

NUMS = [0, 1, 2, -3, 2.5, 0.1, 1e-7, 1234.5678, -0.5, 7]
TEXTS = ["a", "B", "abc", "", "10", "x*", "Ñu", "TRUE"]
FUNCS1 = ["ABS", "INT", "SIGN", "SQRT", "LN", "EXP", "LEN", "UPPER", "LOWER", "TRIM", "ISBLANK", "ISNUMBER", "ISTEXT", "ISERROR", "NOT", "VALUE"]
FUNCS2 = ["ROUND", "ROUNDUP", "ROUNDDOWN", "MOD", "POWER", "LEFT", "RIGHT", "LOG", "MROUND", "CEILING", "FLOOR", "IFERROR", "_xlfn.IFNA"]
AGG = ["SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTA", "COUNTBLANK", "PRODUCT", "SUMPRODUCT"]


def atom(rng, depth):
    r = rng.random()
    if depth > 2 or r < 0.35:
        k = rng.random()
        if k < 0.55:
            return f"{rng.choice('ABC')}{rng.randint(1, 8)}"
        if k < 0.8:
            return repr(rng.choice(NUMS))
        if k < 0.95:
            return '"' + rng.choice(TEXTS).replace('"', '""') + '"'
        return rng.choice(["TRUE", "FALSE"])
    return expr(rng, depth + 1)


def expr(rng, depth=0):
    r = rng.random()
    if r < 0.3:
        op = rng.choice(["+", "-", "*", "/", "^", "&", "=", "<>", "<", ">", "<=", ">="])
        return f"({atom(rng, depth)}{op}{atom(rng, depth)})"
    if r < 0.45:
        return f"{rng.choice(FUNCS1)}({atom(rng, depth)})"
    if r < 0.6:
        f = rng.choice(FUNCS2)
        return f"{f}({atom(rng, depth)},{atom(rng, depth)})"
    if r < 0.72:
        f = rng.choice(AGG)
        a, b = sorted(rng.sample(range(1, 9), 2))
        col = rng.choice("ABC")
        if f == "SUMPRODUCT":
            return f"SUMPRODUCT(A{a}:A{b},B{a}:B{b})"
        return f"{f}({col}{a}:{col}{b})"
    if r < 0.8:
        return f"IF({atom(rng, depth)},{atom(rng, depth)},{atom(rng, depth)})"
    if r < 0.88:
        crit = rng.choice(['">1"', '"<=2"', '"a"', '"<>a"', '"*b*"', '"="', '2', '"10"', '">a"'])
        f = rng.choice(["COUNTIF", "SUMIF", "AVERAGEIF"])
        if f == "COUNTIF":
            return f"COUNTIF(A1:C8,{crit})"
        return f"{f}(A1:A8,{crit},B1:B8)"
    if r < 0.94:
        return f"VLOOKUP({atom(rng, depth)},A1:C8,{rng.randint(1, 3)},{rng.choice(['FALSE', 'TRUE'])})"
    return f"MATCH({atom(rng, depth)},A1:A8,{rng.choice([0, 1, -1])})"


def build(path, seed, n=80):
    rng = random.Random(seed)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "D"
    for r in range(1, 9):
        for c in range(1, 4):
            k = rng.random()
            if k < 0.15:
                continue
            ws.cell(r, c, rng.choice(NUMS) if k < 0.7 else (rng.choice(TEXTS) or None) if k < 0.92 else rng.choice([True, False]))
    for i in range(n):
        ws.cell(i + 1, 6, "=" + expr(rng))
    wb.save(path)
