"""Date arithmetic: differences, business days, time zones, ages.

Business-day counting excludes weekends and public holidays via the
`holidays` package (default country Spain, subdivision Madrid, both
overridable per call) — a sensible default for a Spanish user, made
configurable because the app is meant to travel.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import holidays as holidays_pkg
from dateutil import parser as dateutil_parser
from dateutil.relativedelta import relativedelta

__all__ = ["diff", "add", "business_days", "weekday", "iso_week", "age", "convert_tz", "parse", "DateError"]

_WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_WEEKDAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MAX_RANGE_YEARS = 200
MAX_HOLIDAYS_LISTED = 25

_TODAY_WORDS = {"today", "hoy", "now", "ahora"}
_YMD = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?=$|[ T])")


class DateError(ValueError):
    pass


class _BilingualParserInfo(dateutil_parser.parserinfo):
    """English + Spanish month/day names; day-first like Spain ("03/04/2025" = 3 April)."""

    MONTHS = [
        ("Jan", "January", "ene", "enero"), ("Feb", "February", "febrero"), ("Mar", "March", "marzo"),
        ("Apr", "April", "abr", "abril"), ("May", "mayo"), ("Jun", "June", "junio"),
        ("Jul", "July", "julio"), ("Aug", "August", "ago", "agosto"),
        ("Sep", "Sept", "September", "septiembre", "setiembre"), ("Oct", "October", "octubre"),
        ("Nov", "November", "noviembre"), ("Dec", "December", "dic", "diciembre"),
    ]
    WEEKDAYS = [
        ("Mon", "Monday", "lunes"), ("Tue", "Tuesday", "martes"), ("Wed", "Wednesday", "miércoles", "miercoles"),
        ("Thu", "Thursday", "jueves"), ("Fri", "Friday", "viernes"), ("Sat", "Saturday", "sábado", "sabado"),
        ("Sun", "Sunday", "domingo"),
    ]
    JUMP = dateutil_parser.parserinfo.JUMP + ["de", "del", "el", "a", "las"]

    def __init__(self) -> None:
        super().__init__(dayfirst=True, yearfirst=False)


_PARSER_INFO = _BilingualParserInfo()


def _parse_dt(value: str) -> datetime:
    """ISO / year-first first (never day-swapped), then day-first free text in English or Spanish."""
    text = value.strip()
    if text.lower() in _TODAY_WORDS:
        return datetime.now().replace(microsecond=0) if text.lower() in ("now", "ahora") else datetime.combine(date.today(), datetime.min.time())
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    m = _YMD.match(text)
    if m:
        try:
            base = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError as exc:
            raise DateError(f"not a valid date: {value!r} ({exc})") from exc
        rest = text[m.end():].strip()
        if not rest:
            return base
        try:
            t = dateutil_parser.parse(rest)
            return base.replace(hour=t.hour, minute=t.minute, second=t.second)
        except (ValueError, OverflowError):
            pass
    try:
        return dateutil_parser.parse(text, parserinfo=_PARSER_INFO)
    except (ValueError, OverflowError) as exc:
        raise DateError(
            f"could not parse date: {value!r}. Use ISO format YYYY-MM-DD (e.g. 2026-04-03); "
            "free text is read day-first, as in Spain (03/04/2026 = 3 April 2026)"
        ) from exc


def parse(text: str) -> dict[str, Any]:
    if not text or not str(text).strip():
        raise DateError("parse needs 'text', e.g. \"3 de abril de 2026\" or \"2026-04-03\"")
    dt = _parse_dt(str(text))
    out = {
        "input": text, "iso": dt.isoformat(), "date": dt.date().isoformat(), "has_time": _has_time(text),
        "weekday": _WEEKDAYS[dt.weekday()],
    }
    if re.match(r"^\s*\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", str(text)):
        out["note"] = "numeric dates are read day-first (DD/MM/YYYY), as in Spain"
    return out


def _has_time(text: str) -> bool:
    return ":" in text or str(text).strip().lower() in ("now", "ahora")


def _to_date(value: Any, field: str = "date") -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str) and value.strip():
        return _parse_dt(value).date()
    raise DateError(f"missing or invalid '{field}': pass a date such as 2026-04-03 (or 'today')")


def diff(start: str, end: str, unit: str = "days") -> dict[str, Any]:
    d1, d2 = _to_date(start, "start"), _to_date(end, "end")
    delta_days = (d2 - d1).days
    rd = relativedelta(d2, d1)
    result: dict[str, Any] = {
        "start": d1.isoformat(),
        "end": d2.isoformat(),
        "days": delta_days,
        "weeks": delta_days / 7,
        "calendar": {"years": rd.years, "months": rd.months, "days": rd.days},
    }
    if unit == "days":
        result["result"] = delta_days
    elif unit == "weeks":
        result["result"] = delta_days / 7
    elif unit in ("months", "years"):
        result["result"] = rd.years * 12 + rd.months if unit == "months" else rd.years + rd.months / 12
    else:
        raise DateError(f"unknown unit: {unit}; use days, weeks, months or years")
    return result


def add(start: str, *, days: int = 0, weeks: int = 0, months: int = 0, years: int = 0) -> dict[str, Any]:
    d = _to_date(start, "start")
    try:
        result = d + relativedelta(years=years, months=months, weeks=weeks, days=days)
    except (OverflowError, ValueError) as exc:
        raise DateError(f"the result falls outside the supported calendar (years 1-9999): {exc}") from exc
    return {"start": d.isoformat(), "result": result.isoformat(), "weekday": _WEEKDAYS[result.weekday()]}


def weekday(value: str) -> dict[str, Any]:
    d = _to_date(value, "value")
    return {
        "date": d.isoformat(), "weekday": _WEEKDAYS[d.weekday()], "weekday_es": _WEEKDAYS_ES[d.weekday()],
        "iso_weekday": d.isoweekday(), "is_weekend": d.weekday() >= 5,
    }


def iso_week(value: str) -> dict[str, Any]:
    d = _to_date(value, "value")
    iso = d.isocalendar()
    return {"date": d.isoformat(), "iso_year": iso[0], "iso_week": iso[1], "iso_weekday": iso[2]}


def business_days(
    start: str,
    end: str,
    *,
    country: str = "ES",
    subdivision: Optional[str] = None,
    include_end: bool = True,
) -> dict[str, Any]:
    """Weekdays that are not public holidays, from `start` to `end`.

    Both dates are counted when they are business days (the usual office
    convention, like a spreadsheet's NETWORKDAYS); `include_end=False`
    stops the day before `end`. Spain defaults to the Madrid calendar.
    """
    d1, d2 = _to_date(start, "start"), _to_date(end, "end")
    if d2 < d1:
        d1, d2 = d2, d1
    if (d2 - d1).days > MAX_RANGE_YEARS * 366:
        raise DateError(f"range too long: at most {MAX_RANGE_YEARS} years")
    country = (country or "ES").strip().upper()
    if subdivision is None and country == "ES":
        subdivision = "MD"
    subdivision = subdivision.strip().upper() if subdivision else None
    note = None
    years = range(d1.year, d2.year + 1)
    try:
        hol = holidays_pkg.country_holidays(country, subdiv=subdivision, years=years)
    except NotImplementedError as exc:
        if subdivision is None:
            raise DateError(f"no holiday calendar for country {country!r}: use an ISO code like ES, FR, DE, US, GB") from exc
        try:
            hol = holidays_pkg.country_holidays(country, years=years)
        except NotImplementedError as exc2:
            raise DateError(f"no holiday calendar for country {country!r}: use an ISO code like ES, FR, DE, US, GB") from exc2
        note = f"subdivision {subdivision!r} is not known for {country}; national holidays only"
        subdivision = None
    count = 0
    holiday_hits = []
    last = d2 if include_end else d2 - timedelta(days=1)
    cur = d1
    while cur <= last:
        if cur.weekday() < 5:
            if cur in hol:
                holiday_hits.append({"date": cur.isoformat(), "name": hol.get(cur)})
            else:
                count += 1
        cur += timedelta(days=1)
    out: dict[str, Any] = {
        "start": d1.isoformat(),
        "end": d2.isoformat(),
        "counted": "start and end included" if include_end else "start included, end excluded",
        "country": country,
        "subdivision": subdivision,
        "business_days": count,
        "holidays_excluded": holiday_hits[:MAX_HOLIDAYS_LISTED],
        "holidays_excluded_count": len(holiday_hits),
    }
    if len(holiday_hits) > MAX_HOLIDAYS_LISTED:
        out["holidays_truncated"] = True
    if note:
        out["note"] = note
    return out


def age(birth_date: str, *, on: Optional[str] = None) -> dict[str, Any]:
    d = _to_date(birth_date, "birth_date")
    ref = _to_date(on, "on") if on else date.today()
    rd = relativedelta(ref, d)
    return {
        "birth_date": d.isoformat(),
        "on": ref.isoformat(),
        "years": rd.years,
        "months": rd.months,
        "days": rd.days,
        "total_days": (ref - d).days,
    }


def convert_tz(value: str, *, from_tz: str, to_tz: str) -> dict[str, Any]:
    if not value or not from_tz or not to_tz:
        raise DateError("convert_tz needs value, from_tz and to_tz (IANA names such as Europe/Madrid, America/New_York)")
    dt = _parse_dt(value)
    try:
        src = ZoneInfo(from_tz)
        dst = ZoneInfo(to_tz)
    except Exception as exc:  # noqa: BLE001
        raise DateError(
            f"unknown time zone ({exc}); use IANA names such as Europe/Madrid, America/New_York, Asia/Tokyo, UTC"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=src)
    converted = dt.astimezone(dst)
    return {
        "input": value,
        "from_tz": from_tz,
        "to_tz": to_tz,
        "result": converted.isoformat(),
        "utc_offset_hours": converted.utcoffset().total_seconds() / 3600 if converted.utcoffset() else 0,
    }
