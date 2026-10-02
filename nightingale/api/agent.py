"""/api/agent/* — the bridge used by mcp_server.py (Bearer token from <DATA_DIR>/mcp-token). The routes, the token check, the
error envelope and the audit event are the shared agent router; this file only says how a call reaches the tools."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.concurrency import run_in_threadpool

from ..agent_tools import AGENT_INSTRUCTIONS, ASYNC_TOOLS, call_tool, call_tool_sync, tool_catalog
from ..hoard_link.agentkit import AppError, make_agent_router
from .deps import services


async def _call(name: str, arguments: dict[str, Any], request: Request) -> Any:
    svc = services(request)
    try:
        if name in ASYNC_TOOLS:
            return await call_tool(svc, name, arguments)
        return await run_in_threadpool(call_tool_sync, svc, name, arguments)  # blocking tools stay off the event loop
    except KeyError:
        raise
    except LookupError as error:  # NotFoundError: a missing dataset, model, chart...
        raise AppError("not_found", str(error)) from error


router = make_agent_router(
    tools_fn=tool_catalog,
    call_fn=_call,
    token_fn=lambda request: services(request).token,
    instructions=AGENT_INSTRUCTIONS,
    app_name="nightingale",
)
