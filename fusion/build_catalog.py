"""Regenerate the shipped catalog from both native HTTP implementations, offline."""
from pathlib import Path
import json
import tempfile

from fastapi.testclient import TestClient
from nightingale.agent_tools import tool_catalog
from laplaces_hoard.api import create_app


def main():
    catalog = [{**tool, "module": "data"} for tool in tool_catalog()]
    names = {tool["name"] for tool in catalog}
    with tempfile.TemporaryDirectory(prefix="nightingale-catalog-") as temp:
        with TestClient(create_app(Path(temp)), base_url="http://127.0.0.1:8812") as client:
            tools = client.get("/api/agent/tools").json()["tools"]
    for tool in tools:
        native_name = tool["name"]
        name = "laplace_" + native_name if native_name in names else native_name
        catalog.append({**tool, "name": name, "native_name": native_name, "module": "calculation"})
        names.add(name)
    target = Path(__file__).with_name("catalog.json")
    target.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(catalog)} native tools; {sum(t.get('native_name',t['name']) != t['name'] for t in catalog)} prefixed collisions")


if __name__ == "__main__":
    main()
