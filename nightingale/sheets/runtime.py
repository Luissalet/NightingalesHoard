"""Excel value semantics and the supported function library.

Self-contained (standard library only): the Python export embeds this file
verbatim, so a compiled workbook runs without Nightingale.

Values: ``float`` numbers, ``str`` text, ``bool``, ``None`` for an empty cell,
:class:`XlError` for ``#DIV/0!`` and friends. A range is a :class:`Area`
(rows of values).
"""
from __future__ import annotations

import datetime as _dt
import math
import re


class XlError(Exception):
    """An Excel error value. Raised to propagate; also stored as a value."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code

    def __eq__(self, other):
        return isinstance(other, XlError) and other.code == self.code

    def __hash__(self):
        return hash(self.code)

    def __repr__(self):
        return self.code


DIV0, NA, VALUE, REF, NAME, NUM = (XlError(c) for c in ("#DIV/0!", "#N/A", "#VALUE!", "#REF!", "#NAME?", "#NUM!"))
ERRORS = {e.code: e for e in (DIV0, NA, VALUE, REF, NAME, NUM, XlError("#NULL!"))}


class Area:
    """A rectangular block of values (a range reference)."""

    __slots__ = ("rows",)

    def __init__(self, rows):
        self.rows = rows

    @property
    def height(self):
        return len(self.rows)

    @property
    def width(self):
        return len(self.rows[0]) if self.rows else 0

    def flat(self):
        for row in self.rows:
            yield from row


EPOCH = _dt.datetime(1899, 12, 30)


def to_serial(value):
    if isinstance(value, _dt.datetime):
        return (value - EPOCH).total_seconds() / 86400.0
    if isinstance(value, _dt.date):
        return float((value - EPOCH.date()).days)
    if isinstance(value, _dt.time):
        return (value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6) / 86400.0
    return value


def from_serial(n):
    return EPOCH + _dt.timedelta(days=float(n))


# --- coercion -------------------------------------------------------------

def scalar(v):
    """Implicit intersection is not modelled: a 1x1 area becomes its value, larger ones are #VALUE!."""
    if isinstance(v, Area):
        if v.height == 1 and v.width == 1:
            return v.rows[0][0]
        raise VALUE
    return v


def check(v):
    if isinstance(v, XlError):
        raise v
    return v


def num(v):
    v = check(scalar(v))
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            raise VALUE
        try:
            if s.endswith("%"):
                return float(s[:-1].replace(",", "")) / 100.0
            return float(s.replace(",", "")) if re.fullmatch(r"[+-]?(\d{1,3}(,\d{3})+|\d*)(\.\d*)?([eE][+-]?\d+)?", s) else float(s)
        except ValueError:
            raise VALUE from None
    raise VALUE


def text(v):
    v = check(scalar(v))
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float) or isinstance(v, int):
        return fmt_general(float(v))
    return v


def boolean(v):
    v = check(scalar(v))
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        u = v.upper()
        if u == "TRUE":
            return True
        if u == "FALSE":
            return False
    raise VALUE


def fmt_general(x: float) -> str:
    """Number → text as Excel converts it (15 significant digits)."""
    if x == 0:
        return "0"
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    e = math.floor(math.log10(abs(x)))
    if e >= 15 or e <= -10:
        mant, exp = f"{x:.14e}".split("e")
        mant = mant.rstrip("0").rstrip(".")
        return f"{mant}E{exp[0]}{exp[1:].lstrip('0').zfill(2)}"
    digits = max(0, 14 - e)
    s = f"{x:.{digits}f}".rstrip("0").rstrip(".")
    return s


def finite(x: float) -> float:
    if math.isnan(x) or math.isinf(x):
        raise NUM
    return x


# --- operators ------------------------------------------------------------

def op_add(a, b): return finite(num(a) + num(b))
def op_sub(a, b): return finite(num(a) - num(b))
def op_mul(a, b): return finite(num(a) * num(b))


def op_div(a, b):
    n = num(a)
    d = num(b)
    if d == 0:
        raise DIV0
    return finite(n / d)


def op_pow(a, b):
    x, y = num(a), num(b)
    if x == 0 and y == 0:
        raise NUM
    if x == 0 and y < 0:
        raise DIV0
    try:
        r = x ** y
    except (OverflowError, ZeroDivisionError):
        raise NUM from None
    if isinstance(r, complex):
        raise NUM
    return finite(r)


def op_neg(a): return -num(a)
def op_pos(a): return scalar(a) if not isinstance(scalar(a), XlError) else check(scalar(a))
def op_pct(a): return num(a) / 100.0
def op_cat(a, b): return text(a) + text(b)


def _rank(v):
    if isinstance(v, bool):
        return 2
    if isinstance(v, str):
        return 1
    return 0


def compare(a, b):
    """Excel ordering: numbers < text < booleans; text case-insensitive; blank as 0/""/FALSE."""
    a, b = check(scalar(a)), check(scalar(b))
    if a is None:
        a = "" if isinstance(b, str) else (False if isinstance(b, bool) else 0.0)
    if b is None:
        b = "" if isinstance(a, str) else (False if isinstance(a, bool) else 0.0)
    ra, rb = _rank(a), _rank(b)
    if ra != rb:
        return -1 if ra < rb else 1
    if ra == 1:
        a, b = a.lower(), b.lower()
    else:
        a, b = float(a), float(b)
    return (a > b) - (a < b)


def op_eq(a, b): return compare(a, b) == 0
def op_ne(a, b): return compare(a, b) != 0
def op_lt(a, b): return compare(a, b) < 0
def op_le(a, b): return compare(a, b) <= 0
def op_gt(a, b): return compare(a, b) > 0
def op_ge(a, b): return compare(a, b) >= 0


# --- argument helpers -----------------------------------------------------

def _iter_args(args, *, area_text=False):
    """Values for aggregate functions: areas skip text/bool/blank; direct args are coerced."""
    for a in args:
        if isinstance(a, Area):
            for v in a.flat():
                if isinstance(v, XlError):
                    raise v
                if isinstance(v, bool) or v is None or isinstance(v, str):
                    continue
                yield float(v)
        else:
            yield num(a)


def _area(v):
    if isinstance(v, Area):
        return v
    return Area([[check(v)]])


# --- criteria (SUMIF & co.) ------------------------------------------------

def _wild(pattern):
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "~" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if c == "*" else "." if c == "?" else re.escape(c))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE | re.DOTALL)


def criterion(c):
    c = check(scalar(c))
    if c is None:
        c = ""
    if not isinstance(c, str):
        target = c
        return lambda v: v is not None and not isinstance(v, str) and _rank(v) == _rank(target) and compare(v, target) == 0
    m = re.match(r"^(<=|>=|<>|<|>|=)?(.*)$", c, re.DOTALL)
    op, rest = m.group(1) or "=", m.group(2)
    try:
        target = num(rest) if rest.strip() != "" else None
    except XlError:
        target = None
    if target is None and rest.upper() in ("TRUE", "FALSE"):
        target = rest.upper() == "TRUE"
    if op in ("=", "<>"):
        if rest == "":
            hit = lambda v: v is None or v == ""
        elif isinstance(target, bool):
            hit = lambda v: isinstance(v, bool) and v == target
        elif isinstance(target, float):
            low = rest.lower()
            hit = lambda v: (isinstance(v, str) and v.lower() == low) or (
                v is not None and not isinstance(v, (str, bool)) and float(v) == target)
        else:
            rx = _wild(rest)
            hit = lambda v: isinstance(v, str) and bool(rx.match(v))
        return hit if op == "=" else (lambda v: not hit(v))
    if target is None or isinstance(target, bool):
        t = rest.lower()
        cmp = {"<": lambda x: x < t, "<=": lambda x: x <= t, ">": lambda x: x > t, ">=": lambda x: x >= t}[op]
        return lambda v: isinstance(v, str) and cmp(v.lower())
    cmp = {"<": lambda x: x < target, "<=": lambda x: x <= target, ">": lambda x: x > target, ">=": lambda x: x >= target}[op]
    return lambda v: v is not None and not isinstance(v, (str, bool)) and not isinstance(v, XlError) and cmp(float(v))


def _cells_matching(pairs):
    """pairs: [(area, criterion)] of equal shape → boolean mask (flat)."""
    first = pairs[0][0]
    n = first.height * first.width
    mask = [True] * n
    for area, crit in pairs:
        if area.height != first.height or area.width != first.width:
            raise VALUE
        test = criterion(crit)
        for i, v in enumerate(area.flat()):
            if mask[i] and (isinstance(v, XlError) or not test(v)):
                mask[i] = False
    return mask


# --- functions ------------------------------------------------------------

def SUM(*a): return finite(math.fsum(_iter_args(a)))


def PRODUCT(*a):
    r, seen = 1.0, False
    for x in _iter_args(a):
        r *= x
        seen = True
    return finite(r) if seen else 0.0


def AVERAGE(*a):
    vals = list(_iter_args(a))
    if not vals:
        raise DIV0
    return math.fsum(vals) / len(vals)


def MIN(*a):
    vals = list(_iter_args(a))
    return min(vals) if vals else 0.0


def MAX(*a):
    vals = list(_iter_args(a))
    return max(vals) if vals else 0.0


def COUNT(*a):
    n = 0
    for x in a:
        if isinstance(x, Area):
            n += sum(1 for v in x.flat() if isinstance(v, (int, float)) and not isinstance(v, bool))
        else:
            try:
                num(x)
                n += 1
            except XlError:
                pass
    return float(n)


def COUNTA(*a):
    n = 0
    for x in a:
        if isinstance(x, Area):
            n += sum(1 for v in x.flat() if v is not None)
        elif x is not None:
            n += 1
    return float(n)


def COUNTBLANK(a):
    return float(sum(1 for v in _area(a).flat() if v is None or v == ""))


def SUMPRODUCT(*a):
    areas = [_area(x) for x in a]
    h, w = areas[0].height, areas[0].width
    if any(x.height != h or x.width != w for x in areas):
        raise VALUE
    total = 0.0
    cols = [list(x.flat()) for x in areas]
    for i in range(h * w):
        p = 1.0
        for c in cols:
            v = c[i]
            if isinstance(v, XlError):
                raise v
            p *= float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0
        total += p
    return finite(total)


def IF(cond, a=True, b=False):
    return a if boolean(cond) else b


def IFERROR(v, alt):
    return alt if isinstance(v, XlError) else v


def IFNA(v, alt):
    return alt if isinstance(v, XlError) and v.code == "#N/A" else v


def IFS(*a):
    if len(a) % 2:
        raise VALUE
    for i in range(0, len(a), 2):
        if boolean(a[i]):
            return a[i + 1]
    raise NA


def _logical(args):
    vals = []
    for x in args:
        if isinstance(x, Area):
            for v in x.flat():
                if isinstance(v, XlError):
                    raise v
                if isinstance(v, (bool, int, float)):
                    vals.append(bool(v))
        else:
            vals.append(boolean(x))
    if not vals:
        raise VALUE
    return vals


def AND(*a): return all(_logical(a))
def OR(*a): return any(_logical(a))
def XOR(*a): return sum(_logical(a)) % 2 == 1
def NOT(a): return not boolean(a)
def TRUE(): return True
def FALSE(): return False


def _scaled(x, d):
    """|x|·10^d rounded to 15 significant digits, or None when it is out of float range."""
    if d > 300:
        return None
    if d < -300:
        return 0.0
    y = abs(x) * 10.0 ** d
    if math.isinf(y):
        return None
    return float(f"{y:.15g}")


def _round_half_away(x, digits):
    d = int(num(digits)) if not isinstance(digits, int) else digits
    y = _scaled(x, d)
    if y is None:
        return x
    q = 10.0 ** d
    r = math.floor(y + 0.5)
    return math.copysign(r / q, x) if r else 0.0


def ROUND(x, d=0): return _round_half_away(num(x), int(num(d)))


def ROUNDUP(x, d=0):
    x, d = num(x), int(num(d))
    y = _scaled(x, d)
    if y is None:
        return x
    return math.copysign(math.ceil(y) / 10.0 ** d, x) if y else 0.0


def ROUNDDOWN(x, d=0):
    x, d = num(x), int(num(d))
    y = _scaled(x, d)
    if y is None:
        return x
    return math.copysign(math.floor(y) / 10.0 ** d, x) if y else 0.0


def TRUNC(x, d=0): return ROUNDDOWN(x, d)
def INT(x): return float(math.floor(num(x)))
def ABS(x): return abs(num(x))
def SIGN(x): x = num(x); return float((x > 0) - (x < 0))
def PI(): return math.pi


def MOD(n, d):
    n, d = num(n), num(d)
    if d == 0:
        raise DIV0
    return n - d * math.floor(n / d)


def POWER(a, b): return op_pow(a, b)


def SQRT(x):
    x = num(x)
    if x < 0:
        raise NUM
    return math.sqrt(x)


def EXP(x): return finite(math.exp(num(x)))


def LN(x):
    x = num(x)
    if x <= 0:
        raise NUM
    return math.log(x)


def LOG10(x): return LN(x) / math.log(10)


def LOG(x, base=10):
    n = num(x)
    b = num(base)
    if n <= 0 or b <= 0:
        raise NUM
    if b == 1:
        raise DIV0
    return math.log(n) / math.log(b)


def CEILING(x, sig=1):
    x, s = num(x), num(sig)
    if s == 0:
        return 0.0
    if x > 0 and s < 0:
        raise NUM
    return math.ceil(float(f"{x / s:.15g}")) * s


def FLOOR(x, sig=1):
    x, s = num(x), num(sig)
    if s == 0:
        raise DIV0
    if x > 0 and s < 0:
        raise NUM
    return math.floor(float(f"{x / s:.15g}")) * s


def _strict(v):
    """Analysis-ToolPak functions (MROUND…) refuse logical arguments."""
    if isinstance(scalar(v), bool):
        raise VALUE
    return num(v)


def MROUND(x, m):
    x, m = _strict(x), _strict(m)
    if m == 0:
        return 0.0
    if (x > 0) != (m > 0) and x != 0:
        raise NUM
    return _round_half_away(x / m, 0) * m


def CHOOSE(i, *opts):
    k = int(num(i))
    if k < 1 or k > len(opts):
        raise VALUE
    return opts[k - 1]


# lookups

def _match_exact(values, target):
    t = check(scalar(target))
    if isinstance(t, str) and any(ch in t for ch in "*?~"):
        rx = _wild(t)
        for i, v in enumerate(values):
            if isinstance(v, str) and rx.match(v):
                return i
        raise NA
    for i, v in enumerate(values):
        if v is None or isinstance(v, XlError):
            continue
        if _rank(v) == _rank(t) and compare(v, t) == 0:
            return i
    raise NA


def _comparable(v, t):
    return v is not None and not isinstance(v, XlError) and _rank(v) == _rank(t)


def _match_sorted(values, target, descending=False):
    """Approximate lookup with Excel's behaviour on data that is not sorted.

    Ascending: binary search; a probe on another type (or blank) moves forward to
    the next comparable value; an exact hit returns at once (198/200 Excel
    observations on unsorted mixed data). Descending: see below.
    """
    t = check(scalar(target))
    if descending:
        # fitted on 200 Excel observations (200/200): linear walk, skip other types
        # and blanks, an exact hit returns, stop at the first smaller value
        best = None
        for i, v in enumerate(values):
            if not _comparable(v, t):
                continue
            c = compare(v, t)
            if c == 0:
                return i
            if c < 0:
                break
            best = i
        if best is None:
            raise NA
        return best
    best = None
    lo, hi = 0, len(values) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        j = mid
        while j <= hi and not _comparable(values[j], t):
            j += 1
        if j > hi:
            hi = mid - 1
            continue
        c = compare(values[j], t)
        if c == 0:
            return j
        if c < 0:
            best = j
            lo = j + 1
        else:
            hi = mid - 1
    if best is None:
        raise NA
    return best


def MATCH(target, area, mtype=1):
    a = _area(area)
    if a.height != 1 and a.width != 1:
        raise NA
    values = list(a.flat())
    t = int(num(mtype))
    if t == 0:
        return float(_match_exact(values, target) + 1)
    return float(_match_sorted(values, target, descending=t < 0) + 1)


def INDEX(area, row=0, col=None):
    a = _area(area)
    r = int(num(row))
    c = int(num(col)) if col is not None else 0
    if col is None and a.height == 1:
        r, c = 1, r
    if r < 0 or c < 0 or r > a.height or c > a.width:
        raise REF
    if r == 0 and c == 0:
        return a
    if r == 0:
        return Area([[row_[c - 1]] for row_ in a.rows])
    if c == 0:
        if a.width == 1:
            return a.rows[r - 1][0]
        return Area([a.rows[r - 1]])
    return a.rows[r - 1][c - 1]


def VLOOKUP(target, area, col, approx=True):
    a = _area(area)
    c = int(num(col))
    if c < 1:
        raise VALUE
    if c > a.width:
        raise REF
    first = [row[0] for row in a.rows]
    i = _match_sorted(first, target) if boolean(approx) else _match_exact(first, target)
    return a.rows[i][c - 1]


def HLOOKUP(target, area, row, approx=True):
    a = _area(area)
    r = int(num(row))
    if r < 1:
        raise VALUE
    if r > a.height:
        raise REF
    first = list(a.rows[0])
    i = _match_sorted(first, target) if boolean(approx) else _match_exact(first, target)
    return a.rows[r - 1][i]


def XLOOKUP(target, look, ret, if_missing=None, mode=0):
    la, ra = _area(look), _area(ret)
    values = list(la.flat())
    if int(num(mode)) != 0:
        raise VALUE
    try:
        i = _match_exact(values, target)
    except XlError:
        if if_missing is not None:
            return if_missing
        raise
    if la.width == 1:
        if ra.height != la.height:
            raise VALUE
        return ra.rows[i][0] if ra.width == 1 else Area([ra.rows[i]])
    if ra.width != la.width:
        raise VALUE
    return ra.rows[0][i] if ra.height == 1 else Area([[row[i]] for row in ra.rows])


# conditional aggregates

def SUMIF(area, crit, sum_area=None):
    a = _area(area)
    s = _area(sum_area) if sum_area is not None else a
    if s.height != a.height or s.width != a.width:
        s = Area([row[: a.width] for row in s.rows[: a.height]])  # Excel resizes from the top-left corner
    mask = _cells_matching([(a, crit)])
    total = 0.0
    for m, v in zip(mask, s.flat()):
        if m:
            if isinstance(v, XlError):
                raise v
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                total += v
    return total


def SUMIFS(sum_area, *pairs):
    if len(pairs) % 2:
        raise VALUE
    s = _area(sum_area)
    mask = _cells_matching([(_area(pairs[i]), pairs[i + 1]) for i in range(0, len(pairs), 2)])
    if len(mask) != s.height * s.width:
        raise VALUE
    total = 0.0
    for m, v in zip(mask, s.flat()):
        if m:
            if isinstance(v, XlError):
                raise v
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                total += v
    return total


def COUNTIF(area, crit): return float(sum(_cells_matching([(_area(area), crit)])))


def COUNTIFS(*pairs):
    if len(pairs) % 2 or not pairs:
        raise VALUE
    return float(sum(_cells_matching([(_area(pairs[i]), pairs[i + 1]) for i in range(0, len(pairs), 2)])))


def _ifs_values(target, pairs):
    t = _area(target)
    mask = _cells_matching([(_area(pairs[i]), pairs[i + 1]) for i in range(0, len(pairs), 2)])
    if len(mask) != t.height * t.width:
        raise VALUE
    out = []
    for m, v in zip(mask, t.flat()):
        if m:
            if isinstance(v, XlError):
                raise v
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(float(v))
    return out


def AVERAGEIF(area, crit, avg_area=None):
    vals = _ifs_values(avg_area if avg_area is not None else area, [area, crit])
    if not vals:
        raise DIV0
    return math.fsum(vals) / len(vals)


def AVERAGEIFS(target, *pairs):
    vals = _ifs_values(target, pairs)
    if not vals:
        raise DIV0
    return math.fsum(vals) / len(vals)


def MAXIFS(target, *pairs):
    vals = _ifs_values(target, pairs)
    return max(vals) if vals else 0.0


def MINIFS(target, *pairs):
    vals = _ifs_values(target, pairs)
    return min(vals) if vals else 0.0


# text

def CONCATENATE(*a): return "".join(text(x) for x in a)


def CONCAT(*a):
    out = []
    for x in a:
        if isinstance(x, Area):
            out.extend(text(v) for v in x.flat())
        else:
            out.append(text(x))
    return "".join(out)


def TEXTJOIN(delim, ignore_empty, *a):
    d, skip = text(delim), boolean(ignore_empty)
    parts = []
    for x in a:
        vals = list(x.flat()) if isinstance(x, Area) else [scalar(x)]
        for v in vals:
            s = text(v)
            if skip and s == "":
                continue
            parts.append(s)
    return d.join(parts)


def LEN(s): return float(len(text(s)))
def UPPER(s): return text(s).upper()
def LOWER(s): return text(s).lower()
def TRIM(s): return re.sub(" +", " ", text(s)).strip(" ")


def _count(n):
    x = num(n)
    if x < 0:
        raise VALUE
    return int(x)


def LEFT(s, n=1):
    t = text(s)
    return t[:_count(n)]


def RIGHT(s, n=1):
    t = text(s)
    k = _count(n)
    return t[len(t) - k:] if k else ""


def MID(s, start, n):
    t = text(s)
    a = num(start)
    if a < 1:
        raise VALUE
    a = int(a)
    k = _count(n)
    return t[a - 1:a - 1 + k]


def VALUE_(s):
    v = check(scalar(s))
    if isinstance(v, str):
        return num(v)
    if isinstance(v, bool):
        raise VALUE
    return num(v)


def TEXT(v, fmt):
    x = num(v)
    f = text(fmt)
    m = re.fullmatch(r"(#,##)?0(?:\.(0+))?(%?)", f)
    if not m:
        raise XlError("#UNSUPPORTED-FORMAT")
    if m.group(3):
        x *= 100
    digits = len(m.group(2) or "")
    r = _round_half_away(x, digits)
    s = f"{abs(r):,.{digits}f}" if m.group(1) else f"{abs(r):.{digits}f}"
    return ("-" if r < 0 else "") + s + m.group(3)


# information

def ISBLANK(v): return scalar(v) is None
def ISNUMBER(v): v = scalar(v); return isinstance(v, (int, float)) and not isinstance(v, bool)
def ISTEXT(v): return isinstance(scalar(v), str)
def ISLOGICAL(v): return isinstance(scalar(v), bool)
def ISERROR(v): return isinstance(v, XlError)
def ISERR(v): return isinstance(v, XlError) and v.code != "#N/A"
def ISNA(v): return isinstance(v, XlError) and v.code == "#N/A"
def NA_(): raise NA


# dates (1900 system; serial numbers)

def DATE(y, m, d):
    y, m, d = int(num(y)), int(num(m)), int(num(d))
    if y < 1900:
        y += 1900
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    base = _dt.date(y, m, 1)
    return float((base - EPOCH.date()).days + d - 1)


def _date(v):
    n = num(v)
    if n < 0:
        raise NUM
    return from_serial(n).date()


def YEAR(v): return float(_date(v).year)
def MONTH(v): return float(_date(v).month)
def DAY(v): return float(_date(v).day)


def EDATE(v, months):
    d = _date(v)
    k = int(num(months))
    m = d.month - 1 + k
    y, m = d.year + m // 12, m % 12 + 1
    import calendar
    day = min(d.day, calendar.monthrange(y, m)[1])
    return float((_dt.date(y, m, day) - EPOCH.date()).days)


def EOMONTH(v, months):
    import calendar
    d = _date(v)
    k = int(num(months))
    m = d.month - 1 + k
    y, m = d.year + m // 12, m % 12 + 1
    return float((_dt.date(y, m, calendar.monthrange(y, m)[1]) - EPOCH.date()).days)


def DAYS(end, start): return float(math.floor(num(end)) - math.floor(num(start)))


def WEEKDAY(v, kind=1):
    w = _date(v).isoweekday()  # Mon=1..Sun=7
    k = int(num(kind))
    if k == 1:
        return float(w % 7 + 1)
    if k == 2:
        return float(w)
    if k == 3:
        return float(w - 1)
    raise NUM


def _holidays(h):
    if h is None:
        return set()
    vals = h.flat() if isinstance(h, Area) else [scalar(h)]
    out = set()
    for v in vals:
        if isinstance(v, XlError):
            raise v
        if v is not None:
            out.add(int(math.floor(num(v))))
    return out


def WORKDAY(start, days, holidays=None):
    d = int(math.floor(num(start)))
    n = int(num(days))
    hol = _holidays(holidays)
    step = 1 if n >= 0 else -1
    while n:
        d += step
        if from_serial(d).isoweekday() < 6 and d not in hol:
            n -= step
    return float(d)


def NETWORKDAYS(start, end, holidays=None):
    a, b = int(math.floor(num(start))), int(math.floor(num(end)))
    hol = _holidays(holidays)
    sign = 1
    if a > b:
        a, b, sign = b, a, -1
    count = sum(1 for d in range(a, b + 1) if from_serial(d).isoweekday() < 6 and d not in hol)
    return float(sign * count)


FUNCTIONS = {
    name: fn for name, fn in {
        "SUM": SUM, "PRODUCT": PRODUCT, "AVERAGE": AVERAGE, "MIN": MIN, "MAX": MAX, "COUNT": COUNT, "COUNTA": COUNTA,
        "COUNTBLANK": COUNTBLANK, "SUMPRODUCT": SUMPRODUCT, "IF": IF, "IFERROR": IFERROR, "IFNA": IFNA, "IFS": IFS,
        "AND": AND, "OR": OR, "XOR": XOR, "NOT": NOT, "TRUE": TRUE, "FALSE": FALSE, "ROUND": ROUND, "ROUNDUP": ROUNDUP,
        "ROUNDDOWN": ROUNDDOWN, "TRUNC": TRUNC, "INT": INT, "ABS": ABS, "SIGN": SIGN, "PI": PI, "MOD": MOD,
        "POWER": POWER, "SQRT": SQRT, "EXP": EXP, "LN": LN, "LOG": LOG, "LOG10": LOG10, "CEILING": CEILING,
        "FLOOR": FLOOR, "MROUND": MROUND, "CHOOSE": CHOOSE, "MATCH": MATCH, "INDEX": INDEX, "VLOOKUP": VLOOKUP,
        "HLOOKUP": HLOOKUP, "XLOOKUP": XLOOKUP, "SUMIF": SUMIF, "SUMIFS": SUMIFS, "COUNTIF": COUNTIF,
        "COUNTIFS": COUNTIFS, "AVERAGEIF": AVERAGEIF, "AVERAGEIFS": AVERAGEIFS, "MAXIFS": MAXIFS, "MINIFS": MINIFS,
        "CONCATENATE": CONCATENATE, "CONCAT": CONCAT, "TEXTJOIN": TEXTJOIN, "LEN": LEN, "UPPER": UPPER,
        "LOWER": LOWER, "TRIM": TRIM, "LEFT": LEFT, "RIGHT": RIGHT, "MID": MID, "VALUE": VALUE_, "TEXT": TEXT,
        "ISBLANK": ISBLANK, "ISNUMBER": ISNUMBER, "ISTEXT": ISTEXT, "ISLOGICAL": ISLOGICAL, "ISERROR": ISERROR,
        "ISERR": ISERR, "ISNA": ISNA, "NA": NA_, "DATE": DATE, "YEAR": YEAR, "MONTH": MONTH, "DAY": DAY,
        "EDATE": EDATE, "EOMONTH": EOMONTH, "DAYS": DAYS, "WEEKDAY": WEEKDAY,
        "WORKDAY": WORKDAY, "NETWORKDAYS": NETWORKDAYS,
    }.items()
}
#: functions that receive error arguments instead of propagating them
ERROR_AWARE = {"IFERROR", "IFNA", "ISERROR", "ISERR", "ISNA", "ISBLANK", "ISNUMBER", "ISTEXT", "ISLOGICAL"}
#: functions whose unused branches must not be evaluated (lazy)
LAZY = {"IF", "IFS", "CHOOSE", "IFERROR", "IFNA"}
#: never deterministic; reported, evaluated only with an explicit as-of value
VOLATILE = {"NOW", "TODAY", "RAND", "RANDBETWEEN", "OFFSET", "INDIRECT", "CELL", "INFO"}
#: volatile functions evaluated against the explicit as-of moment of a run
AS_OF = {"TODAY", "NOW"}


def lazy_if(cond, a, b):
    return a() if boolean(cond) else b()


def lazy_ifs(pairs):
    for c, v in pairs:
        if boolean(c()):
            return v()
    raise NA


def lazy_choose(i, opts):
    k = int(num(i))
    if k < 1 or k > len(opts):
        raise VALUE
    return opts[k - 1]()


def lazy_iferror(v, alt, only_na=False):
    r = guard(v)
    if isinstance(r, XlError) and (not only_na or r.code == "#N/A"):
        return alt()
    return r


def raise_(e):
    raise e


def unsupported(name):
    raise XlError("#UNSUPPORTED:" + name)


def call(name, args):
    """Call a function; scalar error arguments propagate except for error-aware functions."""
    fn = FUNCTIONS.get(name)
    if fn is None:
        raise NAME
    try:
        return fn(*args)
    except XlError:
        raise
    except TypeError:
        raise VALUE from None
    except (ValueError, OverflowError, ZeroDivisionError):
        raise NUM from None


def guard(thunk):
    """Evaluate and turn a raised error into an error value."""
    try:
        return thunk()
    except XlError as e:
        return e


def normalise(v):
    """Final cell value: whole areas are not a cell value; ints become floats."""
    if isinstance(v, Area):
        if v.height == 1 and v.width == 1:
            v = v.rows[0][0]
        else:
            return VALUE
    if v is None:
        return 0.0  # a formula that lands on an empty cell shows 0
    if isinstance(v, int) and not isinstance(v, bool):
        return float(v)
    return v
