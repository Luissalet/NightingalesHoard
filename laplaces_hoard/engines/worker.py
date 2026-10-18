"""A persistent worker process with a hard per-call timeout.

SymPy can hang (some `solve`/`integrate` inputs never return). Rather than
trust a signal-based timeout (unreliable on Windows and inside threads),
each call runs in a separate `multiprocessing` process using the `spawn`
context (the only one available on Windows, so the same code path is
exercised on both platforms). If the call does not answer within the
timeout the process is killed and a fresh one is started for the next
call — the worker "self-heals" instead of wedging the app.

`target` must be a *module-level* function (picklable under spawn):
`target(op: str, payload: dict) -> dict`, raising on error.
"""
from __future__ import annotations

import multiprocessing as mp
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Optional

_CTX = mp.get_context("spawn")
STARTUP_TIMEOUT_S = 60.0


def _windows_console_less() -> bool:
    """True on Windows when this process has no visible console window.

    multiprocessing starts workers with python.exe and creation flags 0: a
    console-less parent (started detached or with CREATE_NO_WINDOW by a
    launcher) would make Windows open a new, visible console for every
    worker. In that case the worker runs under pythonw.exe instead.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        get_console_window = ctypes.WinDLL("kernel32", use_last_error=True).GetConsoleWindow
        get_console_window.argtypes = []
        get_console_window.restype = wintypes.HWND  # pointer-sized: 64-bit safe
        return not get_console_window()
    except (OSError, AttributeError):
        return False


def _configure_executable() -> None:
    if not _windows_console_less():
        return
    # the *base* interpreter, as multiprocessing itself does for venvs: the venv
    # launcher stub would sit between us and the real process and break the
    # handle duplication the spawn protocol relies on. The venv's packages are
    # still found because spawn sends the parent's sys.path to the child.
    base = Path(getattr(sys, "_base_executable", sys.executable))
    pythonw = base.with_name("pythonw.exe")
    if pythonw.exists():
        _CTX.set_executable(str(pythonw))


_configure_executable()


class WorkerTimeout(RuntimeError):
    pass


class WorkerError(RuntimeError):
    """The target raised inside the worker. `kind` is the exception class name."""

    def __init__(self, message: str, kind: str = "WorkerError"):
        super().__init__(message)
        self.kind = kind
        self.message = message


def _loop(target: Callable[[str, dict], dict], conn) -> None:
    # `target` was unpickled (so its module, e.g. SymPy, imported) before we got
    # here: tell the parent we are ready, so start-up time never eats into the
    # first call's timeout.
    conn.send(("ready", None))
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        if msg is None:
            return
        op, payload = msg
        try:
            result = target(op, payload)
            conn.send(("ok", result))
        except Exception as exc:  # noqa: BLE001 - report any failure to the parent
            conn.send(("error", (type(exc).__name__, str(exc))))


class TimeoutWorker:
    """Restartable process pool of size 1, guarded by a hard timeout."""

    def __init__(self, target: Callable[[str, dict], dict], timeout: float = 10.0):
        self._target = target
        self._timeout = timeout
        self._proc: Optional[mp.process.BaseProcess] = None
        self._parent_conn = None
        # One pipe, one process: concurrent HTTP requests must take turns, or
        # two callers would read each other's answers off the pipe.
        self._lock = threading.Lock()

    def _ensure_started(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            return
        parent_conn, child_conn = _CTX.Pipe()
        proc = _CTX.Process(target=_loop, args=(self._target, child_conn), daemon=True)
        proc.start()
        child_conn.close()  # the child owns its end now
        self._proc = proc
        self._parent_conn = parent_conn
        try:
            ready = parent_conn.poll(STARTUP_TIMEOUT_S) and parent_conn.recv()
        except (EOFError, OSError):
            ready = None
        if not ready or ready[0] != "ready":
            self._kill()
            raise WorkerError("the computation worker could not start", kind="WorkerError")

    def warm_up(self) -> None:
        """Start the process ahead of the first call (imports can take seconds on Windows)."""
        with self._lock:
            self._ensure_started()

    def _kill(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.join(timeout=2)
                if self._proc.is_alive():
                    self._proc.kill()
                    self._proc.join(timeout=2)
            except Exception:  # noqa: BLE001
                pass
        if self._parent_conn is not None:
            try:
                self._parent_conn.close()
            except Exception:  # noqa: BLE001
                pass
        self._proc = None
        self._parent_conn = None

    def run(self, op: str, payload: dict, timeout: Optional[float] = None) -> dict[str, Any]:
        timeout = timeout if timeout is not None else self._timeout
        # wait for a busy worker at most one extra timeout (+ start-up slack)
        if not self._lock.acquire(timeout=timeout + 30):
            raise WorkerTimeout("the computation worker is busy with another request; retry in a moment")
        try:
            return self._run_locked(op, payload, timeout)
        finally:
            self._lock.release()

    def _run_locked(self, op: str, payload: dict, timeout: float) -> dict[str, Any]:
        self._ensure_started()
        assert self._parent_conn is not None
        self._parent_conn.send((op, payload))
        if not self._parent_conn.poll(timeout):
            self._kill()
            raise WorkerTimeout(
                f"'{op}' did not finish within {timeout:g}s and was stopped; "
                "try a smaller or more specific input"
            )
        try:
            status, value = self._parent_conn.recv()
        except (EOFError, OSError) as exc:
            self._kill()
            raise WorkerError(f"worker died unexpectedly: {exc}") from exc
        if status == "error":
            kind, message = value if isinstance(value, tuple) else ("WorkerError", str(value))
            raise WorkerError(message, kind=kind)
        return value

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown_locked()

    def _shutdown_locked(self) -> None:
        if self._parent_conn is not None:
            try:
                self._parent_conn.send(None)
            except Exception:  # noqa: BLE001
                pass
        self._kill()
