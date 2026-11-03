"""Reconciliation of two datasets: nothing dropped, nothing multiplied, zero kept apart from missing."""
import duckdb
import pytest

from nightingale import reconcile as rec

CRM = """code,name,balance,city
C001,Acme S.L.,1200.0,Madrid
C002,Beta SA,0,Bilbao
C003,Gamma,,Sevilla
C004,Delta,300,
C004,Delta bis,300,Vigo
,Sin código,50,León
C006,Zeta,75.5,Cádiz
"""
ERP = """cliente,razon_social,saldo,ciudad
c001 ,ACME SL,1200.00,Madrid
C002,Beta SA,,Bilbao
C003,Gamma,0.00,Sevilla
C004,Delta,300.00,
C005,Epsilon,10,Gijón
C006,Zeta,75.49,Cádiz
"""


@pytest.fixture
def con():
    c = duckdb.connect()
    c.execute("""CREATE TABLE a AS SELECT * FROM (VALUES ('k1', 1.0), ('k2', 0.0), ('k3', NULL), ('k4', 5.0),
                 ('k4', 5.0), (NULL, 9.0)) t(id, v)""")
    c.execute("""CREATE TABLE b AS SELECT * FROM (VALUES ('k1', 1.0), ('k2', NULL), ('k3', 0.0), ('k4', 5.0),
                 ('k9', 2.0)) t(id, v)""")
    yield c
    c.close()


def test_every_row_lands_in_exactly_one_bucket(con):
    r = rec.reconcile(con, "a", "b", [{"left": "id", "right": "id"}])
    assert r["accounted"]
    L, R = r["left"], r["right"]
    assert (L["rows"], L["paired"], L["only_left"], L["ambiguous"], L["missing_key"]) == (6, 3, 0, 2, 1)
    assert (R["rows"], R["paired"], R["only_right"], R["ambiguous"]) == (5, 3, 1, 1)
    assert L["duplicate_keys"] == 1 and L["extra_copies"] == 1
    cats = r["columns"][0]["categories"]
    assert cats["equal"] == 1 and cats["zero_vs_missing"] == 2  # k2 0 vs NULL, k3 NULL vs 0
    assert r["samples"]["only_right"][0]["id"] == "k9"
    assert r["samples"]["missing_key_left"][0]["v"] == 9.0


def test_keys_are_never_guessed(con):
    with pytest.raises(rec.ReconcileError):
        rec.reconcile(con, "a", "b", [])
    with pytest.raises(rec.ReconcileError):
        rec.reconcile(con, "a", "b", [{"left": "nope", "right": "id"}])


def test_export_table_keeps_every_row(con):
    r = rec.reconcile(con, "a", "b", [{"left": "id", "right": "id"}], [], None, 0)
    rows = con.execute(rec.rows_table_sql(r, ["id", "v"], ["id", "v"])).fetchall()
    statuses = [x[0] for x in rows]
    # 3 pairs + 2 ambiguous left + 1 missing-key left + 1 ambiguous right + 1 only right
    assert len(rows) == 8 and statuses.count("paired") == 3 and statuses.count("ambiguous") == 3


def test_datasets_end_to_end(services):
    services.ingest_text(CRM, "crm", "csv")
    services.ingest_text(ERP, "erp", "csv")
    s = rec.suggest_datasets(services, "crm", "erp")
    best = {m["left"]: (m["candidates"][0]["right"] if m["candidates"] else None) for m in s["mapping"]}
    assert best["code"] == "cliente" and best["balance"] == "saldo" and best["city"] == "ciudad"
    assert any(k["left"] == "code" and k["strength"] == "weak" for k in s["key_candidates"])
    exact = rec.reconcile_datasets(services, "crm", "erp", [{"left": "code", "right": "cliente"}],
                                   [{"left": "balance", "right": "saldo"}])
    assert exact["pairs"] == 3 and exact["left"]["only_left"] == 1  # 'c001 ' does not match C001 exactly
    loose = rec.reconcile_datasets(services, "crm", "erp", [{"left": "code", "right": "cliente"}],
                                   [{"left": "balance", "right": "saldo"}, {"left": "city", "right": "ciudad"}],
                                   {"trim": True, "case_insensitive": True, "tolerance": 0.05}, save_as="crm_vs_erp")
    assert loose["pairs"] == 4 and loose["right"]["only_right"] == 1
    bal = loose["columns"][0]["categories"]
    assert bal["equal"] == 1 and bal["equal_within_tolerance"] == 1 and bal["zero_vs_missing"] == 2
    assert loose["saved_dataset"]
    saved = services.query(f'SELECT status, COUNT(*) AS n FROM "{loose["saved_dataset"]}" GROUP BY status ORDER BY status')
    counts = {row["status"]: row["n"] for row in saved["rows"]}
    assert counts == {"ambiguous": 3, "missing_key": 1, "only_right": 1, "paired": 4}
    assert loose["log_id"]


def test_http_and_tool(client):
    client.services.ingest_text(CRM, "crm", "csv")
    client.services.ingest_text(ERP, "erp", "csv")
    r = client.post("/api/reconcile/suggest", json={"left": "crm", "right": "erp"})
    assert r.status_code == 200 and r.json()["key_candidates"]
    body = {"left": "crm", "right": "erp", "keys": [{"left": "code", "right": "cliente"}], "options": {"trim": True, "case_insensitive": True}}
    r = client.post("/api/reconcile/run", json=body)
    assert r.status_code == 200 and r.json()["pairs"] == 4
    r = client.post("/api/reconcile/export.csv", json=body)
    assert r.status_code == 200 and r.text.splitlines()[0].startswith("status,left_row,right_row")
    assert len(r.text.strip().splitlines()) == 1 + 9
    r = client.post("/api/reconcile/run", json={**body, "keys": [{"left": "x", "right": "cliente"}]})
    assert r.status_code == 400
    from nightingale.agent_tools import call_tool_sync
    out = call_tool_sync(client.services, "data_reconcile", {"action": "run", "left": "crm", "right": "erp",
                                                            "keys": [{"left": "code", "right": "cliente"}]})
    assert out["pairs"] == 3
    with pytest.raises(Exception):
        call_tool_sync(client.services, "data_reconcile", {"action": "run", "left": "crm", "right": "erp"})
