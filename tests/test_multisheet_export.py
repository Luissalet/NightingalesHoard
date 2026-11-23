"""data_export mode='sheets': several datasets into one new .xlsx, one sheet per dataset."""

import asyncio
from pathlib import Path

import openpyxl
import pytest

from nightingale.agent_tools import call_tool, tool_catalog
from nightingale.workbench.engine import DataError


@pytest.fixture
def trio(services, tmp_path):
    """Three small datasets with different column shapes."""
    files = {
        "ventas": "mes,tienda,importe,unidades\nenero,norte,10.5,2\nfebrero,sur,20.25,3\nmarzo,norte,4.25,1\n",
        "personal": "nombre,sueldo\nAna,1000\nLuis,1500\n",
        "notas": "clave,texto\nk1,=no es una formula\nk2,hola\n",
    }
    for name, text in files.items():
        path = tmp_path / f"{name}.csv"
        path.write_text(text, encoding="utf-8")
        services.ingest_file(str(path), name)
    return services


def _export(services, tmp_path, **args):
    out = tmp_path / "out" / "libro.xlsx"
    payload = {"dataset": "ventas", "format": "xlsx", "mode": "sheets", "path": str(out), **args}
    return asyncio.run(call_tool(services, "data_export", payload)), out


def _open(path):
    return openpyxl.load_workbook(path)


def test_one_sheet_per_dataset_in_order_with_bold_frozen_header(trio, tmp_path):
    result, out = _export(trio, tmp_path, sheet_name="Ventas 2026", extra_sheets=[
        {"dataset": "personal", "sheet_name": "Plantilla"}, {"dataset": "notas"}])
    wb = _open(out)
    assert wb.sheetnames == ["Ventas 2026", "Plantilla", "notas"]
    sales = wb["Ventas 2026"]
    assert [c.value for c in sales[1]] == ["mes", "tienda", "importe", "unidades"]
    assert all(c.font.bold for c in sales[1]) and sales.freeze_panes == "A2"
    assert [c.value for c in sales[2]] == ["enero", "norte", 10.5, 2]
    assert sales.max_row == 4
    assert [c.value for c in wb["Plantilla"][3]] == ["Luis", 1500]
    # a text that looks like a formula stays text
    assert wb["notas"]["B2"].value == "=no es una formula" and wb["notas"]["B2"].data_type == "s"
    assert [(s["sheet"], s["dataset"], s["row_count"]) for s in result["sheets"]] == [
        ("Ventas 2026", "ventas", 3), ("Plantilla", "personal", 2), ("notas", "notas", 2)]
    assert result["mode"] == "sheets" and result["row_count"] == 7 and result["path"] == str(out)


def test_totals_row_is_marked_and_sums_only_numeric_columns(trio, tmp_path):
    result, out = _export(trio, tmp_path, totals=True, extra_sheets=[
        {"dataset": "personal", "totals": True}, {"dataset": "notas"}])
    wb = _open(out)
    sales = wb["ventas"]
    last = sales.max_row
    assert last == 5
    label = sales.cell(last, 1).value
    assert label in ("Totals", "Totales")
    assert [c.value for c in sales[last]] == [label, None, 35.0, 6]
    assert all(c.font.bold and c.border.top.style == "thin" and c.fill.fgColor.rgb.endswith("E8EEF3") for c in sales[last])
    assert [c.value for c in wb["personal"][4]][1] == 2500
    assert wb["notas"].max_row == 3  # no totals asked for this sheet
    assert result["sheets"][0]["totals"]["sums"] == {"importe": 35.0, "unidades": 6}
    assert result["sheets"][2]["totals"] is None


@pytest.mark.parametrize("lang,label", [("en", "Totals"), ("es", "Totales")])
def test_totals_label_follows_the_request_language(client, tmp_path, lang, label):
    path = tmp_path / "t.csv"
    path.write_text("a,b\nx,1\ny,2\n", encoding="utf-8")
    client.services.ingest_file(str(path), "t")
    body = {"dataset": "t", "format": "xlsx", "mode": "sheets", "totals": True, "path": f"{lang}.xlsx"}
    reply = client.post("/api/export", params={"lang": lang}, json=body)
    assert reply.status_code == 200
    assert [c.value for c in _open(reply.json()["path"])["t"][4]] == [label, 3]


def test_all_numeric_sheet_still_gets_a_label_in_the_first_column(services, tmp_path):
    path = tmp_path / "n.csv"
    path.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    services.ingest_file(str(path), "n")
    _, out = _export(services, tmp_path, dataset="n", totals=True)
    row = [c.value for c in _open(out)["n"][4]]
    assert row[0] in ("Totals", "Totales") and row[1] == 6


def test_versions_choose_the_content_of_each_sheet(trio, tmp_path):
    trio.transform_apply("personal", "derive", {"name": "doble", "expr": "sueldo * 2"})
    _, out = _export(trio, tmp_path, dataset="personal", version=0, extra_sheets=[
        {"dataset": "personal", "sheet_name": "actual"}])
    wb = _open(out)
    assert wb.sheetnames == ["personal", "actual"]
    assert [c.value for c in wb["personal"][1]] == ["nombre", "sueldo"]
    assert [c.value for c in wb["actual"][1]] == ["nombre", "sueldo", "doble"]


def test_same_dataset_twice_without_names_gets_distinct_sheet_names(trio, tmp_path):
    trio.transform_apply("personal", "derive", {"name": "doble", "expr": "sueldo * 2"})
    result, out = _export(trio, tmp_path, dataset="personal", version=0, extra_sheets=[{"dataset": "personal"}])
    assert _open(out).sheetnames == ["personal", "personal_v1"]
    assert [s["dataset_version"] for s in result["sheets"]] == [0, 1]


@pytest.mark.parametrize("bad", ["a" * 32, "x/y", "x[1]", "x:y", "x*", "x?", "back\\slash", "'quoted", "quoted'", " pad",
                                 "History", "history", ""])
def test_a_sheet_name_Excel_would_refuse_is_refused_not_rewritten(trio, tmp_path, bad):
    with pytest.raises(DataError):
        trio.export("ventas", "xlsx", str(tmp_path / "x.xlsx"), source="agent", mode="sheets", sheet_name=bad)
    assert not (tmp_path / "x.xlsx").exists()


def test_duplicate_chosen_names_are_refused_ignoring_case(trio, tmp_path):
    with pytest.raises(DataError, match="same name"):
        trio.export("ventas", "xlsx", str(tmp_path / "x.xlsx"), source="agent", mode="sheets", sheet_name="Datos",
                    extra_sheets=[{"dataset": "personal", "sheet_name": "datos"}])


def test_unknown_dataset_or_version_in_extra_sheets_writes_nothing(trio, tmp_path):
    out = tmp_path / "x.xlsx"
    for extra in ({"dataset": "no_existe"}, {"dataset": "personal", "version": 99}):
        with pytest.raises(Exception):
            trio.export("ventas", "xlsx", str(out), source="agent", mode="sheets", extra_sheets=[extra])
        assert not out.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".export-")]


def test_sheets_options_are_refused_outside_sheets_mode_and_format(trio, tmp_path):
    for kwargs in ({"sheet_name": "a"}, {"totals": True}, {"extra_sheets": [{"dataset": "personal"}]}):
        with pytest.raises(DataError, match="mode='sheets'"):
            trio.export("ventas", "xlsx", str(tmp_path / "f.xlsx"), source="agent", mode="flat", **kwargs)
    with pytest.raises(DataError, match="xlsx"):
        trio.export("ventas", "csv", str(tmp_path / "f.csv"), source="agent", mode="sheets")
    with pytest.raises(DataError, match=r"\.xlsx"):
        trio.export("ventas", "xlsx", str(tmp_path / "f.csv"), source="agent", mode="sheets")
    with pytest.raises(DataError):
        trio.export("ventas", "xlsx", str(tmp_path / "f.xlsx"), source="agent", mode="sheets",
                    workbook_updates=[{"dataset": "personal"}])


def test_flat_xlsx_export_is_unchanged(trio, tmp_path):
    out = tmp_path / "flat.xlsx"
    asyncio.run(call_tool(trio, "data_export", {"dataset": "personal", "format": "xlsx", "path": str(out)}))
    wb = _open(out)
    assert wb.sheetnames == ["data"] and not wb["data"]["A1"].font.bold and wb["data"].freeze_panes is None


def test_undo_removes_the_new_workbook_and_restores_a_replaced_one(trio, tmp_path):
    first, out = _export(trio, tmp_path)
    prior = out.read_bytes()
    second, _ = _export(trio, tmp_path, totals=True, extra_sheets=[{"dataset": "personal"}])
    assert out.read_bytes() != prior
    assert asyncio.run(call_tool(trio, second["undo"]["tool"], second["undo"]["arguments"]))["undone"]
    assert out.read_bytes() == prior
    assert asyncio.run(call_tool(trio, first["undo"]["tool"], first["undo"]["arguments"]))["undone"]
    assert not out.exists()
    assert not list(out.parent.glob("*.workbook-export.json"))


def test_default_file_name_and_the_analysis_log(trio, tmp_path):
    (tmp_path / "dir").mkdir()
    result = trio.export("ventas", "xlsx", str(tmp_path / "dir"), source="agent", mode="sheets",
                         extra_sheets=[{"dataset": "personal"}])
    assert Path(result["path"]) == tmp_path / "dir" / "ventas_sheets.xlsx" and Path(result["path"]).exists()
    entry = next(e for e in trio.log_search(source="agent", dataset="ventas")["entries"] if e["op"] == "export")
    assert "personal" in entry["output_summary"] and "personal" in entry["input_json"]


def test_the_agent_contract_declares_exactly_the_new_arguments(trio):
    spec = next(tool for tool in tool_catalog() if tool["name"] == "data_export")
    props = spec["inputSchema"]["properties"]
    assert {"sheet_name", "totals", "extra_sheets"} <= set(props)
    assert "sheets" in props["mode"]["enum"]
    item = spec["inputSchema"]["$defs"]["SheetArgs"]["properties"]
    assert set(item) == {"dataset", "version", "sheet_name", "totals"}


def test_values_that_a_worksheet_cannot_hold_are_kept_as_text(services, tmp_path):
    path = tmp_path / "raros.csv"
    path.write_text("n,v\na,1\nb,2\n", encoding="utf-8")
    services.ingest_file(str(path), "raros")
    services.transform_apply("raros", "derive", {"name": "d", "expr": "CAST('nan' AS DOUBLE)"})
    result, out = _export(services, tmp_path, dataset="raros", totals=True)
    ws = _open(out)["raros"]
    assert ws["C2"].value == "nan"
    assert result["sheets"][0]["totals"]["sums"] == {"v": 3}
