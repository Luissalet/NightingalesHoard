"""The workspace's tool index reads the first line of each description: it must fit."""
from nightingale.agent_tools import TOOLS


def test_every_first_line_fits_the_tool_index():
    for tool in TOOLS:
        first = tool.description.splitlines()[0]
        assert len(first) <= 110, (tool.name, len(first), first)
        assert "Sinónimos:" in tool.description, tool.name
