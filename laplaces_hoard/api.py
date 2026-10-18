"""FastAPI application: HTTP surface for the UI and the MCP adapter.

`create_app(data_dir, static_dir, port)` builds the app. All the actual
computation lives in `engines/*` (no FastAPI imports there); this module
wires HTTP requests to those functions, logs every call to the work log,
and serves the built frontend.
"""
from __future__ import annotations

import base64
import csv
import io
import logging
import re
import threading
import time
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Literal, Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import backend as backend_mod
from . import db
from .engines import ask as ask_engine
from .engines import calc, charts, dates, sandbox, stats, symbolic, units
from .engines.calc import CalcError
from .engines.data import Catalog, DataError, SQLGateError, is_numeric_type
from .engines.safe_ast import UnsafeExpressionError
from .engines.stats import StatsError
from .engines.symbolic import SymbolicError
from .engines.units import UnitsError
from .engines.dates import DateError
from .engines.charts import ChartError
from .hoard_link import Link
from .hoard_link import family, tokens
from .hoard_link.guard import install_guard
from .hoard_link.copilot import install_copilot

__version__ = "0.1.0"
SERVICE_SLUG = "laplaces-hoard"
DISPLAY_NAME = "Laplace's Hoard"

_ENGINE_ERRORS = (
    UnsafeExpressionError, CalcError, SymbolicError, UnitsError, DateError,
    StatsError, DataError, SQLGateError, ChartError,
)


def _error_code(exc: Exception) -> str:
    name = type(exc).__name__
    name = name.removesuffix("Error") or name
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s2 = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1)
    return s2.lower() or "error"


log = logging.getLogger("laplaces_hoard")


def _setup_logging(data_dir: Path) -> None:
    """Rotating log at <data>/logs/app.log: call summaries and errors, never file contents."""
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    target = str((log_dir / "app.log").resolve())
    for h in log.handlers:
        if isinstance(h, RotatingFileHandler) and h.baseFilename == target:
            return
    handler = RotatingFileHandler(target, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ()) if x != "body")
        parts.append(f"{loc or 'body'}: {err.get('msg', 'invalid')}")
    return "invalid arguments: " + "; ".join(parts)


def _calc(expression: str, precision: int = 15) -> dict:
    """calc runs in the timeout worker: `9**9**9**9` must not pin a server thread."""
    return sandbox.run("calc", {"expression": expression, "precision": precision}, error_cls=CalcError)


def _units(op: str, **payload: Any) -> dict:
    """Pint evaluates `**` too ("10**10**10 m"), so units share the same worker."""
    return sandbox.run(f"units.{op}", payload, error_cls=UnitsError)


def _dataset_brief(d: dict) -> dict:
    """data_list entry: enough to pick a dataset, small enough for a 27B context."""
    cols = [c["name"] for c in d.get("columns", [])]
    out = {"name": d["name"], "kind": d["kind"], "row_count": d["row_count"],
           "column_count": len(cols), "columns": cols[:30], "source_path": d["source_path"]}
    if len(cols) > 30:
        out["columns_truncated"] = True
    return out


class AppState:
    def __init__(self, data_dir: Path, link_factory: Optional[Callable[[], Link]] = None):
        self.data_dir = data_dir
        self.conn = db.connect(data_dir)
        self._catalog = None
        self._catalog_lock = threading.Lock()
        # One Link per app, closed on shutdown. The factory is also what
        # "save config" and "re-check" use to rebuild it, so a test's
        # factory (a Link backed by httpx.MockTransport) keeps every
        # rebuilt Link offline too.
        self.link_factory = link_factory or (lambda: backend_mod.load_link(data_dir))
        self.link = self.link_factory()

    @property
    def catalog(self):
        with self._catalog_lock:
            if self._catalog is None:
                self._catalog = Catalog(self.data_dir)
            return self._catalog

    async def rebuild_link(self) -> None:
        """A fresh Link: picks up a saved config and starts with an empty probe cache."""
        old, self.link = self.link, self.link_factory()
        await old.aclose()


# ---------------------------------------------------------------------- #
# request bodies
#
# Defined at module level (not inside create_app) so FastAPI can resolve
# their string annotations under `from __future__ import annotations` —
# a class local to a function is invisible to `typing.get_type_hints`,
# which silently makes FastAPI treat the parameter as a query field
# instead of the JSON body.
# ---------------------------------------------------------------------- #


class CalcBody(BaseModel):
    expression: str
    precision: int = 15


class MathBody(BaseModel):
    operation: str
    expression: Optional[str] = None
    expressions: Optional[list[str]] = None
    variable: Optional[str] = None
    variables: Optional[list[str]] = None
    domain: str = "real"
    order: Optional[int] = None
    lower: Optional[Any] = None
    upper: Optional[Any] = None
    point: Optional[Any] = None
    direction: Optional[str] = None
    x0: Optional[float] = None
    matrix_op: Optional[str] = None
    matrix: Optional[list[list[Union[str, int, float]]]] = None
    matrix2: Optional[list[list[Union[str, int, float]]]] = None
    function: Optional[str] = None
    timeout: float = Field(default=10.0, ge=0.1, le=60.0)


class UnitsConvertBody(BaseModel):
    quantity: str
    to: str


class UnitsCheckBody(BaseModel):
    expression: str


class StatsBody(BaseModel):
    test: str
    data: Optional[list[Any]] = None
    data2: Optional[list[Any]] = None
    dataset: Optional[str] = None
    column: Optional[str] = None
    column2: Optional[str] = None
    group_by: Optional[str] = None
    where: Optional[str] = None
    mu: float = 0.0
    confidence: float = 0.95
    successes: Optional[int] = None
    trials: Optional[int] = None
    p0: float = 0.5


class DateCalcBody(BaseModel):
    operation: str
    start: Optional[str] = None
    end: Optional[str] = None
    value: Optional[str] = None
    unit: str = "days"
    days: int = 0
    weeks: int = 0
    months: int = 0
    years: int = 0
    country: str = "ES"
    subdivision: Optional[str] = None  # ES without a subdivision means Madrid (MD)
    include_end: bool = True
    birth_date: Optional[str] = None
    on: Optional[str] = None
    from_tz: Optional[str] = None
    to_tz: Optional[str] = None
    text: Optional[str] = None


class DataRegisterBody(BaseModel):
    path: str
    name: Optional[str] = None
    options: Optional[dict] = None
    target: Literal["laplace", "nightingale"] = "laplace"


class DataListBody(BaseModel):
    target: Literal["laplace", "nightingale"] = "laplace"


class DataDescribeBody(BaseModel):
    name: str
    target: Literal["laplace", "nightingale"] = "laplace"


class DataQueryBody(BaseModel):
    sql: str
    limit: int = 50
    target: Literal["laplace", "nightingale"] = "laplace"


class ExportCsvBody(BaseModel):
    sql: str
    # A Spanish Excel expects ';' between fields (it treats ',' as the
    # decimal point) and a comma decimal point in numbers; opening a plain
    # ',' + '.' CSV there puts everything in one column.
    lang: Literal["en", "es"] = "en"


class DataChartBody(BaseModel):
    sql: str
    kind: str
    x: str
    y: Optional[str] = None
    color: Optional[str] = None
    title: Optional[str] = None
    include_image: bool = False


class ReportChartBody(BaseModel):
    kind: str
    x: str
    y: Optional[str] = None
    color: Optional[str] = None
    title: Optional[str] = None


class DataReportBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    sql: str = Field(..., min_length=1)
    question: Optional[str] = Field(None, max_length=1000)
    chart: Optional[ReportChartBody] = None
    limit: int = Field(20, ge=1, le=50)


class ReportRerunBody(BaseModel):
    id: str


class WorkLogQuery(BaseModel):
    limit: int = 10
    engine: Optional[str] = None
    query: Optional[str] = None


class CellBody(BaseModel):
    engine: str
    input: str


class EmptyBody(BaseModel):
    pass


class ObjectBody(BaseModel):
    id: str


class CellIdBody(BaseModel):
    id: int = Field(gt=0)


class CellPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Optional[Literal["calc", "math", "units", "dates"]] = None
    input: Optional[str] = None


class CellPatchBody(CellIdBody):
    patch: CellPatch


class ContextBody(BaseModel):
    kind: str = ""
    object_id: str = ""


class CapabilityOverride(BaseModel):
    # Only what the Settings form edits; "" clears a field. Anything else
    # (e.g. a `command` to run) is rejected rather than written to disk.
    model_config = ConfigDict(extra="forbid")
    url: Optional[str] = None
    model: Optional[str] = None


class BackendConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    faustus_url: Optional[str] = None
    faustus_token: Optional[str] = None
    only_resident: Optional[bool] = None
    capabilities: Optional[dict[Literal["llm"], CapabilityOverride]] = None


class AskBody(BaseModel):
    question: str
    datasets: list[str] = Field(default_factory=list)


def create_app(
    data_dir: Path,
    static_dir: Optional[Path] = None,
    port: int = 8812,
    link_factory: Optional[Callable[[], Link]] = None,
) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    state = AppState(data_dir, link_factory=link_factory)

    _setup_logging(data_dir)
    # start the computation worker now (SymPy/Pint imports take seconds on
    # Windows) so the model's first calc call does not pay for it
    # Worker starts only on the first computation, never for discovery or UI.

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await __import__("asyncio").to_thread(sandbox.shutdown)
            if state._catalog is not None:
                state._catalog.close()
            state.conn.close()
            await state.link.aclose()
            for handler in list(log.handlers):
                if getattr(handler, "baseFilename", None) == str((data_dir / "logs" / "app.log").resolve()):
                    log.removeHandler(handler)
                    handler.close()

    app = FastAPI(title=DISPLAY_NAME, version=__version__, lifespan=_lifespan)
    # The shared request guard (loopback Host, Origin and Fetch Metadata rules; LAPLACE_ALLOWED_HOSTS opens a LAN name or
    # a tailnet). strict_ports keeps the old rule that the Host names this app's own port.
    install_guard(app, port_getter=lambda: port, allowed_env="LAPLACE_ALLOWED_HOSTS", strict_ports=True)
    app.state.lh = state

    # The per-tool routes /api/agent/<tool> run the assistant's tools: they need the same bearer token as
    # POST /api/agent/call (data/mcp-token), which the MCP adapter sends. /api/ui/<tool> (the web UI) stays open.
    token_file = data_dir / "mcp-token"
    tokens.read_or_create_token(token_file)

    def require_agent_token(request: Request) -> None:
        if not tokens.check_bearer(request.headers.get("authorization"), tokens.read_token(token_file) or ""):
            raise HTTPException(status_code=401, detail={"error": "unauthorized", "message": "Missing or invalid MCP token (see data/mcp-token)."})

    # Errors always come back as {"error": "<code>", "message": "<actionable text>"}.
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            body = detail
        else:
            body = {"error": "http_error" if exc.status_code != 404 else "not_found", "message": str(detail)}
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse({"error": "invalid_arguments", "message": _validation_message(exc)}, status_code=422)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _log_failure(engine: str, operation: str, input_data: Any, source: str, start: float, exc: Exception):
        """Log a failed operation in the work log and raise the JSON error the API answers with."""
        elapsed = (time.monotonic() - start) * 1000
        if isinstance(exc, _ENGINE_ERRORS):
            db.log_computation(
                state.conn, engine=engine, operation=operation, input_data=input_data,
                output_data=None, ok=False, error=str(exc), elapsed_ms=elapsed, source=source,
            )
            log.info("%s %s.%s error %s", source, engine, operation, _error_code(exc))
            raise HTTPException(status_code=400, detail={"error": _error_code(exc), "message": str(exc)}) from exc
        # an engine bug must still answer in JSON and be logged
        message = f"{type(exc).__name__}: {exc}"[:500]
        db.log_computation(
            state.conn, engine=engine, operation=operation, input_data=input_data,
            output_data=None, ok=False, error=message, elapsed_ms=elapsed, source=source,
        )
        log.exception("%s %s.%s failed unexpectedly", source, engine, operation)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"unexpected failure in {engine}.{operation}: {message}"},
        ) from exc

    def _log_success(engine: str, operation: str, input_data: Any, source: str, start: float, output: Any):
        elapsed = (time.monotonic() - start) * 1000
        # "_chart_path" goes to its own column; "_response_extra" (PNG bytes,
        # full chart spec) is returned but never written to the work log.
        chart_path = output.get("_chart_path") if isinstance(output, dict) else None
        extra = output.get("_response_extra", {}) if isinstance(output, dict) else {}
        loggable = {k: v for k, v in output.items() if not k.startswith("_")} if isinstance(output, dict) else output
        cid = db.log_computation(
            state.conn, engine=engine, operation=operation, input_data=input_data,
            output_data=loggable, ok=True, error=None, elapsed_ms=elapsed, source=source,
            chart_path=chart_path,
        )
        if isinstance(output, dict):
            return {"id": cid, "cite": f"[{cid}]", **loggable, **extra}
        return {"id": cid, "cite": f"[{cid}]", "result": output}

    def _record(engine: str, operation: str, input_data: Any, source: str, fn):
        start = time.monotonic()
        try:
            output = fn()
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - _log_failure answers every failure in JSON
            _log_failure(engine, operation, input_data, source, start, exc)
        return _log_success(engine, operation, input_data, source, start, output)

    def ui(engine: str, operation: str, input_data: Any, fn):
        return _record(engine, operation, input_data, "ui", fn)

    async def _arecord(engine: str, operation: str, input_data: Any, source: str, fn):
        """Async twin of `_record`, for the one feature (`ask`) whose engine call must be awaited."""
        start = time.monotonic()
        try:
            output = await fn()
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - _log_failure answers every failure in JSON
            _log_failure(engine, operation, input_data, source, start, exc)
        return _log_success(engine, operation, input_data, source, start, output)

    # ------------------------------------------------------------------ #
    # health
    # ------------------------------------------------------------------ #

    @app.get("/api/health")
    def health():
        n_computations = state.conn.execute("SELECT COUNT(*) AS n FROM computations").fetchone()["n"]
        n_datasets = state._catalog.dataset_count() if state._catalog is not None else None
        return {
            "service": SERVICE_SLUG,
            "name": DISPLAY_NAME,
            "version": __version__,
            "status": "ok",
            "computations_logged": n_computations,            "hoard_link": family.health_block(),

            "datasets_registered": n_datasets,
        }

    # ------------------------------------------------------------------ #
    # shared model backend (Hoard Link): Settings -> Models panel
    # ------------------------------------------------------------------ #

    @app.get("/api/backend")
    async def get_backend():
        status = await state.link.status()
        return {
            "capabilities": {cap: status[cap] for cap in backend_mod.USED_CAPABILITIES},
            "config": {
                "only_resident": state.link.config.only_resident,
                "faustus_urls": list(state.link.config.faustus_urls),
                "token_set": backend_mod.token_set(state.data_dir),
                # what data/backend.json holds (never the token), so the form can show and clear it
                "saved": backend_mod.saved_overrides(state.data_dir),
                "error": backend_mod.config_error(state.data_dir),
            },
            "app": backend_mod.app_backends(),
        }

    @app.put("/api/backend/config")
    async def put_backend_config(body: BackendConfigBody):
        backend_mod.save_config(
            state.data_dir, faustus_url=body.faustus_url, faustus_token=body.faustus_token,
            only_resident=body.only_resident,
            capabilities={cap: o.model_dump(exclude_unset=True) for cap, o in (body.capabilities or {}).items()},
        )
        # rebuild the Link so the new config applies immediately, without restarting the app
        await state.rebuild_link()
        return {"ok": True, "token_set": backend_mod.token_set(state.data_dir)}

    @app.post("/api/backend/recheck")
    async def recheck_backend():
        # a fresh Link has an empty probe cache, which is what "re-check now" means
        await state.rebuild_link()
        status = await state.link.status()
        return {cap: status[cap] for cap in backend_mod.USED_CAPABILITIES}

    # ------------------------------------------------------------------ #
    # tool operations: POST /api/agent/<tool> (the MCP adapter, logged as
    # source "agent") and the identical POST /api/ui/<tool> (the web UI,
    # logged as "ui") — so "Assistant activity" only ever shows the model.
    # ------------------------------------------------------------------ #

    def _mount_tools(prefix: str, source: str, dependencies: Optional[list] = None) -> None:
        def rec(engine: str, operation: str, input_data: Any, fn):
            return _record(engine, operation, input_data, source, fn)

        @app.post(f"{prefix}/notebook_cells", name=f"{source}_notebook_cells", dependencies=dependencies)
        def tool_notebook_cells(body: EmptyBody):
            return {"cells": db.list_cells(state.conn)}

        @app.post(f"{prefix}/notebook_cell_get", name=f"{source}_notebook_cell_get", dependencies=dependencies)
        def tool_notebook_cell_get(body: CellIdBody):
            return _cell_or_error(body.id)

        @app.post(f"{prefix}/notebook_cell_create", name=f"{source}_notebook_cell_create", dependencies=dependencies)
        def tool_notebook_cell_create(body: CellBody):
            return rec("notebook", "create", body.model_dump(),
                       lambda: _evaluate_cell(db.add_cell(state.conn, engine=body.engine, input_text=body.input), source))

        @app.post(f"{prefix}/notebook_cell_patch", name=f"{source}_notebook_cell_patch", dependencies=dependencies)
        def tool_notebook_cell_patch(body: CellPatchBody):
            patch = body.patch.model_dump(exclude_unset=True, exclude_none=True)
            def apply():
                cell = db.patch_cell(state.conn, body.id, patch)
                if cell is None:
                    raise DataError("no such notebook cell; read notebook_cells for available ids")
                return _evaluate_cell(cell, source) if patch else cell
            return rec("notebook", "patch", body.model_dump(exclude_none=True), apply)

        @app.post(f"{prefix}/notebook_cell_delete", name=f"{source}_notebook_cell_delete", dependencies=dependencies)
        def tool_notebook_cell_delete(body: CellIdBody):
            return rec("notebook", "delete", body.model_dump(),
                       lambda: {"deleted": db.delete_cell(state.conn, body.id), "cell_id": body.id})

        @app.post(f"{prefix}/computation_get", name=f"{source}_computation_get", dependencies=dependencies)
        def tool_computation_get(body: ObjectBody):
            def read():
                item = db.get_computation(state.conn, body.id)
                if item is None:
                    raise HTTPException(404, detail={"error": "not_found", "message": "No such computation; read work_log for available ids."})
                return item
            return read()

        @app.post(f"{prefix}/app_context", name=f"{source}_app_context", dependencies=dependencies)
        def tool_app_context(body: ContextBody):
            def resolve():
                objects = []
                if body.object_id:
                    item = None
                    if body.kind == "cell" and body.object_id.isdecimal():
                        item = db.get_cell(state.conn, int(body.object_id))
                    elif body.kind == "computation":
                        item = db.get_computation(state.conn, body.object_id)
                    elif body.kind == "dataset":
                        item = next((d for d in state.catalog.list_datasets() if d["name"] == body.object_id), None)
                    if item:
                        reference_id = str(item["name"] if body.kind == "dataset" else item["id"])
                        title = item.get("name") or item.get("input")
                        if isinstance(title, dict):
                            title = title.get("expression") or item.get("operation")
                        objects.append({"kind": body.kind, "object_id": reference_id,
                                        "title": str(title or reference_id)})
                return {"project": None, "active_objects": objects}
            return resolve()

        @app.post(f"{prefix}/calc", name=f"{source}_calc", dependencies=dependencies)
        def tool_calc(body: CalcBody):
            return rec("calc", "compute", body.model_dump(exclude_defaults=True), lambda: _calc(body.expression, body.precision))

        @app.post(f"{prefix}/math", name=f"{source}_math", dependencies=dependencies)
        def tool_math(body: MathBody):
            payload = body.model_dump(exclude_none=True)
            op = payload.pop("operation")
            timeout = payload.pop("timeout", 10.0)
            if "expressions" not in payload and "expression" in payload and op == "solve":
                payload["expressions"] = [payload.pop("expression")]
            return rec("math", op, body.model_dump(exclude_defaults=True), lambda: symbolic.run(op, timeout=timeout, **payload))

        @app.post(f"{prefix}/units_convert", name=f"{source}_units_convert", dependencies=dependencies)
        def tool_units_convert(body: UnitsConvertBody):
            return rec("units", "convert", body.model_dump(),
                       lambda: _units("convert", quantity=body.quantity, to=body.to))

        @app.post(f"{prefix}/stats", name=f"{source}_stats", dependencies=dependencies)
        def tool_stats(body: StatsBody):
            payload = body.model_dump(exclude={"test"})
            return rec("stats", body.test, body.model_dump(exclude_defaults=True),
                       lambda: stats.run(body.test, catalog=state.catalog if body.dataset else None, **payload))

        @app.post(f"{prefix}/date_calc", name=f"{source}_date_calc", dependencies=dependencies)
        def tool_date_calc(body: DateCalcBody):
            return rec("dates", body.operation, body.model_dump(exclude_defaults=True), lambda: _dispatch_date(body))

        def workbench(tool: str, args: dict) -> dict:
            native_call = getattr(state, "workbench_call", None)
            if native_call is not None:
                return {"target": "nightingale", **native_call(tool, args)}
            response = family.call("nightingale", tool, args)
            if not response.get("ok"):
                raise DataError("Nightingale is unavailable or refused the request: " + str(response.get("error") or "unknown error"))
            result = response.get("result")
            if not isinstance(result, dict):
                raise DataError("Nightingale returned an unexpected result")
            return {"target": "nightingale", **result}

        @app.post(f"{prefix}/data_list", name=f"{source}_data_list", dependencies=dependencies)
        def tool_data_list(body: DataListBody = DataListBody()):
            return rec("data", "list", body.model_dump(),
                       lambda: workbench("data_list", {}) if body.target == "nightingale"
                       else {"datasets": [_dataset_brief(d) for d in state.catalog.list_datasets()]})

        @app.post(f"{prefix}/data_register", name=f"{source}_data_register", dependencies=dependencies)
        def tool_data_register(body: DataRegisterBody):
            return rec("data", "register", body.model_dump(),
                       lambda: workbench("data_ingest", {"kind": "folder" if Path(body.path).is_dir() else "file",
                                                              "path": body.path, "name": body.name,
                                                              "options": body.options or {}})
                       if body.target == "nightingale" else state.catalog.register(body.path, body.name, body.options))

        @app.post(f"{prefix}/data_describe", name=f"{source}_data_describe", dependencies=dependencies)
        def tool_data_describe(body: DataDescribeBody):
            return rec("data", "describe", body.model_dump(),
                       lambda: workbench("data_profile", {"dataset": body.name})
                       if body.target == "nightingale" else state.catalog.describe(body.name))

        @app.post(f"{prefix}/data_query", name=f"{source}_data_query", dependencies=dependencies)
        def tool_data_query(body: DataQueryBody):
            return rec("data", "query", body.model_dump(exclude_defaults=True),
                       lambda: workbench("data_query", {"sql": body.sql, "limit": body.limit})
                       if body.target == "nightingale" else state.catalog.query(body.sql, body.limit))

        @app.post(f"{prefix}/data_chart", name=f"{source}_data_chart", dependencies=dependencies)
        def tool_data_chart(body: DataChartBody):
            return rec("data", "chart", body.model_dump(exclude_defaults=True), lambda: _chart(body, include_spec=(source == "ui")))

        @app.post(f"{prefix}/data_report", name=f"{source}_data_report", dependencies=dependencies)
        def tool_data_report(body: DataReportBody):
            return rec("report", "create", body.model_dump(exclude_none=True), lambda: _report(body))

        @app.post(f"{prefix}/report_rerun", name=f"{source}_report_rerun", dependencies=dependencies)
        def tool_report_rerun(body: ReportRerunBody):
            item = db.get_computation(state.conn, body.id)
            if item is None or item["engine"] != "report" or not item["ok"]:
                raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"no saved report {body.id}"})
            report = DataReportBody.model_validate(item["input"])
            return rec("report", "rerun", {**report.model_dump(exclude_none=True), "parent_id": body.id},
                       lambda: _report(report))

    def _chart(body: "DataChartBody", include_spec: bool) -> dict:
        out_path = state.data_dir / "charts" / f"chart_{time.time_ns()}.png"
        result = charts.build_chart(
            state.catalog, body.sql, body.kind, body.x, body.y, body.color, body.title, out_path=out_path
        )
        spec = result["spec"]
        # The PNG is always rendered and saved (so the Work log can show it
        # later, from any source), but only base64-attached to the response
        # when actually requested: an unrequested image in a tool result can
        # crash a text-only local model mid-turn.
        extra: dict[str, Any] = {}
        if body.include_image:
            extra["png_base64"] = base64.b64encode(result["png_bytes"]).decode("ascii")
        if include_spec:
            extra["spec"] = spec  # the UI re-renders it interactively
        return {
            "kind": body.kind,
            "spec_summary": {
                "mark": spec["mark"]["type"],
                "encoding": {k: v.get("field") or v.get("aggregate") for k, v in spec["encoding"].items()},
                "title": body.title,
            },
            "row_count": result["row_count"],
            "total_rows": result.get("total_rows"),
            "truncated": result["truncated"],
            "png_bytes": len(result["png_bytes"]),
            "_chart_path": str(out_path),
            "_response_extra": extra,
        }

    def _report(body: DataReportBody) -> dict:
        """One work-log artifact with the query, result and optional saved chart."""
        result = state.catalog.query(body.sql, body.limit)
        report: dict[str, Any] = {
            "title": body.title, "question": body.question, "sql": body.sql,
            "columns": result["columns"], "rows": result["rows"],
            "row_count": result["row_count"], "total_rows": result["total_rows"],
            "truncated": result["truncated"],
        }
        if body.chart is not None:
            spec = body.chart
            out_path = state.data_dir / "charts" / f"report_{time.time_ns()}.png"
            chart = charts.build_chart(state.catalog, body.sql, spec.kind, spec.x, spec.y,
                                       spec.color, spec.title or body.title, out_path=out_path)
            report["chart"] = {"kind": spec.kind, "x": spec.x, "y": spec.y,
                               "color": spec.color, "row_count": chart["row_count"]}
            report["_chart_path"] = str(out_path)
        return report

    _mount_tools("/api/agent", "agent", dependencies=[Depends(require_agent_token)])
    _mount_tools("/api/ui", "ui")

    @app.post("/api/agent/work_log", dependencies=[Depends(require_agent_token)])
    def agent_work_log(body: WorkLogQuery):
        def _run():
            limit = max(1, min(body.limit, 50))
            query = (body.query or "").strip() or None
            if query and re.fullmatch(r"\[?L-\d{6}\]?", query):
                item = db.get_computation(state.conn, query.strip("[]"))
                if item is None:
                    raise DataError(f"no computation with id {query}")
                return {"items": [db.brief(item, full=True)], "count": 1, "has_more": False}
            rows = db.list_computations(state.conn, limit=limit + 1, engine=body.engine, query=query,
                                        exclude_engine=None if body.engine else "log")
            return {"items": [db.brief(r) for r in rows[:limit]], "count": min(len(rows), limit),
                    "has_more": len(rows) > limit}
        return _record("log", "search", body.model_dump(exclude_none=True), "agent", _run)

    # ------------------------------------------------------------------ #
    # UI-facing richer endpoints
    # ------------------------------------------------------------------ #

    @app.get("/api/log")
    def get_log(limit: int = 20, engine: Optional[str] = None, source: Optional[str] = None, query: Optional[str] = None):
        return {"items": db.list_computations(state.conn, limit=limit, engine=engine, query=query, source=source)}

    @app.get("/api/log/{cid}")
    def get_log_item(cid: str):
        item = db.get_computation(state.conn, cid)
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"no computation {cid}"})
        return item

    @app.get("/api/charts/{cid}")
    def get_chart_image(cid: str):
        """The PNG a data_chart call saved, so the Work log can show it even
        when the call itself did not request the image (see data_chart's
        include_image)."""
        item = db.get_computation(state.conn, cid)
        chart_path = item.get("chart_path") if item else None
        if not chart_path:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"no chart for {cid}"})
        path = Path(chart_path)
        if not path.is_file():
            raise HTTPException(
                status_code=404, detail={"error": "not_found", "message": "the chart image file is missing"}
            )
        return FileResponse(path, media_type="image/png")

    @app.post("/api/log/{cid}/rerun")
    def rerun_log_item(cid: str):
        item = db.get_computation(state.conn, cid)
        if item is None:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"no computation {cid}"})
        engine_name = item["engine"]
        input_data = item["input"] or {}
        dispatch = {
            "calc": lambda: _calc(input_data.get("expression", ""), input_data.get("precision", 15)),
            "units": lambda: _rerun_units(item["operation"], input_data),
            "dates": lambda: _dispatch_date_dict(item["operation"], input_data),
            "data": lambda: _rerun_data(item["operation"], input_data),
        }
        if engine_name == "math":
            payload = dict(input_data)
            op = payload.pop("operation", item["operation"])
            timeout = payload.pop("timeout", 10.0)
            payload = {k: v for k, v in payload.items() if v is not None}
            if op == "solve" and "expressions" not in payload and "expression" in payload:
                payload["expressions"] = [payload.pop("expression")]
            fn = lambda: symbolic.run(op, timeout=timeout, **payload)  # noqa: E731
        elif engine_name == "stats":
            payload = {k: v for k, v in input_data.items() if k != "test" and v is not None}
            fn = lambda: stats.run(input_data.get("test", item["operation"]), catalog=state.catalog, **payload)  # noqa: E731
        elif engine_name == "report":
            fn = lambda: _report(DataReportBody.model_validate(input_data))  # noqa: E731
        elif engine_name in dispatch:
            fn = dispatch[engine_name]
        else:
            raise HTTPException(status_code=400, detail={"error": "not_rerunnable", "message": f"engine {engine_name} cannot be re-run"})
        return ui(engine_name, item["operation"], input_data, fn)

    @app.post("/api/export/csv")
    def export_csv(body: ExportCsvBody):
        """The complete result of a gated read-only query as CSV (the UI grid shows at most 1000 rows)."""
        holder: dict[str, Any] = {}

        def _run():
            r = state.catalog.query_all(body.sql, max_rows=1_000_000)
            holder.update(r)
            return {"row_count": r["row_count"], "truncated": r["truncated"]}

        _record("data", "export_csv", {"sql": body.sql, "lang": body.lang}, "ui", _run)
        buf = io.StringIO()
        names = [c["name"] for c in holder["columns"]]
        # DECIMAL values (exact money amounts) arrive as text like "-1150.00";
        # they are numbers too and need the same decimal comma as a float
        numeric = {c["name"] for c in holder["columns"] if is_numeric_type(c["type"])}
        es = body.lang == "es"
        writer = csv.writer(buf, delimiter=";" if es else ",", lineterminator="\n")
        writer.writerow(names)
        for row in holder["rows"]:
            out_row = []
            for n in names:
                v = row[n]
                if v is None:
                    out_row.append("")
                elif es and (isinstance(v, float) or (n in numeric and isinstance(v, str))):
                    out_row.append(str(v).replace(".", ","))
                else:
                    out_row.append(v)
            writer.writerow(out_row)
        # BOM so Excel on Windows opens UTF-8 (accents) correctly
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="query.csv"'})

    @app.post("/api/ui/data_ask", name="ui_data_ask")
    async def ui_data_ask(body: AskBody):
        """"Ask your data" (Data screen, UI only - the agent already writes SQL itself via data_query)."""
        async def _run():
            return await ask_engine.ask(state.catalog, state.link, body.question, body.datasets)
        return await _arecord("data", "ask", body.model_dump(exclude_defaults=True), "ui", _run)

    @app.get("/api/datasets")
    def list_datasets():
        return {"datasets": state.catalog.list_datasets()}

    @app.get("/api/datasets/{name}")
    def describe_dataset(name: str):
        return ui("data", "describe", {"name": name}, lambda: state.catalog.describe(name))

    @app.post("/api/units/check")
    def units_check(body: UnitsCheckBody):
        return ui("units", "check", body.model_dump(), lambda: _units("check", expression=body.expression))

    @app.get("/api/units/compatible")
    def units_compatible(unit: str):
        return ui("units", "compatible", {"unit": unit}, lambda: _units("compatible", unit=unit))

    @app.get("/api/agent-calls")
    def agent_calls(limit: int = 20):
        return {"items": db.list_computations(state.conn, limit=limit, source="agent")}

    @app.get("/api/cells")
    def list_cells():
        return {"cells": db.list_cells(state.conn)}

    @app.post("/api/cells")
    def create_cell(body: CellBody):
        cell = db.add_cell(state.conn, engine=body.engine, input_text=body.input)
        try:
            result = _run_cell(body.engine, body.input)
            db.update_cell(state.conn, cell["id"], result=result)
            cell["result"] = result
        except _ENGINE_ERRORS as exc:
            error_result = {"error": _error_code(exc), "message": str(exc)}
            db.update_cell(state.conn, cell["id"], result=error_result)
            cell["result"] = error_result
        except HTTPException as exc:
            # the engine's own error, already turned into {error, message}
            # by ui(): store it as is, not as an "unexpected failure"
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            error_result = {"error": str(detail.get("error", "error")),
                            "message": str(detail.get("message", exc.detail))}
            db.update_cell(state.conn, cell["id"], result=error_result)
            cell["result"] = error_result
        except Exception as exc:  # noqa: BLE001 - a cell must never come back as a raw 500
            log.exception("notebook cell (%s) failed unexpectedly: %r", body.engine, body.input)
            error_result = {
                "error": "internal_error",
                "message": f"unexpected failure evaluating this cell: {type(exc).__name__}: {exc}"[:500],
            }
            db.update_cell(state.conn, cell["id"], result=error_result)
            cell["result"] = error_result
        return cell

    @app.delete("/api/cells/{cell_id}")
    def remove_cell(cell_id: int):
        ok = db.delete_cell(state.conn, cell_id)
        if not ok:
            raise HTTPException(status_code=404, detail={"error": "not_found", "message": "no such cell"})
        return {"deleted": True}

    def _cell_or_error(cell_id: int):
        cell = db.get_cell(state.conn, cell_id)
        if cell is None:
            raise HTTPException(404, detail={"error": "not_found", "message": "No such cell; read notebook_cells for available ids."})
        return cell

    def _evaluate_cell(cell: dict, source: str):
        try:
            result = _run_cell(cell["engine"], cell["input"], source)
        except _ENGINE_ERRORS as exc:
            result = {"error": _error_code(exc), "message": str(exc)}
        except HTTPException as exc:
            result = exc.detail if isinstance(exc.detail, dict) else {"error": "cell_error", "message": str(exc.detail)}
        except Exception as exc:
            log.exception("notebook evaluation failed")
            result = {"error": "internal_error", "message": f"{type(exc).__name__}: {exc}"}
        return db.finish_cell(state.conn, cell, result) or {"cell_id": cell["id"], "deleted": True}

    def _run_cell(engine_name: str, text: str, source: str = "ui") -> dict:
        def record_cell(engine, operation, input_data, fn):
            return _record(engine, operation, input_data, source, fn)
        if engine_name == "calc":
            return record_cell("calc", "compute", {"expression": text}, lambda: _calc(text))
        if engine_name == "math":
            op, payload = symbolic.parse_cell(text)
            return record_cell("math", op, {"operation": op, **payload}, lambda: symbolic.run(op, **payload))
        if engine_name == "units":
            for sep in ("->", "→", " to ", " en "):
                if sep in text:
                    q, to = text.split(sep, 1)
                    return record_cell("units", "convert", {"quantity": q.strip(), "to": to.strip()},
                              lambda: _units("convert", quantity=q.strip(), to=to.strip()))
            return record_cell("units", "check", {"expression": text}, lambda: _units("check", expression=text))
        if engine_name == "dates":
            return record_cell("dates", "parse", {"text": text}, lambda: dates.parse(text))
        raise HTTPException(status_code=400, detail={"error": "unknown_engine", "message": f"unknown cell engine {engine_name}"})

    def _dispatch_date(body: "DateCalcBody") -> dict:
        return _dispatch_date_dict(body.operation, body.model_dump())

    def _dispatch_date_dict(operation: str, d: dict) -> dict:
        if operation == "diff":
            return dates.diff(d.get("start"), d.get("end"), d.get("unit") or "days")
        if operation == "add":
            return dates.add(d.get("start"), days=d.get("days") or 0, weeks=d.get("weeks") or 0,
                             months=d.get("months") or 0, years=d.get("years") or 0)
        if operation == "business_days":
            return dates.business_days(
                d.get("start"), d.get("end"), country=d.get("country") or "ES",
                subdivision=d.get("subdivision"), include_end=d.get("include_end", True),
            )
        if operation == "weekday":
            return dates.weekday(d.get("value") or d.get("start"))
        if operation == "iso_week":
            return dates.iso_week(d.get("value") or d.get("start"))
        if operation == "age":
            return dates.age(d.get("birth_date") or d.get("start"), on=d.get("on") or d.get("end"))
        if operation == "convert_tz":
            return dates.convert_tz(d.get("value") or d.get("start"), from_tz=d.get("from_tz"), to_tz=d.get("to_tz"))
        if operation == "parse":
            return dates.parse(d.get("text") or d.get("value") or "")
        raise DateError(
            f"unknown date operation: {operation}; choose diff, add, business_days, weekday, iso_week, age, convert_tz or parse"
        )

    def _rerun_units(operation: str, input_data: dict) -> dict:
        if operation == "check":
            return _units("check", expression=input_data.get("expression", ""))
        if operation == "compatible":
            return _units("compatible", unit=input_data.get("unit", ""))
        return _units("convert", quantity=input_data.get("quantity", ""), to=input_data.get("to", ""))

    def _rerun_data(operation: str, input_data: dict) -> dict:
        if operation == "query":
            return state.catalog.query(input_data.get("sql", ""), input_data.get("limit", 50))
        if operation == "describe":
            return state.catalog.describe(input_data.get("name", ""))
        if operation == "list":
            return {"datasets": state.catalog.list_datasets()}
        raise DataError(f"cannot re-run data operation: {operation}")

    # ------------------------------------------------------------------ #
    # static frontend
    # ------------------------------------------------------------------ #

    if static_dir is not None and Path(static_dir).is_dir():
        static_root = Path(static_dir).resolve()
        index_path = static_root / "index.html"

        if (static_root / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=static_root / "assets"), name="assets")

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail={"error": "not_found", "message": f"no API route /{full_path}"})
            if full_path:
                # Only ever serve files that resolve *inside* the built frontend:
                # "..%2f", "//etc/passwd" and Windows "..\\" must not escape it.
                candidate = (static_root / full_path).resolve()
                if candidate.is_relative_to(static_root) and candidate.is_file():
                    return FileResponse(candidate)
            return FileResponse(index_path)
    else:
        @app.get("/")
        def no_frontend():
            return HTMLResponse(
                "<html><body style='font-family:sans-serif;padding:2rem'>"
                "<h1>Laplace's Hoard</h1>"
                "<p>The frontend has not been built yet. Run:</p>"
                "<pre>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</pre>"
                "<p>API is live at <a href='/api/health'>/api/health</a>.</p>"
                "</body></html>"
            )

    # The family contract (Hoard Link 0.4): the shared GET /api/agent/tools +
    # POST /api/agent/call over the per-tool routes above (which stay as they
    # are), a bearer token in data/mcp-token, and one agent.call event per
    # call on the hub's bus. Descriptions come from mcp_server.py's docstrings
    # so the two catalogues never disagree.
    # The family contract invokes route.endpoint directly. Keep native synchronous
    # engines off its event loop, just as FastAPI does for their UI routes.
    import functools
    import inspect
    from fastapi.concurrency import run_in_threadpool
    for route in app.routes:
        if getattr(route, "path", "").startswith("/api/agent/") and not inspect.iscoroutinefunction(route.endpoint):
            original = route.endpoint
            def off_loop(endpoint):
                @functools.wraps(endpoint)
                async def run(*args, **kwargs):
                    return await run_in_threadpool(endpoint, *args, **kwargs)
                return run
            route.endpoint = off_loop(original)

    family.install_fastapi(app, "laplace", str(data_dir),
                           mcp_source=str(Path(__file__).with_name("mcp_server.py")))
    install_copilot(app, "laplace", data_dir, app_name=DISPLAY_NAME,
                    config_path=None if backend_mod.config_error(data_dir) else data_dir / backend_mod.BACKEND_FILE)

    return app
