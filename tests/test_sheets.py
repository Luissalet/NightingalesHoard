"""Excel workbook compiler: formula semantics, model, export, store, API and agent tool.

Tests that need a reference engine (Excel or LibreOffice) to put cached values
in a workbook are skipped when neither is installed.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import openpyxl
import pytest
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

from nightingale.sheets import runtime as rt
from nightingale.sheets.compiler import addr, generate, load
from nightingale.sheets.engine import Compiled, export_python
from nightingale.sheets.parser import parse
from nightingale.sheets.reference import engines, make_cases, recalc, verify

A = rt.Area


@pytest.mark.parametrize("mac", [False, True])
def test_workbook_date_epoch_survives_evaluation_and_export(tmp_path, mac):
    from openpyxl.utils.datetime import MAC_EPOCH, WINDOWS_EPOCH, to_excel
    wb = openpyxl.Workbook()
    wb.epoch = MAC_EPOCH if mac else WINDOWS_EPOCH
    ws = wb.active
    ws.title = "Dates"
    ws.append([2026, 11, 3, "=DATE(A1,B1,C1)"])
    ws["D1"].number_format = "yyyy-mm-dd"
    ws["A2"] = dt.datetime(2026, 11, 3, 12)
    ws["B2"] = "=YEAR(A2)"
    ws["C2"] = "=A2+1"
    ws["C2"].number_format = "yyyy-mm-dd"
    ws["D2"] = "=EDATE(A2,1)"
    ws["E2"] = "=EOMONTH(A2,0)"
    ws["F2"] = "=WORKDAY(A2,1)"
    ws["G2"] = "=TODAY()"
    ws["H2"] = "=DATE(2026,11,3)"  # folded at compilation, not just runtime
    ws["I2"] = "=H2+1"
    path = tmp_path / f"dates-{mac}.xlsx"
    wb.save(path)
    model = load(path)
    compiled = Compiled(model)
    moment = dt.datetime(2026, 11, 3, 12)
    values = compiled.evaluate(as_of=moment)["values"]
    expected = float(to_excel(dt.datetime(2026, 11, 3), wb.epoch))
    assert model.summary()["date_system"] == ("1904" if mac else "1900")
    assert model.describe(("Dates", 2, 1))["display"] == "2026-11-03"
    assert values[("Dates", 1, 4)] == expected
    assert values[("Dates", 2, 2)] == 2026
    assert values[("Dates", 2, 3)] == expected + 1.5
    assert values[("Dates", 2, 4)] == float(to_excel(dt.date(2026, 12, 3), wb.epoch))
    assert values[("Dates", 2, 5)] == float(to_excel(dt.date(2026, 11, 30), wb.epoch))
    assert values[("Dates", 2, 6)] == expected + 1
    assert values[("Dates", 2, 7)] == expected
    assert values[("Dates", 2, 9)] == expected + 1
    exported = {}
    exec(export_python(model), exported)
    assert exported["compute"](as_of=moment) == {
        row["cell"]: row["value"] for row in compiled.evaluate(as_of=moment)["results"]}
    # An interleaved calculation with the other epoch cannot change this model.
    other = openpyxl.Workbook()
    other.active.append([2026, "=DATE(A1,11,3)"])
    other_path = tmp_path / "other.xlsx"
    other.save(other_path)
    other_compiled = Compiled(load(other_path))
    other_compiled.evaluate()
    assert compiled.evaluate(as_of=moment)["values"][("Dates", 1, 4)] == expected
    from concurrent.futures import ThreadPoolExecutor
    def calculate(i):
        if i % 2:
            return compiled.evaluate(as_of=moment)["values"][("Dates", 1, 4)] == expected
        return other_compiled.evaluate()["values"][("Sheet", 1, 2)] == float(to_excel(dt.date(2026, 11, 3), WINDOWS_EPOCH))
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(calculate, range(40)))


def test_literal_text_starting_equals_is_not_a_formula(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "=1+2"
    ws["A1"].data_type = "s"
    ws["B1"] = '=A1&"!"'
    path = tmp_path / "literal.xlsx"
    wb.save(path)
    model = load(path)
    assert model.cells[("Sheet", 1, 1)].formula is None
    assert Compiled(model).evaluate()["results"][0]["value"] == "=1+2!"
    exported = {}
    exec(export_python(model), exported)
    assert exported["compute"]()["Sheet!B1"] == "=1+2!"
    from nightingale.sheets.reference import _write_case
    destination = tmp_path / "overrides.xlsx"
    _write_case(path, destination, model, Compiled(model), {"Sheet!A1": {"error": "#N/A"}})
    cell = openpyxl.load_workbook(destination).active["A1"]
    assert cell.value == "#N/A" and cell.data_type == "e"


def test_empty_reference_verification_is_inconclusive_without_recalculation(tmp_path, monkeypatch):
    from nightingale.sheets import reference
    wb = openpyxl.Workbook()
    wb.active.append([1, "=A1+1"])
    path = tmp_path / "empty.xlsx"
    wb.save(path)
    model = load(path)
    monkeypatch.setattr(reference, "engines", lambda: ["excel"])
    def unexpected(*args):
        pytest.fail("An empty verification must not start the reference engine")
    monkeypatch.setattr(reference, "recalc", unexpected)
    for cases, n in [([], 12), (None, 0)]:
        result = reference.verify(model, path, cases=cases, n=n, engine="excel")
        assert result["status"] == "inconclusive" and result["compared"] == 0
    model.cells[("Sheet", 1, 2)].frozen = "unsupported"
    result = reference.verify(model, path, cases=[{}], engine="excel")
    assert result["status"] == "inconclusive" and result["compared"] == 0


# --- parser ---------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("=1+2*3", ("bin", "+", ("num", 1.0), ("bin", "*", ("num", 2.0), ("num", 3.0)))),
    ("=-2^2", ("bin", "^", ("un", "-", ("num", 2.0)), ("num", 2.0))),
    ("=2^3^2", ("bin", "^", ("bin", "^", ("num", 2.0), ("num", 3.0)), ("num", 2.0))),
    ("='My S'!$C$3", ("ref", "My S", 3, 3)),
    ("=SUM(A:A)", ("call", "SUM", [("range", "H", 1, 1, 1048576, 1)])),
    ("=_xlfn.IFNA(A1,0)", ("call", "IFNA", [("ref", "H", 1, 1), ("num", 0.0)])),
    ("=IF(A1,,2)", ("call", "IF", [("ref", "H", 1, 1), ("missing",), ("num", 2.0)])),
    ('="a""b"&1', ("bin", "&", ("str", 'a"b'), ("num", 1.0))),
])
def test_parse(text, expected):
    assert parse(text, "H") == expected


@pytest.mark.parametrize("text", ["=A1 B1", "=SUM(A1:INDEX(B:B,3))", "=SUM(", "=[1]Hoja!A1+1"])
def test_parse_reports_what_it_does_not_handle(text):
    from nightingale.sheets.parser import walk
    assert any(n[0] in ("bad", "name") for n in walk(parse(text, "H")))


# --- Excel semantics (values checked against Excel) ---------------------------

def test_rounding_and_arithmetic():
    assert rt.ROUND(2.5) == 3 and rt.ROUND(-2.5) == -3
    assert rt.ROUND(1.005, 2) == 1.01  # Excel rounds the 15-digit value, not the binary one
    assert rt.ROUNDUP(-1.21, 1) == -1.3 and rt.ROUNDDOWN(1.29, 1) == 1.2
    assert rt.ROUND(5, 1234) == 5
    assert rt.MOD(-3, 2) == 1 and rt.MOD(3, -2) == -1
    with pytest.raises(rt.XlError):
        rt.op_div(1, 0)
    with pytest.raises(rt.XlError, match="#DIV/0!"):
        rt.op_pow(0, -1)
    assert rt.op_add("3", True) == 4
    assert rt.PRODUCT(A([[None, "a"]])) == 0


def test_text_conversion_matches_excel():
    assert [rt.fmt_general(x) for x in (1e-7, 1e-10, 0.1 + 0.2, 1e15, 1 / 3)] == [
        "0.0000001", "1E-10", "0.3", "1E+15", "0.333333333333333"]
    assert rt.op_cat(7, False) == "7FALSE"
    assert rt.TEXT(1234.567, "#,##0.00") == "1,234.57" and rt.TEXT(0.256, "0%") == "26%"


def test_comparisons_follow_excel_type_order():
    assert rt.op_lt(99, "a") and rt.op_lt("z", False) and rt.op_eq("ABC", "abc")
    assert rt.op_eq(None, 0) and rt.op_eq(None, "") and rt.op_eq(None, False)


def test_criteria():
    area = A([["10", 10.0, "a", None, "apple", 3.0, True]])
    assert rt.COUNTIF(area, "10") == 2
    assert rt.COUNTIF(area, ">5") == 1
    assert rt.COUNTIF(area, "a*") == 2
    assert rt.COUNTIF(area, "<>a") == 6
    assert rt.COUNTIF(area, "") == 1
    assert rt.SUMIFS(A([[1.0, 2.0, 4.0]]), A([["x", "y", "x"]]), "x") == 5


def test_lookups():
    t = A([[1.0, "uno"], [2.0, "dos"], [5.0, "cinco"]])
    assert rt.VLOOKUP(2, t, 2, False) == "dos"
    assert rt.VLOOKUP(4, t, 2, True) == "dos"
    with pytest.raises(rt.XlError, match="#N/A"):
        rt.VLOOKUP(9, t, 2, False)
    assert rt.INDEX(t, 3, 2) == "cinco"
    assert rt.XLOOKUP("dos", A([["uno"], ["dos"]]), A([[10.0], [20.0]])) == 20


@pytest.mark.parametrize("values,target,kind,excel", [
    # observed in Excel on unsorted, mixed data (tests/match_probe.py)
    ([2, 15, None], 2, -1, 1),
    ([None, True, True, 2.5, None, 3], 2.5, -1, 4),
    ([5, 5, None, 0, 2.5, 5, "z"], 1, -1, 2),
    ([-4, -2, None, 2, 8], -2, -1, None),
    ([0.1, 2, 1, 1e-7, 2, -3, -3, False], 1e-7, 1, 4),
])
def test_approximate_match_like_excel(values, target, kind, excel):
    area = A([[float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v for v in values]])
    if excel is None:
        with pytest.raises(rt.XlError):
            rt.MATCH(target, area, kind)
    else:
        assert rt.MATCH(target, area, kind) == excel


def test_dates():
    assert rt.DATE(2026, 13, 1) == rt.DATE(2027, 1, 1)
    d = rt.DATE(2026, 1, 31)
    assert rt.from_serial(rt.EDATE(d, 1)).date() == dt.date(2026, 2, 28)
    assert rt.from_serial(rt.EOMONTH(d, 1)).date() == dt.date(2026, 2, 28)
    assert rt.from_serial(rt.WORKDAY(rt.DATE(2026, 11, 6), 1)).date() == dt.date(2026, 11, 9)
    assert rt.NETWORKDAYS(rt.DATE(2026, 11, 2), rt.DATE(2026, 11, 8)) == 5


# --- workbook fixtures --------------------------------------------------------

def quote_workbook(path: Path) -> Path:
    wb = openpyxl.Workbook()
    q = wb.active
    q.title = "Cotización"
    t = wb.create_sheet("Tarifas")
    for r in [("Material", "€/kg", "Densidad"), ("Acero", 2.1, 7.85), ("Aluminio", 4.6, 2.7), ("Latón", 6.3, 8.5)]:
        t.append(r)
    for i, (a, b) in enumerate([(1, 0), (10, 0.05), (50, 0.1), (200, 0.15)], start=2):
        t.cell(i, 5, a)
        t.cell(i, 6, b)
    rows = [("Material", "Aluminio"), ("Largo (mm)", 250), ("Ancho (mm)", 120), ("Espesor (mm)", 3), ("Unidades", 40),
            ("Urgente", False), ("Fecha pedido", dt.date(2026, 11, 3))]
    for i, (k, v) in enumerate(rows, start=1):
        q.cell(i, 1, k)
        q.cell(i, 2, v)
    q["B7"].number_format = "dd/mm/yyyy"
    formulas = {
        9: ("Peso unitario (kg)", "=B2*B3*B4/1e6*VLOOKUP(B1,Tarifas!A2:C4,3,FALSE)"),
        10: ("Coste material", "=ROUND(B9*VLOOKUP(B1,Tarifas!A:B,2,FALSE)*B5,2)"),
        11: ("Descuento", "=INDEX(Tarifas!F2:F5,MATCH(B5,Tarifas!E2:E5,1))"),
        12: ("Recargo urgencia", "=IF(B6,15%,0)"),
        13: ("Total", "=ROUND(B10*(1-B11)*(1+B12),2)"),
        14: ("Entrega", "=WORKDAY(B7,5)"),
        15: ("Total con IVA", "=B13*(1+IVA)"),
        16: ("Plazo", '=IF(B5>100,"Largo","Normal")'),
    }
    for r, (k, f) in formulas.items():
        q.cell(r, 1, k)
        q.cell(r, 2, f)
    q["B14"].number_format = "dd/mm/yyyy"
    q["A20"], q["B20"] = "IVA", 0.21
    wb.defined_names["IVA"] = DefinedName("IVA", attr_text="'Cotización'!$B$20")
    dv = DataValidation(type="list", formula1="=Tarifas!$A$2:$A$4")
    q.add_data_validation(dv)
    dv.add("B1")
    wb.save(path)
    return path


ENGINES = engines()
need_engine = pytest.mark.skipif(not ENGINES, reason="ni Excel ni LibreOffice instalados")


@pytest.fixture(scope="module")
def calculated(tmp_path_factory):
    """The quote workbook recalculated by a real engine (cached values present)."""
    if not ENGINES:
        pytest.skip("ni Excel ni LibreOffice instalados")
    tmp = tmp_path_factory.mktemp("quote")
    src = quote_workbook(tmp / "quote.xlsx")
    outs, err = recalc([src], ENGINES[0], tmp, 300)
    assert not err, err
    return outs[0]


@need_engine
def test_model_structure(calculated):
    m = load(calculated)
    s = m.summary()
    labels = {p["label"] for p in s["parameters"]}
    assert {"Material", "Unidades", "Urgente", "IVA", "Fecha pedido"} <= labels
    mat = next(p for p in s["parameters"] if p["label"] == "Material")
    assert mat["options"] == ["Acero", "Aluminio", "Latón"]
    assert {r["label"] for r in s["results"]} == {"Total con IVA", "Entrega", "Plazo"}
    assert s["frozen"] == [] and s["cycles"] == []
    assert s["tables"] >= 10


@need_engine
def test_cached_values_reproduced(calculated):
    r = Compiled(load(calculated)).check_cached()
    assert r["difference_count"] == 0 and r["agree"] == r["compared"] >= 8


@need_engine
def test_evaluate_by_label_and_address(calculated):
    c = Compiled(load(calculated))
    out = {x["label"]: x for x in c.evaluate({"Material": "Latón", "Cotización!B5": 60, "Urgente": True})["results"]}
    weight = 250 * 120 * 3 / 1e6 * 8.5
    cost = round(weight * 6.3 * 60, 2)
    total = round(cost * 0.9 * 1.15, 2)
    assert out["Total con IVA"]["value"] == pytest.approx(total * 1.21)
    assert out["Entrega"]["display"] == "2026-11-10"
    with pytest.raises(ValueError, match="fórmula"):
        c.evaluate({"Cotización!B13": 1})


@need_engine
def test_export_runs_standalone_and_matches(calculated, tmp_path):
    m = load(calculated)
    code = export_python(m)
    f = tmp_path / "calculo.py"
    f.write_text(code, encoding="utf-8")
    inputs = {"Cotización!B5": 250, "Cotización!B1": "Acero"}
    out = subprocess.run([sys.executable, "-I", str(f), json.dumps(inputs)], capture_output=True, text=True, check=True)
    got = json.loads(out.stdout)
    mine = {x["cell"]: x["value"] for x in Compiled(m).evaluate(inputs)["results"]}
    assert got == mine
    assert "import nightingale" not in code


@need_engine
def test_verify_against_reference_engine(calculated):
    m = load(calculated)
    r = verify(m, calculated, n=6, seed=3)
    assert r["status"] == "agree", r["report"]
    assert r["compared"] >= 6 and r["agree"] == r["compared"]


def test_frozen_cycles_and_literals(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "H"
    ws["A1"] = "=0.21"            # constant typed as a formula → parameter
    ws["A2"] = "=A1*100"
    ws["B1"] = "=B2+1"            # cycle
    ws["B2"] = "=B1+1"
    ws["C1"] = "=GETPIVOTDATA(\"x\",A1)"  # unsupported → frozen
    ws["C2"] = "=C1*2"
    ws["D1"] = "=TODAY()"
    p = tmp_path / "w.xlsx"
    wb.save(p)
    m = load(p)
    s = m.summary()
    assert [x["cell"] for x in s["parameters"]] == ["H!A1"]
    assert {x["cell"] for x in s["frozen"]} == {"H!B1", "H!B2", "H!C1"}
    assert s["cycles"] == [["H!B1", "H!B2"]]
    assert s["volatile"] == ["H!D1"]
    c = Compiled(m)
    assert "H!C2" in {addr(k) for k in c.frozen_upstream()}
    res = {x["cell"]: x for x in c.evaluate({"H!A1": 0.5}, as_of=dt.datetime(2026, 11, 3))["results"]}
    assert res["H!A2"]["value"] == 50
    assert res["H!D1"]["value"] == rt.DATE(2026, 11, 3)
    assert res["H!C2"].get("uses_frozen")
    assert "congelada" in generate(m)


def test_cases_are_seeded_and_respect_options(tmp_path):
    m = load(quote_workbook(tmp_path / "q.xlsx"))
    a, b = make_cases(m, 5, seed=1), make_cases(m, 5, seed=1)
    assert a == b
    assert all(c["Cotización!B1"] in ("Acero", "Aluminio", "Latón") for c in a)


# --- store, agent tool and HTTP -------------------------------------------------

def test_agent_tool_and_http(client, tmp_path):
    from nightingale.agent_tools import call_tool_sync, tool_catalog
    assert "data_workbook" in {t["name"] for t in tool_catalog()}
    data = client.services.config.data_dir
    (data / "files").mkdir(parents=True, exist_ok=True)
    src = quote_workbook(data / "files" / "cotizacion.xlsx")
    svc = client.services
    r = call_tool_sync(svc, "data_workbook", {"action": "compile", "path": "files/cotizacion.xlsx", "name": "Cotización"})
    wid = r["id"]
    assert r["summary"]["executed"] == r["summary"]["formulas"]
    ev = call_tool_sync(svc, "data_workbook", {"action": "evaluate", "workbook_id": wid, "inputs": {"Unidades": 10}})
    assert any(x["label"] == "Total con IVA" for x in ev["results"])
    tr = call_tool_sync(svc, "data_workbook", {"action": "trace", "workbook_id": wid, "cell": "Total con IVA"})
    assert {"Unidades", "Material", "IVA"} <= {p["label"] for p in tr["parameters"]}
    ex = call_tool_sync(svc, "data_workbook", {"action": "export", "workbook_id": wid})
    assert Path(ex["path"]).exists()
    with pytest.raises(Exception):
        call_tool_sync(svc, "data_workbook", {"action": "compile", "path": "C:/Windows/win.ini" if sys.platform == "win32" else "/etc/passwd"})

    lst = client.get("/api/sheets").json()
    assert lst["workbooks"][0]["id"] == wid
    with open(src, "rb") as fh:
        up = client.post("/api/sheets/upload", files={"file": ("otra.xlsx", fh, "application/octet-stream")}).json()
    assert up["name"] == "otra"
    e = client.post(f"/api/sheets/{wid}/evaluate", json={"inputs": {"Material": "Acero"}}).json()
    assert e["inputs"] == {"Cotización!B1": "Acero"}
    assert client.post(f"/api/sheets/{wid}/evaluate", json={"inputs": {"Nada": 1}}).status_code == 400
    code = client.get(f"/api/sheets/{wid}/export.py")
    assert code.status_code == 200 and "def compute" in code.text
    assert client.get(f"/api/sheets/{wid}/trace", params={"cell": "Total"}).json()["formulas"]
    assert client.delete(f"/api/sheets/{up['id']}").json() == {"deleted": up["id"]}
    assert client.get(f"/api/sheets/{up['id']}").status_code == 404
    assert client.get("/api/sheets/..%2F..%2Fetc").status_code == 404
    assert client.get("/api/sheets/abc").status_code == 404


@need_engine
@pytest.mark.parametrize("mac", [False, True])
def test_epoch_and_literal_overrides_against_independent_engine(tmp_path, mac):
    from openpyxl.utils.datetime import MAC_EPOCH, WINDOWS_EPOCH, to_excel
    wb = openpyxl.Workbook()
    wb.epoch = MAC_EPOCH if mac else WINDOWS_EPOCH
    ws = wb.active
    ws.append([2026, 11, 3, "=DATE(A1,B1,C1)"])
    ws["A2"] = dt.datetime(2026, 11, 3, 12)
    ws["B2"] = "=A2+1"
    ws["B2"].number_format = "yyyy-mm-dd hh:mm"
    ws["C2"] = "=YEAR(A2)"
    ws["D2"] = "=EDATE(A2,1)"
    ws["A3"] = "=1+2"
    ws["A3"].data_type = "s"
    ws["B3"] = '=A3&"!"'
    path = tmp_path / f"independent-{mac}.xlsx"
    wb.save(path)
    model = load(path)
    serial = float(to_excel(dt.datetime(2026, 11, 8, 12), wb.epoch))
    result = verify(model, path, cases=[{}, {"Sheet!A2": serial, "Sheet!A3": "=9+1"}])
    assert result["status"] == "agree", result
    assert result["compared"] == 10 and result["agree"] == 10
