"""Unit conversion and dimensional checking, backed by Pint.

No currencies: exchange rates change over time and need network access,
which this local-only tool does not make on its own — `convert()` raises a
clear error if the target/source look like a currency code.
"""
from __future__ import annotations

import re
from typing import Any

import pint

__all__ = ["convert", "check", "compatible", "UnitsError"]

_ureg = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
_ureg.formatter.default_format = "~P"

# "pound"/"pounds" are deliberately absent: in Pint they are the mass unit (lb).
_CURRENCY_CODES = {
    "usd", "eur", "gbp", "jpy", "cad", "aud", "chf", "cny", "mxn", "brl",
    "dollar", "dollars", "euro", "euros", "yen", "sterling",
}
_CURRENCY_SYMBOLS = ("$", "€", "£", "¥")


def _sig(x: float, digits: int = 12) -> float:
    """Round to `digits` significant digits: hides float noise like -40.000000000000064."""
    if x == 0 or x != x or x in (float("inf"), float("-inf")):
        return x
    return float(f"{x:.{digits}g}")


def _fmt(x: float) -> str:
    return f"{x:.12g}"

_COMPOUND_TOKEN = re.compile(r"(-?\d+(?:\.\d+)?)\s*([A-Za-zµ°]+)")

# A number written with a decimal comma and no dot anywhere in the text
# ("3,5 km"): Pint silently reads "3,5" as 35 (it just drops the comma), so
# this is normalised to "3.5" before Pint ever sees it. Only applied when
# there is no '.' in the text at all, so an already-unambiguous number is
# never second-guessed.
_DECIMAL_COMMA = re.compile(r"(?<!\d)(-?\d+),(\d+)(?!\d)")

# Spanish names for common units, mapped to the English/symbol names Pint
# understands. Matched as whole words so "metros" -> "meter" but a unit that
# already has no Spanish counterpart (km, kg, mph...) passes through as-is.
_SPANISH_UNITS = {
    "metros": "meter", "metro": "meter", "kilometros": "kilometer", "kilómetros": "kilometer",
    "kilometro": "kilometer", "kilómetro": "kilometer", "centimetros": "centimeter",
    "centímetros": "centimeter", "centimetro": "centimeter", "centímetro": "centimeter",
    "milimetros": "millimeter", "milímetros": "millimeter", "milimetro": "millimeter",
    "milímetro": "millimeter", "millas": "mile", "milla": "mile", "pies": "foot", "pie": "foot",
    "pulgadas": "inch", "pulgada": "inch", "yardas": "yard", "yarda": "yard",
    "libras": "pound", "libra": "pound", "onzas": "ounce", "onza": "ounce",
    "kilogramos": "kilogram", "kilogramo": "kilogram", "gramos": "gram", "gramo": "gram",
    "toneladas": "metric_ton", "tonelada": "metric_ton",
    "litros": "liter", "litro": "liter", "mililitros": "milliliter", "mililitro": "milliliter",
    "galones": "gallon", "galon": "gallon", "galón": "gallon",
    "segundos": "second", "segundo": "second", "minutos": "minute", "minuto": "minute",
    "horas": "hour", "hora": "hour", "dias": "day", "días": "day", "dia": "day", "día": "day",
    "semanas": "week", "semana": "week",
    "vatios": "watt", "vatio": "watt", "julios": "joule", "julio": "joule",
    "bares": "bar",
}
_SPANISH_UNIT_RE = re.compile(
    r"\b(" + "|".join(sorted(_SPANISH_UNITS, key=len, reverse=True)) + r")\b", re.IGNORECASE
)


def _normalize_decimal_comma(text: str) -> str:
    if "." in text:
        return text
    return _DECIMAL_COMMA.sub(r"\1.\2", text)


def _translate_spanish_units(text: str) -> str:
    return _SPANISH_UNIT_RE.sub(lambda m: _SPANISH_UNITS[m.group(0).lower()], text)


class UnitsError(ValueError):
    pass


def _reject_currency(text: str) -> None:
    words = re.findall(r"[A-Za-z]+", text.lower())
    if any(w in _CURRENCY_CODES for w in words) or any(sym in text for sym in _CURRENCY_SYMBOLS):
        raise UnitsError(
            "currency conversion is not supported: exchange rates change and "
            "need network access, which this local tool does not perform on its own"
        )


_POWER = re.compile(r"(\*\*|\^)\s*(\S+)")


def _guard_powers(text: str) -> None:
    """Pint evaluates `**` with Python ints: "10**10**10 m" would allocate gigabytes
    before any timeout fires. Unit powers are small (m**3, s^-2, 10**6), so any
    exponent that is not a plain number up to 100 is refused."""
    for _, token in _POWER.findall(text):
        token = token.strip("()")
        m = re.match(r"^[-+]?\d+(\.\d+)?", token)
        if not m or abs(float(m.group(0))) > 100 or _POWER.search(token[m.end():]):
            raise UnitsError(f"exponent {token!r} is not supported here: use plain powers up to 100, e.g. m**3 or 10**6")


def _parse_quantity(text: str) -> "pint.Quantity":
    _reject_currency(text)
    _guard_powers(text)
    text = _translate_spanish_units(_normalize_decimal_comma(text.strip()))
    matches = _COMPOUND_TOKEN.findall(text)
    reconstructed = " ".join(f"{n} {u}".strip() for n, u in matches)
    if matches and len(matches) > 1 and reconstructed.replace("  ", " ") == text.replace("  ", " "):
        total = None
        for n, u in matches:
            q = _ureg.Quantity(float(n), u)
            total = q if total is None else total + q
        return total
    try:
        return _ureg.Quantity(text)
    except Exception as exc:  # noqa: BLE001
        raise UnitsError(f"could not parse quantity: {text!r} ({exc})") from exc


def convert(quantity: str, to: str) -> dict[str, Any]:
    q = _parse_quantity(quantity)
    _reject_currency(to)
    _guard_powers(to)
    to = _translate_spanish_units(to.strip())
    try:
        target = _ureg.Unit(to)
    except Exception as exc:  # noqa: BLE001
        raise UnitsError(f"unknown target unit: {to!r} ({exc})") from exc
    try:
        result = q.to(target)
    except pint.DimensionalityError as exc:
        raise UnitsError(
            f"cannot convert {q.units} to {target}: dimensions differ ({exc})"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - e.g. offset-unit arithmetic
        raise UnitsError(f"cannot convert {quantity!r} to {to!r}: {exc}") from exc
    try:
        from_mag = float(q.magnitude)
        to_mag = float(result.magnitude)
    except (TypeError, ValueError) as exc:
        raise UnitsError("only single numeric quantities can be converted (no arrays)") from exc
    return {
        "input": quantity,
        "to": to,
        "from_magnitude": _sig(from_mag),
        "from_unit": str(q.units),
        "to_magnitude": _sig(to_mag),
        "to_unit": str(result.units),
        "formatted": f"{_fmt(to_mag)} {result.units:~P}",
        "precision_note": "floating-point conversion, rounded to 12 significant digits",
    }


def check(expression: str) -> dict[str, Any]:
    """Check dimensional consistency, e.g. `"3 m/s * 2 s" -> length` OK, wrong units flagged."""
    _guard_powers(expression)
    expression = _translate_spanish_units(_normalize_decimal_comma(expression.strip()))
    try:
        q = _ureg.parse_expression(expression)
    except pint.DimensionalityError as exc:
        raise UnitsError(f"dimensionally inconsistent: {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        raise UnitsError(f"could not parse expression: {expression!r} ({exc})") from exc
    dim = q.dimensionality if hasattr(q, "dimensionality") else pint.util.UnitsContainer()
    try:
        magnitude = float(q.magnitude) if hasattr(q, "magnitude") else float(q)
    except (TypeError, ValueError) as exc:
        raise UnitsError(f"could not evaluate {expression!r} to a single quantity") from exc
    return {
        "input": expression,
        "dimensionality": str(dim) if str(dim) else "dimensionless",
        "magnitude": _sig(magnitude),
        "units": str(q.units) if hasattr(q, "units") else "dimensionless",
    }


def compatible(unit: str) -> dict[str, Any]:
    unit = _translate_spanish_units(unit.strip())
    try:
        u = _ureg.Unit(unit)
    except Exception as exc:  # noqa: BLE001
        raise UnitsError(f"unknown unit: {unit!r} ({exc})") from exc
    names = sorted(str(x) for x in _ureg.get_compatible_units(u))
    return {"unit": unit, "dimensionality": str(u.dimensionality), "compatible": names[:50], "count": len(names)}
