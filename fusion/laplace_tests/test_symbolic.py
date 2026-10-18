import time

import pytest

from laplaces_hoard.engines import symbolic
from laplaces_hoard.engines.worker import TimeoutWorker, WorkerTimeout


def _slow_or_fast(op, payload):
    if op == "slow":
        time.sleep(5)
        return {"ok": True}
    return {"ok": True, "fast": True}


def test_solve_verifies_by_substitution():
    r = symbolic.run("solve", expressions=["x**2 - 4 == 0"], variables=["x"])
    assert r["verified"] is True
    values = {sol["values"]["x"] for sol in r["result"]}
    assert values == {"-2", "2"}


def test_solve_linear_system():
    r = symbolic.run("solve", expressions=["x + y == 3", "x - y == 1"], variables=["x", "y"])
    assert r["verified"] is True
    assert r["result"][0]["values"] == {"x": "2", "y": "1"}


def test_diff_and_integrate_are_inverse_on_polynomials():
    d = symbolic.run("diff", expression="x**3 + 2*x", variable="x")
    assert d["result"] == "3*x**2 + 2"
    i = symbolic.run("integrate", expression="x**2", variable="x", lower=0, upper=2)
    assert i["result"] == "8/3"


def test_matrix_determinant_and_multiply():
    det = symbolic.run("matrix", matrix_op="det", matrix=[["1", "2"], ["3", "4"]])
    assert det["result"] == "-2"
    mul = symbolic.run("matrix", matrix_op="multiply", matrix=[["1", "2"]], matrix2=[["1"], ["1"]])
    assert mul["result"] == "Matrix([[3]])"


def test_worker_timeout_then_recovers():
    """The generic worker: kills a hung call and starts a fresh process for the next one."""
    worker = TimeoutWorker(_slow_or_fast, timeout=1)
    try:
        # Imports under Windows spawn are startup, explicitly outside the
        # per-call timeout. The app likewise warms its worker before use.
        worker.warm_up()
        start = time.time()
        with pytest.raises(WorkerTimeout):
            worker.run("slow", {})
        assert time.time() - start < 3
        # self-healed: a normal call right after must still work
        assert worker.run("fast", {}) == {"ok": True, "fast": True}
    finally:
        worker.shutdown()


def _always_slow(op, payload):
    # far longer than the bound below, so "returned in time" can only mean
    # the timeout fired - while leaving room for the worker process to
    # spawn (twice: start, then restart after the kill) on a busy machine
    time.sleep(30)
    return {}


def test_symbolic_run_timeout_reports_as_error_not_a_hang(monkeypatch):
    """Deterministic version of the real 'SymPy can hang' scenario: force the
    dispatch to always be slow, and check `run()` surfaces it as a
    SymbolicError (not a hang) within the requested timeout."""
    from laplaces_hoard.engines import sandbox

    fake_worker = TimeoutWorker(_always_slow, timeout=10)
    monkeypatch.setattr(sandbox, "_worker", fake_worker)
    start = time.time()
    with pytest.raises(symbolic.SymbolicError):
        symbolic.run("simplify", expression="x", timeout=0.3)
    assert time.time() - start < 10
    fake_worker.shutdown()


def test_symbolic_engine_recovers_after_a_reported_timeout():
    # the real (module-level) worker must still answer correctly after the
    # timeout test above swapped in and tore down a different worker instance.
    r = symbolic.run("simplify", expression="2*x + 3*x")
    assert r["result"] == "5*x"
