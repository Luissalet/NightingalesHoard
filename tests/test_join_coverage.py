"""Coverage is a count of left rows, distinct from the joined row count."""
import csv

from nightingale.agent_tools import call_tool_sync


def seed(svc, tmp_path):
    left, right = 'clave, ñ"', 'referencia, ú"'
    for name, columns, rows in (
        ("sales", [left, "zone", "amount"], [["A", "N", 1], ["A", "N", 2], ["B", "N", 3], [None, "N", 4]]),
        ("refs", [right, "area", "label"], [["A", "N", "one"], ["A", "N", "two"], ["C", "N", "three"], ["B", "S", "four"], [None, "N", "null"]]),
    ):
        path = tmp_path / f"{name}.csv"
        with path.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output)
            writer.writerow(columns)
            writer.writerows(rows)
        svc.ingest_file(str(path), name)
    return [{"left": left, "right": right}, {"left": "zone", "right": "area"}]


def metadata(svc):
    tables = svc.meta.db.query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    return {row["name"]: sorted([tuple(item) for item in svc.meta.db.query('SELECT * FROM "' + row["name"].replace('"', '""') + '"')], key=repr) for row in tables}


def test_coverage_native_api_and_agent_keep_saved_versions(client, tmp_path):
    svc = client.services
    keys = seed(svc, tmp_path)
    before = metadata(svc)
    expected = {"left_rows": 4, "matched": 2, "unmatched": 2, "match_rate": 0.5}
    assert svc.join_preview("sales", "refs", keys) == expected
    # Coverage is independent of how; NULL does not match NULL, and pairs use AND.
    assert client.post("/api/datasets/sales/join-preview", json={"other_dataset": "refs", "on": keys, "how": "full"}).json() == expected
    assert call_tool_sync(svc, "data_join_preview", {"dataset": "sales", "other_dataset": "refs", "on": keys}) == expected
    assert metadata(svc) == before


def test_join_preview_multiplicity_and_one_apply_preserve_originals(services, tmp_path):
    keys = seed(services, tmp_path)
    params = {"other_dataset": "refs", "on": keys, "how": "left"}
    right_before = services.preview("refs", limit=20)
    original = services.preview("sales", version=0, limit=20)
    assert services.transform_preview("sales", "join", params)["row_count_after"] == 6
    assert services.transform_preview("sales", "join", {**params, "how": "inner"})["row_count_after"] == 4
    assert services.recipe("sales")["current_version"] == 0
    result = services.transform_apply("sales", "join", params)
    assert result["current_version"] == 1
    assert result["row_count"] == 6
    for name, version, expected in (("sales", 0, original), ("refs", None, right_before)):
        actual = services.preview(name, version=version, limit=20)
        assert {k: v for k, v in actual.items() if k != "elapsed_ms"} == {k: v for k, v in expected.items() if k != "elapsed_ms"}
    assert services.recipe("refs")["current_version"] == 0
