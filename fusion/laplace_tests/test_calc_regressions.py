"""Regressions found in review: precision, rounding, syntax a model actually writes."""
import time

import pytest

from laplaces_hoard.engines import calc, sandbox
from laplaces_hoard.engines.safe_ast import UnsafeExpressionError


def test_decimal_honours_the_requested_precision():
    r = calc.compute("sqrt(2)", precision=40)
    assert r["decimal"] == "1.41421356237309504880168872420969807857"
    assert r["is_exact"] is False


def test_is_exact_means_the_decimal_is_the_value():
    assert calc.compute("0.1 + 0.2")["is_exact"] is True
    third = calc.compute("1/3")
    assert third["exact"] == "1/3"
    assert third["decimal"] == "0.333333333333333"
    assert third["is_exact"] is False


def test_caret_is_power():
    assert calc.compute("2^10")["exact"] == "1024"


def test_round_is_exact_half_away_from_zero():
    assert calc.compute("round(2.5)")["exact"] == "3"
    assert calc.compute("round(-2.5)")["exact"] == "-3"
    assert calc.compute("round(1.005, 2)")["exact"] == "101/100"
    assert calc.compute("round(1.005, 2)")["decimal"] == "1.01"
    assert calc.compute("round(1234.5678, -2)")["exact"] == "1200"


def test_aggregates_accept_a_list():
    assert calc.compute("sum([1, 2, 3])")["exact"] == "6"
    assert calc.compute("mean([1, 2, 3, 4])")["exact"] == "5/2"
    assert calc.compute("median([5, 1, 3])")["exact"] == "3"
    assert calc.compute("max([1, 7, 3])")["exact"] == "7"
    with pytest.raises(UnsafeExpressionError, match="at least one"):
        calc.compute("mean()")


def test_comparison_returns_a_boolean():
    r = calc.compute("0.1 + 0.2 == 0.3")
    assert r["value"] is True and r["exact"] == "True"
    assert calc.compute("2**10 > 1000")["value"] is True


def test_a_list_alone_is_an_actionable_error():
    with pytest.raises(UnsafeExpressionError, match="sum"):
        calc.compute("[1, 2]")


def test_parse_errors_carry_a_hint():
    with pytest.raises(UnsafeExpressionError, match=r"pct\(15, 2347\)"):
        calc.compute("15% of 2347")
    with pytest.raises(UnsafeExpressionError, match="decimal separator"):
        calc.compute("3,5 * 2 +")


def test_huge_exact_results_are_capped():
    r = calc.compute("factorial(5000)")
    assert r["exact_truncated"] is True
    assert r["exact_digit_count"] == 16326
    assert len(r["exact"]) <= calc.MAX_OUTPUT_CHARS + 1
    mid = calc.compute("2**5000")  # 1506 digits: shown in full
    assert "exact_truncated" not in mid and len(mid["exact"]) == 1506
    assert r["latex"] is None
    assert r["decimal"].startswith("4.22857792660554e+16325")


def test_runaway_expression_times_out_and_worker_recovers():
    start = time.monotonic()
    with pytest.raises(calc.CalcError, match="did not finish"):
        sandbox.run("calc", {"expression": "nextprime(10**3000)"}, error_cls=calc.CalcError, timeout=1)
    assert time.monotonic() - start < 8
    r = sandbox.run("calc", {"expression": "0.1 + 0.2"}, error_cls=calc.CalcError)
    assert r["exact"] == "3/10"


def test_worker_preserves_engine_error_types():
    with pytest.raises(UnsafeExpressionError):
        sandbox.run("calc", {"expression": "__import__('os')"}, error_cls=calc.CalcError)
    with pytest.raises(calc.CalcError, match="division by zero"):
        sandbox.run("calc", {"expression": "1/0"}, error_cls=calc.CalcError)


def test_concurrent_calls_get_their_own_answers():
    from concurrent.futures import ThreadPoolExecutor

    exprs = [f"{i} * 1000 + 7" for i in range(24)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda e: sandbox.run("calc", {"expression": e}, error_cls=calc.CalcError), exprs))
    assert [r["exact"] for r in results] == [str(i * 1000 + 7) for i in range(24)]


def test_number_theory_helpers_must_be_the_whole_expression():
    # used to silently drop the "+ 1" and answer isprime(97)
    with pytest.raises(UnsafeExpressionError, match="whole expression"):
        calc.compute("isprime(97) + 1")


def test_memory_bomb_powers_are_refused_before_allocating():
    start = time.monotonic()
    with pytest.raises(UnsafeExpressionError, match="too large"):
        calc.compute("2**(10**10)")
    with pytest.raises(UnsafeExpressionError, match="too large"):
        calc.compute("9**9**9")
    assert time.monotonic() - start < 1
    # big but reasonable powers still work exactly
    assert calc.compute("2**100000")["exact_digit_count"] == 30103
    assert calc.compute("(1/2)**3")["exact"] == "1/8"
