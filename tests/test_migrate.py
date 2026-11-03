"""Migration rehearsal: every source row is loaded or rejected with a reason, reruns are idempotent, checks compare both sides."""
import sqlite3

import pytest

from nightingale import migrate as mg

CUSTOMERS = """id,name,balance,since
1,Acme,100.50,2020-01-05
2,Beta,200,2021-02-30
3,Gamma,abc,2022-03-04
1,Acme again,5,2022-04-04
,Nameless,7,2022-05-05
5,,9,2022-06-06
6,Zeta,10,2023-01-01
"""
ORDERS = """oid,customer,amount
10,1,50
11,1,25.5
12,6,10
13,99,5
14,,1
15,2,3
"""


VALS = "SELECT * FROM (VALUES (1, '  Ann ', '10.50'), (2, 'Bob', '3')) t(id, name, price)"


def spec(**over):
    s = {
        "name": "rehearsal1",
        "tables": [
            {"name": "orders", "source": "orders", "primary_key": ["oid"],
             "columns": [{"name": "oid", "type": "INTEGER"}, {"name": "customer", "type": "INTEGER"},
                         {"name": "amount", "type": "REAL"}],
             "foreign_keys": [{"columns": ["customer"], "ref_table": "customers", "ref_columns": ["id"]}]},
            {"name": "customers", "source": "customers", "primary_key": ["id"], "unique": [["name"]],
             "columns": [{"name": "id", "type": "INTEGER"},
                         {"name": "name", "type": "TEXT", "required": True},
                         {"name": "balance", "type": "REAL"},
                         {"name": "since", "type": "DATE"}]},
        ],
        "checks": {"totals": [], "queries": []},
    }
    s.update(over)
    return s


@pytest.fixture
def loaded(services):
    services.ingest_text(CUSTOMERS, "customers", "csv")
    services.ingest_text(ORDERS, "orders", "csv")
    return services


def table(res, name):
    return next(t for t in res["tables"] if t["table"] == name)


def test_rejections_by_each_reason_and_counts_reconcile(loaded):
    res = mg.rehearse_spec(loaded, spec(), sample=20)
    assert res["load_order"] == ["customers", "orders"]  # parents first even though listed last
    c = table(res, "customers")
    # row 3 'abc', row 2 date 2021-02-30 -> cast failures; row 5 missing id (pk); row 6 missing name;
    # row 4 repeats id 1 -> duplicate key
    assert c["source_rows"] == 7
    assert c["by_reason"]["cast_failure"] == 2
    assert c["by_reason"]["missing_required"] == 2
    assert c["by_reason"]["duplicate_primary_key"] == 1
    assert c["loaded"] == 2 and c["rejected"] == 5 and c["reconciled"]
    cast = {s["row"]: s for s in c["samples"]["cast_failure"]}
    assert cast[2]["column"] == "since" and cast[2]["value"] == "2021-02-30"
    assert cast[3]["column"] == "balance" and cast[3]["value"] == "abc"
    o = table(res, "orders")
    assert o["by_reason"]["foreign_key_not_found"] == 2  # customer 99, customer 2 (rejected parent)
    assert o["loaded"] == 4 and o["rejected"] == 2 and o["reconciled"]  # row 14: NULL fk is allowed
    assert res["summary"]["reconciled"] and res["summary"]["foreign_key_violations"] == 0
    lite = sqlite3.connect(res["file"])
    assert lite.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 2
    assert lite.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 4


def test_duplicate_unique_key(loaded):
    loaded.ingest_text("id,name\n1,A\n2,A\n3,B\n", "u", "csv")
    s = {"name": "u1", "tables": [{"name": "t", "source": "u", "primary_key": ["id"], "unique": [["name"]],
                                   "columns": [{"name": "id", "type": "INTEGER"}, {"name": "name", "type": "TEXT"}]}]}
    res = mg.rehearse_spec(loaded, s)
    t = table(res, "t")
    assert t["by_reason"]["duplicate_unique_key"] == 1 and t["loaded"] == 2 and t["samples"]["duplicate_unique_key"][0]["row"] == 2


def test_idempotent_rerun_and_rebuild(loaded):
    res = mg.rehearse_spec(loaded, spec())
    idem = {i["table"]: i for i in res["idempotency"]}
    assert res["summary"]["idempotent"]
    for name in ("customers", "orders"):
        assert idem[name]["unchanged"] and idem[name]["duplicate_keys"] == 0 and idem[name]["rows_before"] == idem[name]["rows_after"]
    again = mg.rehearse_spec(loaded, spec())  # re-run rebuilds the same file
    assert [t["loaded"] for t in again["tables"]] == [t["loaded"] for t in res["tables"]]
    lite = sqlite3.connect(again["file"])
    assert lite.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 2


def test_table_without_primary_key_is_not_claimed_idempotent(loaded):
    s = {"name": "nopk", "tables": [{"name": "t", "source": "orders", "columns": [{"name": "oid", "type": "INTEGER"}]}]}
    res = mg.rehearse_spec(loaded, s)
    assert res["idempotency"][0]["provable"] is False and res["summary"]["idempotent"] is False


def test_totals_pass_and_fail(loaded):
    checks = {"totals": [
        {"table": "orders", "source_expr": "SUM(TRY_CAST(amount AS DOUBLE))", "target_column": "amount"},
        {"table": "orders", "name": "loose", "source_expr": "SUM(TRY_CAST(amount AS DOUBLE))", "target_column": "amount", "tolerance": 100}]}
    res = mg.rehearse_spec(loaded, spec(checks=checks))
    strict, loose = res["totals"]
    assert not strict["passed"] and strict["source"] == 94.5 and strict["target"] == 86.5 and strict["difference"] == -8.0
    assert loose["passed"]
    # all rows loaded -> exact match
    s = {"name": "okt", "tables": [{"name": "o", "source": "orders", "primary_key": ["oid"],
                                    "columns": [{"name": "oid", "type": "INTEGER"}, {"name": "amount", "type": "REAL"}]}],
         "checks": {"totals": [{"table": "o", "source_expr": "SUM(amount)", "target_column": "amount"}]}}
    ok = mg.rehearse_spec(loaded, s)
    assert ok["totals"][0]["passed"] and ok["totals"][0]["difference"] == 0


def test_functional_query_mismatch_detected(loaded):
    s = {"name": "fq", "tables": [{"name": "o", "source": "orders", "primary_key": ["oid"],
                                   "columns": [{"name": "oid", "type": "INTEGER"}, {"name": "customer", "type": "INTEGER"},
                                               {"name": "amount", "type": "REAL"}]}],
         "checks": {"queries": [
             {"name": "same", "source_sql": "SELECT customer, SUM(amount) FROM orders WHERE customer IS NOT NULL GROUP BY 1",
              "target_sql": "SELECT customer, SUM(amount) FROM o WHERE customer IS NOT NULL GROUP BY 1"},
             {"name": "different", "source_sql": "SELECT COUNT(*) FROM orders",
              "target_sql": "SELECT COUNT(*) + 1 FROM o"}]}}
    res = mg.rehearse_spec(loaded, s)
    same, diff = res["queries"]
    assert same["passed"] and not diff["passed"]
    assert diff["only_in_source"] == [[6]] and diff["only_in_target"] == [[7]]
    bad = mg.rehearse_spec(loaded, {**s, "name": "fq2", "checks": {"queries": [
        {"name": "w", "source_sql": "SELECT 1", "target_sql": "DELETE FROM o"}]}})
    assert not bad["queries"][0]["passed"] and "SELECT" in bad["queries"][0]["error"]


def test_representation_column_that_alters_values_is_flagged(loaded):
    s = {"name": "rep", "tables": [{"name": "p", "source_sql": VALS, "primary_key": ["id"], "columns": [
        {"name": "id", "type": "INTEGER"},
        {"name": "name", "type": "TEXT", "expr": "trim(name)", "kind": "representation"},
        {"name": "price", "type": "REAL"}]}]}
    res = mg.rehearse_spec(loaded, s)
    cols = {c["column"]: c for c in table(res, "p")["columns"]}
    assert cols["name"]["flagged"] and cols["name"]["changed"] == 1
    assert cols["name"]["changed_examples"][0]["source"].strip() == "Ann"
    assert "declare it a correction" in cols["name"]["note"]
    assert not cols["id"]["flagged"] and not cols["price"].get("flagged")
    assert res["summary"]["flagged_columns"] == 1
    # the same expression declared as a correction is reported, not flagged
    s["tables"][0]["columns"][1]["kind"] = "correction"
    res2 = mg.rehearse_spec(loaded, s)
    name = {c["column"]: c for c in table(res2, "p")["columns"]}["name"]
    assert name["changed"] == 1 and "flagged" not in name and res2["summary"]["flagged_columns"] == 0


def test_format_only_roundtrip_differences_are_listed_not_flagged(loaded):
    s = {"name": "fmt", "tables": [{"name": "q", "source_sql": VALS, "primary_key": ["id"],
                                    "columns": [{"name": "id", "type": "INTEGER"}, {"name": "price", "type": "REAL"}]}]}
    price = {c["column"]: c for c in table(mg.rehearse_spec(loaded, s), "q")["columns"]}["price"]
    assert not price["flagged"] and price["roundtrip_format_only"] == 2  # '10.50' -> 10.5 and '3' -> 3.0
    assert price["format_only_examples"][0]["source"] == "10.50"


def test_cycle_and_bad_spec_are_refused(loaded):
    a = {"name": "a", "source": "orders", "primary_key": ["oid"], "columns": [{"name": "oid", "type": "INTEGER"}],
         "foreign_keys": [{"columns": ["oid"], "ref_table": "b", "ref_columns": ["oid"]}]}
    b = {**a, "name": "b", "foreign_keys": [{"columns": ["oid"], "ref_table": "a", "ref_columns": ["oid"]}]}
    with pytest.raises(mg.MigrateError, match="cycle"):
        mg.rehearse_spec(loaded, {"name": "cyc", "tables": [a, b]})
    with pytest.raises(mg.MigrateError, match="type must be"):
        mg.normalize_spec({"name": "x", "tables": [{"name": "t", "source": "s", "columns": [{"name": "c", "type": "BLOB"}]}]})
    with pytest.raises(mg.MigrateError, match="primary key or a unique key"):
        mg.normalize_spec(spec(tables=[
            {"name": "c", "source": "customers", "columns": [{"name": "id", "type": "INTEGER"}]},
            {"name": "o", "source": "orders", "columns": [{"name": "customer", "type": "INTEGER"}],
             "foreign_keys": [{"columns": ["customer"], "ref_table": "c", "ref_columns": ["id"]}]}]))
    with pytest.raises(mg.MigrateError, match="no column"):
        mg.rehearse_spec(loaded, {"name": "nocol", "tables": [{"name": "t", "source": "orders", "columns": [{"name": "zzz", "type": "TEXT"}]}]})


def test_source_is_untouched_and_rejected_saved_as_dataset(loaded):
    before = loaded.query('SELECT * FROM "customers"')["rows"]
    res = mg.rehearse_spec(loaded, spec(), save_rejected="rejected_rows")
    assert loaded.query('SELECT * FROM "customers"')["rows"] == before
    saved = loaded.query('SELECT reason, COUNT(*) AS n FROM "rejected_rows" GROUP BY reason ORDER BY reason')["rows"]
    got = {r["reason"]: r["n"] for r in saved}
    assert got == {"cast_failure": 2, "duplicate_primary_key": 1, "foreign_key_not_found": 2, "missing_required": 2}
    assert res["saved_rejected_dataset"] and res["rejected_total"] == 7


def test_source_sql_suggest_ddl_and_specs(loaded):
    s = {"name": "viaSql", "tables": [{"name": "big", "source_sql": 'SELECT oid, amount FROM "orders" WHERE oid > 11',
                                       "primary_key": ["oid"], "columns": [{"name": "oid", "type": "INTEGER"}, {"name": "amount", "type": "REAL"}]}]}
    assert table(mg.rehearse_spec(loaded, s), "big")["loaded"] == 4
    with pytest.raises(Exception):
        mg.rehearse_spec(loaded, {**s, "tables": [{**s["tables"][0], "source_sql": "DROP TABLE x"}]})
    sug = mg.suggest_spec(loaded, "orders")
    assert sug["suggestion"] and "suggestion" in sug["note"].lower()
    cols = {c["name"]: c for c in sug["spec"]["tables"][0]["columns"]}
    assert cols["oid"]["type"] == "INTEGER" and cols["amount"]["type"] == "REAL" and not cols["customer"]["required"]
    assert sug["spec"]["tables"][0]["primary_key"] == ["oid"]
    d = mg.ddl(loaded, spec())
    assert d["ddl"].index('CREATE TABLE "customers"') < d["ddl"].index('CREATE TABLE "orders"')
    assert 'FOREIGN KEY ("customer") REFERENCES "customers" ("id")' in d["ddl"]
    mg.save_spec(loaded, "keep", spec())
    assert [x["name"] for x in mg.list_specs(loaded)["specs"]] == ["keep"]
    assert mg.get_spec(loaded, "keep")["spec"]["tables"][0]["name"] == "orders"
    assert mg.rehearse_spec(loaded, name="keep")["summary"]["reconciled"]
    assert mg.list_specs(loaded)["specs"][0]["rehearsed"]
    assert mg.delete_spec(loaded, "keep")["deleted"] and mg.list_specs(loaded)["specs"] == []
    with pytest.raises(mg.MigrateError):
        mg.get_spec(loaded, "../etc")


def test_http_and_tool(client):
    client.services.ingest_text(CUSTOMERS, "customers", "csv")
    client.services.ingest_text(ORDERS, "orders", "csv")
    assert client.put("/api/migrate/specs/web", json=spec()).status_code == 200
    assert client.get("/api/migrate/specs").json()["specs"][0]["name"] == "web"
    r = client.post("/api/migrate/rehearse", json={"name": "web"})
    assert r.status_code == 200 and r.json()["summary"]["rejected"] == 7 and r.json()["log_id"]
    f = client.get("/api/migrate/file/web")
    assert f.status_code == 200 and f.content.startswith(b"SQLite format 3")
    assert client.post("/api/migrate/ddl", json={"name": "web"}).json()["ddl"].startswith("--")
    assert client.post("/api/migrate/ddl.sql", json={"name": "web"}).text.count("CREATE TABLE") == 2
    assert client.post("/api/migrate/suggest", json={"dataset": "orders"}).json()["suggestion"]
    assert client.post("/api/migrate/rehearse", json={"spec": {"name": "bad", "tables": []}}).status_code == 400
    assert client.get("/api/migrate/file/nothere").status_code == 404
    from nightingale.agent_tools import call_tool_sync
    out = call_tool_sync(client.services, "data_migrate", {"action": "rehearse", "name": "web"})
    assert out["summary"]["loaded"] == 6
    assert call_tool_sync(client.services, "data_migrate", {"action": "list"})["specs"]
    with pytest.raises(Exception):
        call_tool_sync(client.services, "data_migrate", {"action": "rehearse"})
    assert call_tool_sync(client.services, "data_migrate", {"action": "delete", "name": "web"})["deleted"]
    assert client.delete("/api/migrate/specs/web").status_code == 400
