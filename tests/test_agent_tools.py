"""The agent tool catalog: at most 18 tools, valid schemas, and a couple of live calls."""

import asyncio

from nightingale.agent_tools import TOOLS, call_tool, tool_catalog


def test_tool_count_and_names():
    assert len(TOOLS) <= 18
    names = [t.name for t in TOOLS]
    assert names == sorted(set(names), key=names.index)  # no duplicates
    for expected in ("data_ingest", "data_list", "data_transform", "data_query", "data_quality",
                       "data_chart", "data_model", "data_forecast", "data_export", "data_log", "data_ask"):
        assert expected in names


def test_catalog_descriptions_have_keyword_hints():
    for entry in tool_catalog():
        assert entry["description"]
        assert "Sinónimos" in entry["description"] or len(entry["description"].splitlines()[0]) <= 110
        assert "inputSchema" in entry


def test_call_tool_ingest_and_list(services, tmp_path):
    path = tmp_path / "t.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")

    async def run():
        await call_tool(services, "data_ingest", {"kind": "file", "path": str(path), "name": "t"})
        return await call_tool(services, "data_list", {})

    result = asyncio.run(run())
    assert result["datasets"][0]["name"] == "t"


def test_call_tool_unknown_raises_keyerror(services):
    import pytest

    async def run():
        await call_tool(services, "nope", {})

    with pytest.raises(KeyError):
        asyncio.run(run())
