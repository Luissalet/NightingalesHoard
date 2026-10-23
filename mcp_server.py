"""Stdio MCP bridge. Discovery reads the shipped catalogue; never starts an app."""
import asyncio
import json
import os
from pathlib import Path

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool, ImageContent

ROOT = Path(__file__).resolve().parent
PRODUCT = "nightingale"
DEFAULT_PORT = 5189
app = Server(PRODUCT + "-hoard", instructions="Nightingale's data workbench and exact calculation engines. All modules default OFF; enable explicitly only for the user's requested work. Preview transforms before applying them, list datasets before assuming names, and cite the returned N-000123 or L-000042 computation id.")


def catalog():
    tools = json.loads((ROOT / "fusion" / "catalog.json").read_text(encoding="utf-8"))
    from importlib import import_module
    specs = import_module("fusion.native").SPECS
    return [
        {"name": PRODUCT + "_modules", "description": "List modules without starting any engine.", "inputSchema": {"type": "object", "properties": {}}},
        {"name": PRODUCT + "_set_module", "description": "Enable or disable one module explicitly. All modules default OFF; enable only on the user's request.",
         "inputSchema": {"type": "object", "properties": {"module": {"type": "string", "enum": [s["id"] for s in specs]}, "enabled": {"type": "boolean"}}, "required": ["module", "enabled"]}},
    ] + [{k: v for k, v in t.items() if k not in ("module", "native_name")} for t in tools]


@app.list_tools()
async def list_tools():
    return [Tool.model_validate(t) for t in catalog()]


@app.call_tool()
async def call_tool(name, arguments):
    data_dir = Path(os.environ.get(PRODUCT.upper() + "_DATA_DIR") or ROOT / "data")
    token_file = Path(os.environ.get(PRODUCT.upper() + "_TOKEN_FILE") or data_dir / "mcp-token")
    url = os.environ.get(PRODUCT.upper() + "_URL") or f"http://127.0.0.1:{DEFAULT_PORT}"
    meta = next((t for t in catalog() if t["name"] == name), {})
    timeout = float(meta.get("x-timeout-s") or meta.get("inputSchema", {}).get("x-timeout-s") or 180)
    wait = (arguments or {}).get("wait_s")
    if isinstance(wait, (int, float)) and not isinstance(wait, bool):
        timeout = max(timeout, max(0, wait) + 90)
    context = app.request_context
    progress_token = context.meta.progressToken if context.meta else None

    async def progress():
        elapsed = 0
        while True:
            await asyncio.sleep(10)
            elapsed += 10
            await context.session.send_progress_notification(progress_token, progress=elapsed, message=name + " is running")

    beat = asyncio.create_task(progress()) if progress_token is not None else None
    try:
        token = token_file.read_text(encoding="utf-8").strip()
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10), trust_env=False) as client:
            response = await client.post(url.rstrip("/") + "/api/agent/call", json={"name": name, "arguments": arguments or {}},
                                          headers={"Authorization": "Bearer " + token})
        body = response.json()
        saved_chart = name == "data_chart" and (arguments or {}).get("action") == "get"
        png = ""
        if name == "laplace_data_chart" and isinstance(body, dict):
            png = body.pop("png_base64", "")
        elif saved_chart and isinstance(body, dict):
            png = body.pop("image_base64", "")
        content = [TextContent(type="text", text=json.dumps(body, ensure_ascii=False))]
        if png and (saved_chart or (arguments or {}).get("include_image")):
            content.append(ImageContent(type="image", data=png, mimeType="image/png"))
        return CallToolResult(content=content,
                              structuredContent=body if isinstance(body, dict) else None, isError=response.is_error)
    except httpx.TimeoutException:
        body = {"error": "timeout", "tool": name, "outcome_unknown": not meta.get("annotations", {}).get("readOnlyHint", False),
                "message": "The request may still be running. Check its native status before retrying; it was not replayed."}
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(body))], structuredContent=body, isError=True)
    except (OSError, httpx.HTTPError) as exc:
        raise RuntimeError(f"{PRODUCT}_unavailable: start {PRODUCT.title()}'s Hoard explicitly from Hoard Hub. {exc}") from exc
    finally:
        if beat is not None:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)


async def main():
    async with stdio_server() as (reader, writer):
        await app.run(reader, writer, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
