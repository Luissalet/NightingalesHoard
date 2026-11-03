"""Explainable calculation book: sensitivity (tornado), scenarios and unit checking."""
from __future__ import annotations

import openpyxl
import pytest

from nightingale.sheets.compiler import load
from nightingale.sheets.engine import Compiled
from nightingale.sheets import explain


def build(path):
    wb = openpyxl.Workbook()
    i = wb.active
    i.title = "Inputs"
    for r, (k, v) in enumerate([("Rate", 50), ("Hours", 100), ("Length", 3), ("Markup", 0.1), ("Fee", 5),
                                ("Mode", "fast"), ("Zero", 0)], start=2):
        i.cell(r, 1, k)
        i.cell(r, 2, v)
    i["B5"].number_format = "0%"
    c = wb.create_sheet("Calc")
    rows = [("Cost", "=Inputs!B2*Inputs!B3^2"), ("Price", "=B1*(1+Inputs!B5)+Inputs!B6"),
            ("Wrong", "=Inputs!B4+Inputs!B2"), ("Labour", "=Inputs!B2*Inputs!B3"),
            ("Total labour", "=SUM(B4,Inputs!B2*Inputs!B3)"), ("Pick", '=IF(Inputs!B7="fast",Inputs!B2,Inputs!B3)'), ("Bump", "=Inputs!B8+1")]
    for r, (k, f) in enumerate(rows, start=1):
        c.cell(r, 1, k)
        c.cell(r, 2, f)
    wb.save(path)
    return path


@pytest.fixture()
def comp(tmp_path):
    return Compiled(load(build(tmp_path / "w.xlsx")))


def test_tornado_ranks_dominant_parameter_first(comp):
    r = explain.sensitivity(comp, "Price", 0.1)
    t = r["targets"][0]
    assert t["status"] == "ok"
    labels = [x["label"] for x in t["rows"]]
    assert labels[0] == "Hours" and t["dominant"]["label"] == "Hours"   # squared, so it moves the price most
    assert labels[1] == "Rate" and "Fee" in labels
    assert t["rows"][0]["swing"] > t["rows"][1]["swing"] > 0
    assert abs(sum(x["share"] for x in t["rows"]) - 1) < 1e-9
    reasons = {x["label"]: x["reason"] for x in r["skipped"]}
    assert "text" in reasons["Mode"] and "base value is 0" in reasons["Zero"]
    # absolute steps override the relative change
    s = explain.sensitivity(comp, "Price", 0.1, {"Fee": 1000000})
    assert s["targets"][0]["rows"][0]["label"] == "Fee"
    # an erroring output is reported, not hidden
    e = explain.sensitivity(comp, "Pick")
    assert e["targets"][0]["rows"][0]["parameter"] in ("Inputs!B2", "Inputs!B3")


def test_all_labelled_results(comp):
    r = explain.sensitivity(comp)
    assert {t["label"] for t in r["targets"]} >= {"Price", "Wrong"}


def test_scenarios_store_and_compare(client, tmp_path):
    from nightingale.agent_tools import call_tool_sync
    from nightingale.sheets.store import WorkbookStore
    data = client.services.config.data_dir
    (data / "files").mkdir(parents=True, exist_ok=True)
    build(data / "files" / "w.xlsx")
    svc = client.services
    wid = call_tool_sync(svc, "data_workbook", {"action": "compile", "path": "files/w.xlsx"})["id"]
    r = call_tool_sync(svc, "data_workbook", {"action": "scenarios", "op": "save", "workbook_id": wid,
                                              "scenario": "Cheap", "inputs": {"Rate": 40}})
    assert r["saved"] == "Cheap" and r["inputs"] == {"Inputs!B2": 40} and r["log_id"]
    with pytest.raises(Exception):
        call_tool_sync(svc, "data_workbook", {"action": "scenarios", "op": "save", "workbook_id": wid,
                                              "scenario": "Bad", "inputs": {"Calc!B1": 1}})
    # persisted with the workbook
    assert WorkbookStore(data).scenarios(wid)["Cheap"]["inputs"] == {"Inputs!B2": 40}
    cmp = call_tool_sync(svc, "data_workbook", {"action": "scenarios", "op": "compare", "workbook_id": wid})
    row = next(x for x in cmp["rows"] if x["label"] == "Price")
    base = row["values"]["Base"]["value"]
    cheap = row["values"]["Cheap"]
    assert cmp["columns"] == ["Base", "Cheap"]
    assert cheap["delta"] == pytest.approx(cheap["value"] - base) and cheap["delta"] < 0
    assert cheap["delta_pct"] == pytest.approx(cheap["delta"] / base)
    # REST
    assert client.get(f"/api/sheets/{wid}/scenarios").json()["scenarios"][0]["name"] == "Cheap"
    assert client.put(f"/api/sheets/{wid}/scenarios/Big", json={"inputs": {"Hours": 200}}).json()["saved"] == "Big"
    assert client.post(f"/api/sheets/{wid}/scenarios/compare", json={"scenarios": ["Big"]}).json()["columns"] == ["Base", "Big"]
    assert client.post(f"/api/sheets/{wid}/sensitivity", json={"output": "Price"}).json()["targets"][0]["rows"]
    assert client.delete(f"/api/sheets/{wid}/scenarios/Big").json()["deleted"] == "Big"
    assert client.delete(f"/api/sheets/{wid}/scenarios/Nope").status_code == 404
    # units through the tool and REST, logged
    u = call_tool_sync(svc, "data_workbook", {"action": "units", "workbook_id": wid,
                                              "units": {"Rate": "EUR/h", "Hours": "h", "Length": "m"}})
    assert u["declared"]["Inputs!B2"] == "EUR/h"
    assert any(e["cell"] == "Calc!B3" for e in u["errors"])
    assert client.get(f"/api/sheets/{wid}/units").json()["errors"]
    assert client.put(f"/api/sheets/{wid}/units", json={"units": {"Rate": "furlongs of nonsense"}}).status_code == 400
    log = client.services.log_search(q="workbook")
    assert any("sensitivity" in str(x) for x in (log if isinstance(log, list) else log.get("entries", log.get("items", []))))


def test_unit_error_m_plus_eur(comp):
    r = explain.check_units(comp, {"Rate": "EUR/h", "Hours": "h", "Length": "m"})
    errs = [e for e in r["errors"] if e["cell"] == "Calc!B3"]
    assert len(errs) == 1
    e = errs[0]
    assert e["operation"] == "+" and e["left_unit"] == "m" and e["right_unit"] == "EUR/h"
    assert e["formula"] == "=Inputs!B4+Inputs!B2"
    cells = {x["cell"]: x["unit"] for x in r["cells"]}
    assert "Calc!B3" not in cells      # no unit after an error


def test_currency_is_not_interchangeable():
    u = explain.parse_unit
    assert u("EUR").dimensionality != u("USD").dimensionality
    assert u("€/kg") == u("EUR/kg")


def test_undeclared_inputs_never_error(comp):
    r = explain.check_units(comp, {})
    assert r["errors"] == []
    assert r["unit_unknown"] >= 1
    # only one side declared: still nothing to complain about
    assert explain.check_units(comp, {"Rate": "EUR/h"})["errors"] == []


def test_eur_per_hour_times_hours_is_eur(comp):
    r = explain.check_units(comp, {"Rate": "EUR/h", "Hours": "h", "Length": "m"})
    cells = {x["cell"]: x for x in r["cells"]}
    assert cells["Calc!B4"]["unit"] == "EUR" and cells["Calc!B4"]["source"] == "inferred"
    assert cells["Calc!B5"]["unit"] == "EUR"                       # SUM keeps the unit
    assert cells["Calc!B1"]["unit"] == "EUR*h"                     # rate * hours^2
    assert "Calc!B2" not in cells                                  # the fee has no unit: unknown, not an error
    # IF branches that disagree are an error
    r2 = explain.check_units(comp, {"Rate": "EUR/h", "Hours": "h"})
    assert any(e["cell"] == "Calc!B6" and e["operation"] == "IF" for e in r2["errors"])


def test_formats_detect_currency_and_percent(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "S"
    ws["A1"], ws["B1"] = "Net", 100
    ws["A2"], ws["B2"] = "Tax", 0.21
    ws["A3"], ws["B3"] = "Dollars", 10
    ws["B1"].number_format = '#,##0.00 "€"'
    ws["B2"].number_format = "0.0%"
    ws["B3"].number_format = "[$$-409]#,##0.00"
    ws["A4"], ws["B4"] = "Gross", "=B1*(1+B2)"
    ws["A5"], ws["B5"] = "Mixed", "=B1+B3"
    p = tmp_path / "f.xlsx"
    wb.save(p)
    r = explain.check_units(Compiled(load(p)))
    cells = {x["cell"]: x for x in r["cells"]}
    assert cells["S!B4"]["unit"] == "EUR"
    ins = {x["cell"]: x for x in r["inputs"]}
    assert ins["S!B2"]["unit"] == "1" and ins["S!B2"]["source"] == "format"
    assert ins["S!B3"]["unit"] == "USD"
    assert [e["cell"] for e in r["errors"]] == ["S!B5"]
