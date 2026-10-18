from laplaces_hoard.engines import dates


def test_business_days_excludes_madrid_holiday():
    """2026-05-01 is a public holiday in Madrid (Labour Day) and must not be counted."""
    r = dates.business_days("2026-05-01", "2026-05-15", country="ES", subdivision="MD")
    assert any(h["date"] == "2026-05-01" for h in r["holidays_excluded"])
    # Fri 1 (holiday, excluded) .. Fri 15, both ends counted: weekdays are
    # 1,4,5,6,7,8,11,12,13,14,15 minus the holiday on the 1st -> 10 business days.
    assert r["business_days"] == 10
    # end excluded: stops on Thu 14 -> 9
    assert dates.business_days("2026-05-01", "2026-05-15", include_end=False)["business_days"] == 9


def test_diff_calendar_aware():
    r = dates.diff("2026-01-31", "2026-03-01")
    assert r["calendar"]["months"] == 1
    assert r["calendar"]["days"] == 1


def test_add_days():
    r = dates.add("2026-09-22", days=10)
    assert r["result"] == "2026-10-02"


def test_weekday_and_iso_week():
    w = dates.weekday("2026-09-22")
    assert w["weekday"] == "Tuesday"
    iso = dates.iso_week("2026-09-22")
    assert iso["iso_week"] == 39


def test_age_calculation():
    r = dates.age("1990-05-15", on="2026-09-22")
    assert r["years"] == 36


def test_convert_tz_handles_offset():
    r = dates.convert_tz("2026-09-22 14:00", from_tz="Europe/Madrid", to_tz="America/New_York")
    assert r["result"].startswith("2026-09-22T08:00:00")


def test_parse_iso_date():
    r = dates.parse("2026-09-22")
    assert r["date"] == "2026-09-22"


# -- regressions found in review ------------------------------------------

import pytest  # noqa: E402


def test_numeric_dates_are_day_first_but_iso_is_never_swapped():
    assert dates.parse("03/04/2025")["date"] == "2025-04-03"
    assert dates.parse("2025-03-04")["date"] == "2025-03-04"
    assert dates.parse("2025/03/04")["date"] == "2025-03-04"
    assert dates.parse("2025-03-04 10:30")["iso"] == "2025-03-04T10:30:00"


def test_spanish_month_names():
    assert dates.parse("3 de abril de 2026")["date"] == "2026-04-03"
    assert dates.parse("15 septiembre 2026")["date"] == "2026-09-15"
    assert dates.weekday("2026-09-22")["weekday_es"] == "martes"


def test_today_is_understood():
    from datetime import date

    assert dates.parse("hoy")["date"] == date.today().isoformat()
    assert dates.diff("today", date.today().isoformat())["days"] == 0


def test_business_days_count_both_ends_by_default():
    # Mon 2026-09-21 .. Fri 2026-09-25: five working days, no holidays
    r = dates.business_days("2026-09-21", "2026-09-25")
    assert r["business_days"] == 5
    assert r["counted"] == "start and end included"
    assert dates.business_days("2026-09-21", "2026-09-25", include_end=False)["business_days"] == 4


def test_business_days_madrid_default_only_for_spain():
    # 2 May 2026 (Madrid regional day) is a Saturday; use 2 May 2024 (Thursday) instead
    es = dates.business_days("2024-05-02", "2024-05-02")
    assert es["subdivision"] == "MD" and es["business_days"] == 0
    # a US count must not silently use Maryland ("MD") holidays
    us = dates.business_days("2026-01-01", "2026-01-09", country="US")
    assert us["subdivision"] is None


def test_unknown_country_is_an_actionable_error():
    with pytest.raises(dates.DateError, match="ISO code"):
        dates.business_days("2026-01-01", "2026-01-10", country="XX")
    r = dates.business_days("2026-01-01", "2026-01-10", country="ES", subdivision="ZZ")
    assert "national holidays only" in r["note"]


def test_missing_dates_name_the_missing_field():
    with pytest.raises(dates.DateError, match="'end'"):
        dates.diff("2026-01-01", None)
