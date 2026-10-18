import pytest

from laplaces_hoard.engines import units


def test_speed_conversion():
    r = units.convert("3.5 km/h", "m/s")
    assert r["to_magnitude"] == pytest.approx(0.9722222222222223)


def test_compound_length_conversion():
    r = units.convert("5 ft 11 in", "cm")
    assert r["to_magnitude"] == pytest.approx(180.34, abs=0.01)


def test_temperature_offset_handled_correctly():
    """100 degF is 37.78 degC, not 55.56 (which a naive *scale* conversion would give)."""
    r = units.convert("100 degF", "degC")
    assert r["to_magnitude"] == pytest.approx(37.7778, abs=0.001)
    r2 = units.convert("0 degC", "degF")
    assert r2["to_magnitude"] == pytest.approx(32.0, abs=0.001)


def test_dimension_mismatch_is_rejected():
    with pytest.raises(units.UnitsError):
        units.convert("5 kg", "m")


def test_currency_is_rejected():
    with pytest.raises(units.UnitsError):
        units.convert("10 USD", "EUR")


def test_dimensional_check():
    r = units.check("3 m/s * 2 s")
    assert r["dimensionality"] == "[length]"


def test_compatible_units_lists_length_units():
    r = units.compatible("meter")
    assert r["count"] > 0


def test_pounds_are_mass_not_currency():
    r = units.convert("5 pounds", "kg")
    assert r["to_magnitude"] == pytest.approx(2.26796185)
    with pytest.raises(units.UnitsError, match="currency"):
        units.convert("10 £", "EUR")


def test_float_noise_is_rounded_away():
    r = units.convert("-40 degC", "degF")
    assert r["to_magnitude"] == -40.0
    assert r["formatted"] == "-40 °F"
    assert units.convert("1 mile", "km")["formatted"] == "1.609344 km"


def test_memory_bomb_exponents_are_refused():
    import time

    start = time.monotonic()
    for bad in ("10**10**10 m", "2**(10**9) m", "10^1000 m"):
        with pytest.raises(units.UnitsError, match="exponent"):
            units.convert(bad, "km")
    assert time.monotonic() - start < 1
    assert units.convert("10**6 m**2", "km**2")["to_magnitude"] == pytest.approx(1.0)
    assert units.convert("9.81 m/s^2", "ft/s**2")["to_magnitude"] == pytest.approx(32.185, abs=1e-3)
