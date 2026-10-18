"""Regressions from the first user walk (docs/USABILITY_REPORT.md): a
notebook cell with a bad "order" argument used to raise a raw ValueError
that bypassed every error handler and came back as an unhandled 500,
leaving the cell with no result at all (and no error shown)."""
import pytest

from laplaces_hoard.engines.safe_ast import UnsafeExpressionError

from laplaces_hoard.engines import symbolic
from laplaces_hoard.engines.symbolic import SymbolicError, parse_cell


def test_diff_with_a_non_numeric_order_is_a_clear_error_not_a_raw_valueerror():
    with pytest.raises(SymbolicError, match="whole number"):
        parse_cell("diff(x**2, x, dos)")


def test_series_with_a_non_numeric_order_is_a_clear_error():
    with pytest.raises(SymbolicError, match="whole number"):
        parse_cell("series(sin(x), x, 0, dos)")


def test_diff_with_a_numeric_order_still_works():
    op, payload = parse_cell("diff(x**2, x, 2)")
    assert op == "diff"
    assert payload["order"] == 2


def test_math_tool_implicit_multiplication_gets_the_same_hint_as_calc():
    # the math tool (not only calc) used to answer "invalid decimal literal"
    try:
        with pytest.raises(UnsafeExpressionError, match=r"5\*x"):
            symbolic.run("solve", expressions=["x^2 - 5x + 6 = 0"])
    finally:
        symbolic.shutdown()
