"""Regressions from the first user walk (docs/USABILITY_REPORT.md): calc's
handling of ambiguous numbers and its error messages."""
import pytest

from laplaces_hoard.engines import calc
from laplaces_hoard.engines.safe_ast import UnsafeExpressionError


def test_dotted_thousands_look_alike_gets_a_warning_not_a_silent_answer():
    r = calc.compute("1.000 * 3")
    assert r["exact"] == "3"  # the value is still exact Python/Sympy semantics
    assert "warning" in r
    assert "1.000" in r["warning"] and "1000" in r["warning"]


def test_a_real_decimal_number_never_gets_the_thousands_warning():
    r = calc.compute("3.5 * 2")
    assert "warning" not in r
    r2 = calc.compute("3.14159")
    assert "warning" not in r2


def test_decimal_comma_parsed_as_a_list_gives_a_decimal_hint():
    with pytest.raises(UnsafeExpressionError, match="use '.' instead of ','"):
        calc.compute("3,5 + 2")


def test_wrong_argument_count_never_leaks_the_internal_helper_name():
    with pytest.raises(UnsafeExpressionError) as exc_info:
        calc.compute("pct(21, 1234, 56)")
    message = str(exc_info.value)
    assert "_pct" not in message
    assert "pct(" in message


def test_implicit_multiplication_gets_a_hint():
    with pytest.raises(UnsafeExpressionError, match=r"write '\*' explicitly"):
        calc.compute("5x")


def test_a_spanish_decimal_inside_a_function_call_says_how_to_write_it():
    # "¿y el 21 % de 1.234,56?" passed through as typed: the comma splits the
    # number into two arguments; the error must say what to write instead
    with pytest.raises(UnsafeExpressionError) as exc_info:
        calc.compute("pct(21, 1.234,56)")
    message = str(exc_info.value)
    assert "pct(p, x)" in message
    assert "1234.56" in message


def test_thousands_dot_warning_shows_the_value_actually_used():
    r = calc.compute("1.000 * 3")
    assert r["decimal"] == "3"
    assert "1.000 = 1.0, not 1000" in r["warning"]


@pytest.mark.parametrize("expression", ["(1.035^10 - 1) * 100", "3.142 * 2", "0.125 * 8", "2.718^2"])
def test_ordinary_three_decimal_numbers_do_not_get_the_thousands_warning(expression):
    # the README's own interest-rate example used to carry a false warning
    assert "warning" not in calc.compute(expression)


@pytest.mark.parametrize("expression", ["2.500 * 4", "12.345 + 1", "100.000 / 12"])
def test_thousands_looking_numbers_still_get_the_warning(expression):
    assert "warning" in calc.compute(expression)
