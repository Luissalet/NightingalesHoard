"""One timeout-guarded worker process for every engine that evaluates user input.

`calc`, `units` and `math` all turn text into big-number arithmetic, and a
single innocent-looking input can run for ever: `9**9**9**9`,
`factorial(10**9)`, `"10**10**10 m"` in Pint, a pathological `solve`.
Running them in the main process would pin a CPU and a server thread until
restart, so they all go through the same persistent `TimeoutWorker`
(spawn context, killed and restarted on timeout).

`_execute` is the module-level, picklable entry point the worker calls.
"""
from __future__ import annotations

from typing import Any, Optional

from . import calc, units
from .worker import TimeoutWorker, WorkerError, WorkerTimeout

__all__ = ["run", "warm_up", "shutdown", "DEFAULT_TIMEOUT_S", "MAX_TIMEOUT_S"]

DEFAULT_TIMEOUT_S = 10.0
MAX_TIMEOUT_S = 60.0


def _execute(task: str, payload: dict) -> dict:
    """Runs inside the worker process."""
    engine, _, op = task.partition(".")
    if engine == "calc":
        return calc.compute(payload["expression"], payload.get("precision", 15))
    if engine == "units":
        if op == "convert":
            return units.convert(payload["quantity"], payload["to"])
        if op == "check":
            return units.check(payload["expression"])
        if op == "compatible":
            return units.compatible(payload["unit"])
    if engine == "math":
        from . import symbolic  # imported lazily: symbolic imports this module

        return symbolic._execute(op, payload)
    raise ValueError(f"unknown task: {task}")


_worker: Optional[TimeoutWorker] = None


def _get_worker() -> TimeoutWorker:
    global _worker
    if _worker is None:
        _worker = TimeoutWorker(_execute, timeout=DEFAULT_TIMEOUT_S)
    return _worker


def _known_errors() -> dict[str, type[Exception]]:
    from .safe_ast import UnsafeExpressionError
    from .symbolic import SymbolicError

    return {
        "UnsafeExpressionError": UnsafeExpressionError,
        "CalcError": calc.CalcError,
        "SymbolicError": SymbolicError,
        "UnitsError": units.UnitsError,
    }


def clamp_timeout(timeout: Optional[float]) -> float:
    if timeout is None:
        return DEFAULT_TIMEOUT_S
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    if value != value:  # NaN
        return DEFAULT_TIMEOUT_S
    return max(0.1, min(value, MAX_TIMEOUT_S))


def run(task: str, payload: dict[str, Any], *, error_cls: type[Exception], timeout: Optional[float] = None) -> dict:
    """Run `task` ("calc", "units.convert", "math.solve", ...) with a hard timeout.

    Engine errors raised in the worker come back as the same exception class;
    a timeout or an unexpected failure is raised as `error_cls`.
    """
    try:
        return _get_worker().run(task, payload, timeout=clamp_timeout(timeout))
    except WorkerTimeout as exc:
        raise error_cls(str(exc)) from exc
    except WorkerError as exc:
        known = _known_errors().get(exc.kind)
        if known is not None:
            raise known(exc.message) from exc
        raise error_cls(f"{exc.kind}: {exc.message}" if exc.kind != "WorkerError" else exc.message) from exc


def warm_up() -> None:
    try:
        _get_worker().warm_up()
    except Exception:  # noqa: BLE001 - best effort; the first real call retries
        pass


def shutdown() -> None:
    global _worker
    if _worker is not None:
        _worker.shutdown()
        _worker = None
