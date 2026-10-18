"""Proof that the MCP adapter works: spawn mcp_server.py over real stdio
against a live instance of the app and drive it through the MCP protocol
(list_tools + call_tool), not just import its Python functions directly.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from conftest import free_port

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_SERVER_PATH = REPO_ROOT / "laplaces_hoard" / "mcp_server.py"


@pytest.mark.asyncio
async def test_mcp_lists_all_tools_and_calls_calc_and_data_query(live_app):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(MCP_SERVER_PATH)],
        env={**os.environ, "LAPLACE_URL": live_app.base_url, "LAPLACE_DATA_DIR": str(live_app.data_dir)},
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_result = await session.list_tools()
            names = {t.name for t in tools_result.tools}
            expected = {
                "calc", "math", "units_convert", "stats", "date_calc",
                "data_list", "data_register", "data_describe", "data_query",
                "data_chart", "data_report", "report_rerun", "work_log",
            }
            assert expected.issubset(names), f"missing tools: {expected - names}"

            for tool in tools_result.tools:
                description = tool.description or ""
                assert "Keywords:" in description, tool.name
                assert tool.annotations is not None, tool.name
                assert tool.annotations.openWorldHint is False, tool.name
                assert tool.annotations.readOnlyHint is (tool.name not in {
                    "data_register", "data_report", "report_rerun",
                    "notebook_cell_create", "notebook_cell_patch", "notebook_cell_delete",
                }), tool.name

            calc_result = await session.call_tool("calc", {"expression": "0.1 + 0.2"})
            assert not calc_result.isError
            text = calc_result.content[0].text
            assert '"exact":"3/10"' in text.replace(" ", "") or "3/10" in text

            cell = await session.call_tool("notebook_cell_create", {"engine": "calc", "input": "3+4"})
            assert not cell.isError
            cell_id = json.loads(cell.content[0].text)["id"]
            patched = await session.call_tool("notebook_cell_patch", {"id": cell_id, "patch": {"input": "8+9"}})
            assert not patched.isError
            assert json.loads(patched.content[0].text)["result"]["exact"] == "17"
            read = await session.call_tool("notebook_cell_get", {"id": cell_id})
            assert not read.isError and json.loads(read.content[0].text)["input"] == "8+9"
            context = await session.call_tool("app_context", {"kind": "cell", "object_id": str(cell_id)})
            assert json.loads(context.content[0].text)["active_objects"][0]["object_id"] == str(cell_id)

            reg_result = await session.call_tool(
                "data_register",
                {"path": str((REPO_ROOT / "tests" / "fixtures" / "tiny.csv"))},
            )
            assert not reg_result.isError, reg_result.content

            query_result = await session.call_tool(
                "data_query", {"sql": "SELECT COUNT(*) AS n FROM tiny", "limit": 10}
            )
            assert not query_result.isError, query_result.content
            assert '"n"' in query_result.content[0].text or "n" in query_result.content[0].text

            report_result = await session.call_tool("data_report", {
                "title": "Tiny values", "sql": "SELECT SUM(value) AS total FROM tiny",
            })
            assert not report_result.isError, report_result.content
            report = json.loads(report_result.content[0].text)
            assert report["rows"][0]["total"] == 60
            rerun_result = await session.call_tool("report_rerun", {"id": report["id"]})
            assert not rerun_result.isError, rerun_result.content
            assert json.loads(rerun_result.content[0].text)["rows"][0]["total"] == 60

            # numbers in a matrix must pass the adapter's own argument validation
            det = await session.call_tool("math", {"operation": "matrix", "matrix_op": "det", "matrix": [[1, 2], [3, 4]]})
            assert not det.isError, det.content
            assert '"-2"' in det.content[0].text

            # errors carry the app's code and actionable message
            bad = await session.call_tool("data_query", {"sql": "DROP TABLE tiny"})
            assert bad.isError
            assert "sql_gate" in bad.content[0].text

            # by default no image comes back at all (an unrequested image can
            # crash a text-only local model); only a small JSON summary,
            # pointing at the Work log entry that does hold the chart
            chart = await session.call_tool(
                "data_chart", {"sql": "SELECT * FROM tiny", "kind": "bar", "x": "id", "y": "value"}
            )
            assert not chart.isError, chart.content
            kinds = [c.type for c in chart.content]
            assert kinds == ["text"]
            assert len(chart.content[0].text) < 1000
            assert "png_base64" not in chart.content[0].text
            assert '"cite"' in chart.content[0].text
            chart_json = json.loads(chart.content[0].text)
            assert chart_json["chart_url"].endswith(f"/api/charts/{chart_json['id']}")

            # include_image=true still returns it, for a model that can see images
            chart2 = await session.call_tool(
                "data_chart",
                {"sql": "SELECT * FROM tiny", "kind": "bar", "x": "id", "y": "value", "include_image": True},
            )
            assert not chart2.isError, chart2.content
            assert [c.type for c in chart2.content] == ["text", "image"]

            log = await session.call_tool("work_log", {"limit": 3})
            assert not log.isError
            assert '"cite"' in log.content[0].text


@pytest.mark.asyncio
async def test_mcp_stats_accepts_a_2d_contingency_table(live_app):
    # the schema used to declare `data: list[float]`, which a real MCP
    # client rejects for a nested table even though the description asks
    # for one ("data = table, e.g. [[8, 2], [1, 9]]")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(MCP_SERVER_PATH)],
        env={**os.environ, "LAPLACE_URL": live_app.base_url, "LAPLACE_DATA_DIR": str(live_app.data_dir)},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "stats", {"test": "fisher_exact", "data": [[8, 2], [1, 9]]}
            )
            assert not result.isError, result.content
            assert "odds_ratio" in result.content[0].text

            result2 = await session.call_tool(
                "stats", {"test": "chi2_contingency", "data": [[10, 20], [15, 25]]}
            )
            assert not result2.isError, result2.content
            assert "p_value" in result2.content[0].text


@pytest.mark.asyncio
async def test_mcp_reports_clear_error_when_app_not_running():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(MCP_SERVER_PATH)],
        env={**os.environ, "LAPLACE_URL": f"http://127.0.0.1:{free_port()}"},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("calc", {"expression": "1+1"})
            assert result.isError
            assert "laplaces-hoard_unavailable" in result.content[0].text


def test_mcp_server_refuses_non_loopback_url(monkeypatch):
    import importlib
    import sys as _sys

    monkeypatch.setenv("LAPLACE_URL", "http://example.com:8812")
    _sys.modules.pop("laplaces_hoard.mcp_server", None)
    with pytest.raises(RuntimeError, match="loopback"):
        importlib.import_module("laplaces_hoard.mcp_server")

    # restore a sane default so any later import in this process is clean
    monkeypatch.setenv("LAPLACE_URL", "http://127.0.0.1:8812")
    _sys.modules.pop("laplaces_hoard.mcp_server", None)
    importlib.import_module("laplaces_hoard.mcp_server")
