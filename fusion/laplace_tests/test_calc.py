from laplaces_hoard.engines import calc
from laplaces_hoard.engines.safe_ast import UnsafeExpressionError


def test_exact_decimal_addition():
    r = calc.compute("0.1 + 0.2")
    assert r["exact"] == "3/10"
    assert r["is_exact"] is True
    assert r["decimal"] == "0.3"


def test_percentage_helpers():
    r = calc.compute("pct(15, 2347)")
    assert r["exact"] == "7041/20"
    assert r["decimal"] == "352.05"
    r2 = calc.compute("pct_change(100, 150)")
    assert r2["exact"] == "50"


def test_number_theory_helpers():
    assert calc.compute("isprime(97)")["value"] is True
    assert calc.compute("isprime(100)")["value"] is False
    assert calc.compute("nextprime(100)")["value"] == 101
    assert calc.compute("factorint(360)")["factors"] == {"2": 3, "3": 2, "5": 1}


def test_ast_whitelist_refuses_dunder_import():
    for bad in ['__import__("os")', 'getattr(1, "__class__")']:
        try:
            calc.compute(bad)
            assert False, f"should have been rejected: {bad}"
        except UnsafeExpressionError:
            pass


def test_ast_whitelist_refuses_attribute_access():
    import pytest
    with pytest.raises(UnsafeExpressionError):
        calc.compute("(1).__class__")


def test_ast_whitelist_refuses_lambda():
    import pytest
    with pytest.raises(UnsafeExpressionError):
        calc.compute("(lambda x: x)(1)")


def test_ast_whitelist_refuses_comprehension():
    import pytest
    with pytest.raises(UnsafeExpressionError):
        calc.compute("[x for x in range(3)]")


def test_ast_whitelist_refuses_unknown_name():
    import pytest
    with pytest.raises(UnsafeExpressionError):
        calc.compute("secret_variable + 1")


def test_division_by_zero_is_reported_as_error():
    import pytest
    with pytest.raises(calc.CalcError):
        calc.compute("1/0")


def test_precision_is_respected_and_capped():
    r = calc.compute("sqrt(2)", precision=50)
    assert r["digits"] == 50
    assert len(r["decimal"].__str__().replace(".", "").lstrip("-")) >= 15
