"""One ASGI process with explicit, independently owned module lifetimes.

Catalog discovery never imports an engine. A disabled module has no database
connection, worker, collector, watcher or listening socket.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StrictBool


class Toggle(BaseModel):
    enabled: StrictBool


class Module:
    def __init__(self, spec, data_dir, factory):
        self.spec = spec
        self.data_dir = Path(data_dir)
        self.factory = factory
        self.app = None
        self.stack = None
        self.lock = asyncio.Lock()
        self.error = ""
        self.requests = {}
        self.stopping = False
        self.desired = False
        self.revision = 0

    def status(self):
        return {**self.spec, "enabled": self.app is not None,
                "data_dir": str(self.data_dir), "error": self.error}

    async def set_enabled(self, enabled, revision=None):
        async with self.lock:
            if revision is not None and revision != self.revision:
                return self.status()
            if enabled == (self.app is not None):
                return self.status()
            self.error = ""
            if enabled:
                stack = AsyncExitStack()
                try:
                    app = await asyncio.to_thread(self.factory, self.spec["id"], self.data_dir)
                    await stack.enter_async_context(app.router.lifespan_context(app))
                except Exception as exc:
                    await stack.aclose()
                    self.error = f"{type(exc).__name__}: {exc}"
                    raise
                self.app, self.stack = app, stack
            else:
                # Close before reporting OFF; calls share this lock and cannot
                # resume or queue new work while shutdown is in progress.
                self.stopping = True
                try:
                    pending = list(self.requests)
                    for task in pending:
                        if self.requests.get(task):
                            task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    await self.stack.aclose()
                finally:
                    self.stopping = False
                self.app = self.stack = None
            return self.status()

    async def request(self, method, path, **kwargs):
        async with self.lock:
            if self.app is None:
                raise HTTPException(409, detail={"error": "module_disabled", "module": self.spec["id"]})
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
                return await client.request(method, path, **kwargs)


def create_runtime(*, name, product, root, data_dir, specs, factory, port):
    root, data_dir = Path(root), Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    token_path = data_dir / "mcp-token"
    if not token_path.exists():
        token_path.write_text(secrets.token_hex(32), encoding="utf-8")
    token = token_path.read_text(encoding="utf-8").strip()
    settings_path = data_dir / "modules.json"
    saved = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    paths = saved.get("data_paths", {})
    modules = {s["id"]: Module(s, paths.get(s["id"], data_dir if s["id"] == "data" else data_dir / s["id"]), factory) for s in specs}
    for key, module in modules.items():
        module.desired = key in saved.get("enabled", [])
    native_factory = factory
    running_loop = None

    def build_native(key, path):
        instance = native_factory(key, path)
        if key == "calculation":
            def workbench_call(tool, args):
                data = modules["data"]
                if data.app is None or data.stopping:
                    from laplaces_hoard.engines.data import DataError
                    raise DataError("Datos está desactivado. Actívalo en Funciones para consultar el workbench.")
                token = (data.data_dir / "mcp-token").read_text(encoding="utf-8").strip()
                result = asyncio.run_coroutine_threadsafe(data.request("POST", "/api/agent/call",
                    json={"name": tool, "arguments": args},
                    headers={"Host": f"127.0.0.1:{port}", "Authorization": "Bearer " + token}), running_loop).result()
                if result.is_error:
                    from laplaces_hoard.engines.data import DataError
                    raise DataError(str(result.json()))
                return result.json()
            instance.state.lh.workbench_call = workbench_call
        return instance

    for module in modules.values():
        module.factory = build_native
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    by_name = {t["name"]: t for t in catalog}

    def persist():
        payload = {"enabled": [k for k, m in modules.items() if m.desired], "data_paths": paths}
        temp = settings_path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(settings_path)

    @asynccontextmanager
    async def lifespan(app):
        nonlocal running_loop
        running_loop = asyncio.get_running_loop()
        try:
            for key in saved.get("enabled", []):
                if key in modules:
                    try:
                        await modules[key].set_enabled(True)
                    except Exception:
                        pass  # The dashboard displays the module's error; others remain usable.
            yield
        finally:
            for module in reversed(list(modules.values())):
                if module.app is not None:
                    await module.set_enabled(False)

    app = FastAPI(title=name, lifespan=lifespan)
    app.state.modules = modules
    app.state.token = token
    app.state.data_dir = data_dir

    @app.middleware("http")
    async def local_request_guard(request, call_next):
        # Reuse the family's established host/origin protection.
        from nightingale.hoard_link.guard import check_request
        verdict = check_request(request.method, dict(request.headers), port, (), strict_ports=True)
        if verdict:
            return JSONResponse({"error": verdict[1]}, status_code=verdict[0])
        return await call_next(request)

    def authorize(request):
        auth = request.headers.get("authorization", "")
        if not secrets.compare_digest(auth, "Bearer " + token):
            raise HTTPException(401, "Invalid token")

    def module_for(key):
        if key not in modules:
            raise HTTPException(404, "Unknown module")
        return modules[key]

    @app.get("/api/health")
    async def health():
        return {"service": product + "-hoard", "name": name, "version": "1.0.0", "status": "healthy",
                "modules": [m.status() for m in modules.values()]}

    @app.get("/api/modules")
    async def module_list():
        return {"name": name, "modules": [m.status() for m in modules.values()]}

    @app.put("/api/modules/{key}")
    async def toggle_module(key: str, body: Toggle):
        module = module_for(key)
        module.revision += 1
        revision = module.revision
        module.desired = body.enabled
        persist()
        try:
            result = await module.set_enabled(body.enabled, revision)
        except Exception as exc:
            if module.revision == revision:
                module.desired = False
            persist()
            raise HTTPException(503, f"No se pudo activar el modulo: {exc}") from exc
        persist()
        return result

    @app.post("/api/modules/stop-all")
    async def stop_all():
        requests = []
        for module in modules.values():
            module.revision += 1
            module.desired = False
            requests.append((module, module.revision))
        persist()
        for module, revision in requests:
            await module.set_enabled(False, revision)
        persist()
        return await module_list()

    controls = [
        {"name": product + "_modules", "description": "List modules, their enabled state and data locations. Does not start engines.",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": product + "_set_module", "description": "Explicitly enable or disable one module. All modules default OFF. Enable only when the user requests this function.",
         "inputSchema": {"type": "object", "properties": {"module": {"type": "string", "enum": list(modules)}, "enabled": {"type": "boolean"}}, "required": ["module", "enabled"]}},
    ]

    @app.get("/api/agent/tools")
    async def tools():
        return {"service": product + "-hoard", "tools": controls + [{k: v for k, v in t.items() if k not in ("module", "native_name")} for t in catalog],
                "instructions": "Independent modules, all OFF on first use. Never enable a collector merely to discover its tools. Existing tool names and schemas are preserved."}

    @app.post("/api/agent/call")
    async def call(request: Request):
        authorize(request)
        return await dispatch(await request.json())

    async def dispatch(payload):
        tool, args = payload.get("name"), payload.get("arguments") or {}
        if tool == product + "_modules":
            return await module_list()
        if tool == product + "_set_module":
            return await toggle_module(args.get("module"), Toggle(enabled=args.get("enabled")))
        if tool not in by_name:
            raise HTTPException(404, "Unknown tool")
        module = modules[by_name[tool]["module"]]
        module_token_file = module.data_dir / "mcp-token"
        module_token = module_token_file.read_text(encoding="utf-8").strip() if module_token_file.exists() else ""
        payload = {**payload, "name": by_name[tool].get("native_name", tool)}
        response = await module.request("POST", "/api/agent/call", json=payload,
                                        headers={"Host": f"127.0.0.1:{port}", "Authorization": "Bearer " + module_token})
        body = response.json()
        if module.spec["id"] == "calculation" and response.is_success and isinstance(body, dict) and body.get("id"):
            native_name = payload["name"]
            if native_name == "data_chart" or native_name in ("data_report", "report_rerun") and body.get("chart"):
                body["chart_url"] = f"http://127.0.0.1:{port}/calculation/api/charts/{body['id']}"
        return JSONResponse(body, status_code=response.status_code)

    @app.get("/")
    async def index():
        return FileResponse(root / "web" / "index.html")

    from nightingale.hoard_link.copilot import install_copilot
    install_copilot(app, product, str(data_dir), app_name=name, inject=False)

    app.mount("/static", StaticFiles(directory=root / "web"), name="static")

    @app.api_route("/{key}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def module_route(key: str, path: str, request: Request):
        module = module_for(key)
        if path.startswith("api/copilot/"):
            return RedirectResponse("/" + path, status_code=307)
        if module.app is None and not path.startswith("api/"):
            return RedirectResponse("/#" + key, status_code=303)
        from .native_response import NativeResponse
        return NativeResponse(module, path, name=name, product=product, on_quit=persist)

    return app
