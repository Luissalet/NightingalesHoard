"""Relato con los datos: the findings a dataset supports, the check that a
sentence only quotes the finding's own figures, and the REST/MCP surface."""

import asyncio
import html

import pytest

from nightingale import story as story_mod
from nightingale.agent_tools import call_tool


def _csv(rows: int = 120) -> str:
    """A dataset with a real trend, a real group gap, a few outliers and a
    dominant category, so every finding kind has something to find."""
    lines = ["fecha,region,importe,unidades"]
    for i in range(rows):
        month = 1 + (i % 12)
        year = 2023 + (i // 12)
        region = "Norte" if i % 3 else ("Sur" if i % 2 else "Este")
        base = 100 + i * 2  # a clear upward trend
        if region == "Norte":
            base += 60  # a clear group gap
        if i in (7, 41, 88):
            base += 900  # a handful of outliers
        lines.append(f"{year}-{month:02d}-{1 + (i % 27):02d},{region},{base},{1 + (i % 5)}")
    return "\n".join(lines)


@pytest.fixture
def ds(client):
    response = client.post("/api/sources/ingest",
                           json={"kind": "paste", "text": _csv(), "name": "ventas", "fmt": "csv"})
    assert response.status_code == 200, response.text
    return "ventas"


# ---- the findings ------------------------------------------------------------

def test_findings_are_computed_with_support(client, ds):
    response = client.post("/api/story/build", json={"dataset": ds})
    assert response.status_code == 200, response.text
    body = response.json()
    kinds = {finding["kind"] for finding in body["findings"]}
    assert "trend" in kinds
    assert "groups" in kinds
    assert "outliers" in kinds
    assert body["rows"] == 120
    for finding in body["findings"]:
        assert finding["statement"]
        assert finding["chart"]["rows"]
        assert finding["query"]["sql"].startswith("SELECT") or finding["query"]["sql"].startswith("WITH")
        assert finding["evidence"]["test"] in ("pearson", "welch", "iqr", "share")


def test_findings_are_ordered_by_strength(client, ds):
    body = client.post("/api/story/build", json={"dataset": ds}).json()
    strengths = [finding["strength"] for finding in body["findings"]]
    assert strengths == sorted(strengths, reverse=True)


def test_a_dataset_without_support_gets_no_findings(client):
    # constant values: no trend, no gap, no outlier, no change
    rows = "\n".join(f"2024-01-{1 + (i % 28):02d},A,10" for i in range(60))
    client.post("/api/sources/ingest", json={"kind": "paste", "text": "fecha,region,importe\n" + rows,
                                             "name": "plano", "fmt": "csv"})
    body = client.post("/api/story/build", json={"dataset": "plano"}).json()
    assert body["findings"] == []


def test_a_dataset_too_small_is_refused(client):
    client.post("/api/sources/ingest", json={"kind": "paste", "text": "a,b\n1,2\n3,4\n", "name": "pocos", "fmt": "csv"})
    response = client.post("/api/story/build", json={"dataset": "pocos"})
    assert response.status_code == 400
    assert "rows" in response.json()["error"]


def test_a_dataset_without_numbers_is_refused(client):
    rows = "\n".join(f"2024-01-{1 + (i % 28):02d},A" for i in range(40))
    client.post("/api/sources/ingest", json={"kind": "paste", "text": "fecha,region\n" + rows,
                                             "name": "textos", "fmt": "csv"})
    response = client.post("/api/story/build", json={"dataset": "textos"})
    assert response.status_code == 400


def test_the_sentence_quotes_the_figures_the_chart_shows(client, ds):
    body = client.post("/api/story/build", json={"dataset": ds}).json()
    trend = next(finding for finding in body["findings"] if finding["kind"] == "trend")
    figures = {figure["key"]: figure["text"] for figure in trend["figures"]}
    assert figures["first"] in trend["statement"]
    assert figures["last"] in trend["statement"]
    # the chart's own rows are the series the sentence summarises
    assert len(trend["chart"]["rows"]) == trend["evidence"]["periods"]


# ---- the figure check --------------------------------------------------------

def test_numbers_in_reads_both_marks():
    assert story_mod.numbers_in("de 1.234,56 a 62") == ["1.234,56", "62"]
    assert story_mod.numbers_in("from 1,234.56 to 62") == ["1,234.56", "62"]


def test_parse_number_follows_the_language():
    assert story_mod.parse_number("1.234,56", "es") == 1234.56
    assert story_mod.parse_number("1,234.56", "en") == 1234.56


def test_a_sentence_with_its_own_figures_passes():
    figures = [{"key": "first", "value": 100.0, "decimals": 2}, {"key": "last", "value": 250.5, "decimals": 2}]
    check = story_mod.verify_statement("Sube de 100,00 a 250,50.", figures, "es")
    assert check["ok"] is True
    assert check["checked"] == 2


def test_a_sentence_with_an_invented_figure_is_rejected():
    figures = [{"key": "first", "value": 100.0, "decimals": 2}]
    check = story_mod.verify_statement("Sube de 100,00 a 999,00.", figures, "es")
    assert check["ok"] is False
    assert check["unsupported"] == ["999,00"]


def test_a_sentence_without_any_figure_is_rejected():
    # the sentence has to state the figure the chart shows; a text that quotes
    # none of them says nothing that can be checked
    figures = [{"key": "first", "value": 100.0, "decimals": 2}]
    check = story_mod.verify_statement("Los valores suben con el tiempo.", figures, "es")
    assert check["ok"] is False
    assert check["checked"] == 0


def test_an_edited_sentence_without_a_figure_falls_back_to_the_template(client, ds):
    body = client.post("/api/story/build", json={"dataset": ds}).json()
    finding = body["findings"][0]
    updated = client.put(f"/api/story/stories/{body['story_id']}",
                         json={"statements": {finding["id"]: "Los valores suben con el tiempo."}}).json()
    assert updated["rejected"][0]["id"] == finding["id"]
    kept = next(item for item in updated["findings"] if item["id"] == finding["id"])
    assert kept["statement"] == kept["template"]


def test_a_period_label_is_allowed_as_extra_text():
    figures = [{"key": "first", "value": 100.0, "decimals": 2}]
    check = story_mod.verify_statement("En 2024-03 llega a 100,00.", figures, "es", extra=["2024-03"])
    assert check["ok"] is True


def test_an_edited_sentence_with_a_wrong_figure_falls_back_to_the_template(client, ds):
    body = client.post("/api/story/build", json={"dataset": ds}).json()
    finding = body["findings"][0]
    response = client.put(f"/api/story/stories/{body['story_id']}",
                          json={"statements": {finding["id"]: "Esto dice 123456789."}})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["rejected"][0]["id"] == finding["id"]
    kept = next(item for item in updated["findings"] if item["id"] == finding["id"])
    assert kept["statement"] == kept["template"]
    assert kept["statement_source"] == "template"


def test_an_edited_sentence_with_the_right_figure_is_kept(client, ds):
    body = client.post("/api/story/build", json={"dataset": ds}).json()
    finding = next(item for item in body["findings"] if item["kind"] == "groups")
    figures = {figure["key"]: figure["text"] for figure in finding["figures"]}
    sentence = f"{figures['highest']} frente a {figures['lowest']}."
    response = client.put(f"/api/story/stories/{body['story_id']}",
                          json={"statements": {finding["id"]: sentence}})
    updated = response.json()
    assert updated["rejected"] == []
    kept = next(item for item in updated["findings"] if item["id"] == finding["id"])
    assert kept["statement"] == sentence
    assert kept["statement_source"] == "edited"


# ---- the routes --------------------------------------------------------------

def test_story_round_trip_and_reorder(client, ds):
    built = client.post("/api/story/build", json={"dataset": ds}).json()
    story_id = built["story_id"]
    ids = [finding["id"] for finding in built["findings"]]
    assert len(ids) >= 2

    listed = client.get("/api/story/stories", params={"dataset": ds}).json()["stories"]
    assert listed[0]["story_id"] == story_id

    reversed_ids = list(reversed(ids))
    updated = client.put(f"/api/story/stories/{story_id}", json={"order": reversed_ids}).json()
    assert [finding["id"] for finding in updated["findings"]] == reversed_ids

    dropped = client.put(f"/api/story/stories/{story_id}", json={"remove": [reversed_ids[0]]}).json()
    assert reversed_ids[0] not in [finding["id"] for finding in dropped["findings"]]

    fetched = client.get(f"/api/story/stories/{story_id}").json()
    assert [finding["id"] for finding in fetched["findings"]] == [finding["id"] for finding in dropped["findings"]]


def test_unknown_story_and_unknown_finding(client, ds):
    assert client.get("/api/story/stories/9999").status_code == 404
    built = client.post("/api/story/build", json={"dataset": ds}).json()
    response = client.put(f"/api/story/stories/{built['story_id']}", json={"remove": ["nope"]})
    assert response.status_code == 400


def test_export_html_is_standalone_and_downloadable(client, ds):
    built = client.post("/api/story/build", json={"dataset": ds}).json()
    exported = client.post(f"/api/story/stories/{built['story_id']}/export", params={"format": "html"}).json()
    assert exported["ok"] is True
    page = open(exported["path"], encoding="utf-8").read()
    assert page.startswith("<!doctype html>")
    assert "data:image/png;base64," in page  # the chart travels inside the file
    assert "<script" not in page  # nothing to run, nothing to fetch
    assert html.escape(built["findings"][0]["statement"]) in page
    assert html.escape(built["findings"][0]["query"]["sql"]) in page  # the query travels with it

    download = client.get("/api/story/download", params={"path": exported["path"]})
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/html")


def test_export_refuses_a_path_outside_the_exports_folder(client, ds):
    built = client.post("/api/story/build", json={"dataset": ds}).json()
    client.post(f"/api/story/stories/{built['story_id']}/export", params={"format": "html"})
    response = client.get("/api/story/download", params={"path": "C:/Windows/win.ini"})
    assert response.status_code == 404


def test_export_presentation_reports_a_missing_cicero(client, ds, monkeypatch):
    built = client.post("/api/story/build", json={"dataset": ds}).json()
    monkeypatch.setattr("nightingale.story_export.cicero_available",
                        lambda: {"available": False, "detail": "hub not reachable"})
    result = client.post(f"/api/story/stories/{built['story_id']}/export",
                         params={"format": "presentation"}).json()
    assert result["ok"] is False
    assert result["unavailable"] is True
    assert "Cicero" in result["detail"]


def test_export_presentation_uses_cicero_tools(client, ds, monkeypatch):
    built = client.post("/api/story/build", json={"dataset": ds}).json()
    calls = []

    def fake_call(app, tool, arguments=None, timeout=120.0):
        calls.append((app, tool, arguments))
        if tool == "cicero_status":
            return {"ok": True, "result": {"decks": 0}}
        if tool == "deck_create":
            return {"ok": True, "result": {"deck_id": "deck-1"}}
        return {"ok": True, "result": {"slides": len(arguments["slides"])}}

    monkeypatch.setattr("nightingale.story_export.family.call", fake_call)
    result = client.post(f"/api/story/stories/{built['story_id']}/export",
                         params={"format": "presentation"}).json()
    assert result["ok"] is True
    assert result["deck_id"] == "deck-1"
    assert [tool for _, tool, _ in calls] == ["cicero_status", "deck_create", "slides_add"]
    slides = calls[-1][2]["slides"]
    assert len(slides) == len(built["findings"])  # one finding per slide
    assert slides[0]["blocks"][0]["text"] == built["findings"][0]["statement"]


def test_story_delete(client, ds):
    built = client.post("/api/story/build", json={"dataset": ds}).json()
    assert client.delete(f"/api/story/stories/{built['story_id']}").json()["ok"] is True
    assert client.get(f"/api/story/stories/{built['story_id']}").status_code == 404


def test_the_story_is_in_the_analysis_log(client, ds):
    built = client.post("/api/story/build", json={"dataset": ds}).json()
    entries = client.get("/api/log", params={"q": "story_build"}).json()["entries"]
    assert any(entry["op_id"] == built["log_id"] for entry in entries)


# ---- the agent tools ---------------------------------------------------------

def test_agent_tools_build_read_and_export(client, ds):
    async def run():
        built = await call_tool(client.services, "story_build", {"dataset": ds})
        fetched = await call_tool(client.services, "story_get", {"story_id": built["story_id"]})
        listed = await call_tool(client.services, "story_list", {"dataset": ds})
        exported = await call_tool(client.services, "story_export",
                                   {"story_id": built["story_id"], "format": "html"})
        return built, fetched, listed, exported

    built, fetched, listed, exported = asyncio.run(run())
    assert built["findings"]
    assert fetched["story_id"] == built["story_id"]
    assert listed["stories"][0]["story_id"] == built["story_id"]
    assert exported["ok"] is True


def test_agent_story_update_rejects_an_invented_figure(client, ds):
    async def run():
        built = await call_tool(client.services, "story_build", {"dataset": ds})
        finding = built["findings"][0]
        return await call_tool(client.services, "story_update",
                               {"story_id": built["story_id"], "statements": {finding["id"]: "Cifra 424242."}})

    updated = asyncio.run(run())
    assert updated["rejected"]


def test_agent_story_build_needs_a_dataset(client):
    async def run():
        await call_tool(client.services, "story_build", {})

    with pytest.raises(Exception):
        asyncio.run(run())