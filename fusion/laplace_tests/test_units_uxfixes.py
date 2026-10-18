"""Regressions from the first user walk (docs/USABILITY_REPORT.md): Spanish
decimal commas and Spanish unit names silently misread."""
import pytest

from laplaces_hoard.engines import units


def test_decimal_comma_is_not_silently_dropped():
    # "3,5 km" used to become 35 km (Pint just strips the comma)
    r = units.convert("3,5 km", "mi")
    assert r["from_magnitude"] == pytest.approx(3.5)
    assert r["to_magnitude"] == pytest.approx(2.1748, abs=0.001)


def test_decimal_comma_with_negative_sign():
    r = units.convert("-2,5 degC", "degF")
    assert r["from_magnitude"] == pytest.approx(-2.5)


def test_a_number_with_an_actual_dot_is_never_reinterpreted():
    # only applied when there is no '.' anywhere in the text
    r = units.convert("3.5 km", "m")
    assert r["from_magnitude"] == pytest.approx(3.5)


def test_check_also_normalises_decimal_commas():
    r = units.check("3,5 m/s * 2 s")
    assert r["magnitude"] == pytest.approx(7.0)


def test_spanish_unit_names_are_understood():
    r = units.convert("2 libras", "kg")
    assert r["to_magnitude"] == pytest.approx(0.907185, abs=1e-5)
    r2 = units.convert("10 pulgadas", "cm")
    assert r2["to_magnitude"] == pytest.approx(25.4)
    r3 = units.convert("5 kilometros", "m")
    assert r3["to_magnitude"] == pytest.approx(5000.0)


def test_spanish_unit_name_as_the_target():
    r = units.convert("1 kg", "libras")
    assert r["to_magnitude"] == pytest.approx(2.20462, abs=1e-4)


def test_compatible_accepts_a_spanish_unit_name():
    r = units.compatible("pulgadas")
    assert r["dimensionality"] == "[length]"
    assert "m" in r["compatible"]
