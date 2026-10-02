"""Boot the real app in a subprocess, then talk to it over HTTP and through the MCP stdio bridge.

Adapted from Hypatia's Hoard's `test_e2e.py` (same author, MIT).
"""

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from nightingale.hoard_link.net import free_port

ROOT = Path(__file__).resolve().parent.parent


def wait_health(url: str, process: subprocess.Popen, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"app exited early with code {process.returncode}")
        try:
            if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError("app did not become healthy")


@pytest.fixture
def app_process(tmp_path):
    port = free_port()
    data_dir = tmp_path / "data"
    env = {**os.environ, "NIGHTINGALE_DATA_DIR": str(data_dir), "NIGHTINGALE_PORT": str(port),
           "PORT_STRICT": "1", "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen([sys.executable, "-m", "nightingale"], cwd=ROOT, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        wait_health(url, process)
        yield url, data_dir, env
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()


def test_subprocess_http_and_mcp_bridge(app_process, tmp_path):
    url, data_dir, env = app_process
    health = httpx.get(f"{url}/api/health").json()
    assert health["service"] == "nightingale-hoard"

    tools = httpx.get(f"{url}/api/agent/tools").json()["tools"]
    assert "data_ingest" in [t["name"] for t in tools]

    token = (data_dir / "mcp-token").read_text().strip()
    assert len(token) >= 32
    assert not any(char.isspace() for char in token)
    assert httpx.post(f"{url}/api/agent/call", json={"name": "data_list"}).status_code == 401
    auth = {"Authorization": f"Bearer {token}"}

    csv_path = tmp_path / "e2e.csv"
    csv_path.write_text("a,b\n1,2\n3,4\n5,6\n", encoding="utf-8")

    ingested = httpx.post(f"{url}/api/agent/call", timeout=30,
                           json={"name": "data_ingest", "arguments": {"kind": "file", "path": str(csv_path), "name": "e2e"}},
                           headers=auth).json()
    assert ingested["row_count"] == 3

    async def through_mcp():
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        params = StdioServerParameters(
            command=sys.executable,
            args=[str(ROOT / "mcp_server.py")],
            env={**env, "NIGHTINGALE_URL": url, "NIGHTINGALE_TOKEN_FILE": str(data_dir / "mcp-token")},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert "N-000" in (init.instructions or "") or "workbench" in (init.instructions or "").lower()
                listed = await session.list_tools()
                names = [t.name for t in listed.tools]
                assert names == [t["name"] for t in tools]
                write_tool = next(t for t in listed.tools if t.name == "data_ingest")
                assert write_tool.annotations.readOnlyHint is False
                read_tool = next(t for t in listed.tools if t.name == "data_list")
                assert read_tool.annotations.readOnlyHint is True

                listed_ds = json.loads((await session.call_tool("data_list", {})).content[0].text)
                assert listed_ds["datasets"][0]["name"] == "e2e"

                transformed = json.loads((await session.call_tool(
                    "data_transform", {"dataset": "e2e", "op": "derive", "params": {"name": "c", "expr": "a + b"}, "preview": False}
                )).content[0].text)
                assert "c" in [c["name"] for c in transformed["columns"]]

                bad = json.loads((await session.call_tool("data_profile", {"dataset": "nope"})).content[0].text)
                assert "error" in bad

    asyncio.run(through_mcp())
