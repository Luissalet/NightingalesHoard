"""Quality rules: define/run/report for every rule kind."""

import asyncio
import pytest

from nightingale.agent_tools import call_tool


@pytest.fixture
def ds(services, tmp_path):
    path = tmp_path / "q.csv"
    path.write_text(
        "id,email,age,status,updated\n"
        "1,a@x.com,20,active,2024-01-01\n"
        "2,,150,pending,2024-01-02\n"
        "3,b@x.com,30,unknown,2024-01-03\n",
        encoding="utf-8",
    )
    services.ingest_file(str(path), "q")
    return "q"


def test_not_null(services, ds):
    services.quality_define(ds, "email present", "not_null", {"column": "email"})
    result = services.quality_run(ds)
    r = result["results"][0]
    assert r["passed"] is False and r["failed"] == 1


def test_unique(services, ds):
    services.quality_define(ds, "id unique", "unique", {"columns": ["id"]})
    result = services.quality_run(ds)
    assert result["results"][0]["passed"] is True


def test_refresh_runs_saved_rules_and_reports_new_failures(services, tmp_path):
    path = tmp_path / "weekly.csv"
    path.write_text("id,status\n1,active\n2,active\n", encoding="utf-8")
    services.ingest_file(str(path), "weekly")
    rule = services.quality_define("weekly", "id unique", "unique", {"column": "id"})
    path.write_text("id,status\n1,active\n1,active\n", encoding="utf-8")
    refreshed = services.refresh("weekly", source="agent")
    assert refreshed["row_count"] == 2
    assert refreshed["quality"]["checked_rules"] == 1
    failure = refreshed["quality"]["failed_rules"][0]
    assert failure["rule_id"] == rule["rule_id"] and failure["failed"] == 2
    assert failure["sample"]
    latest = services.quality_report("weekly")["rules"][0]["last_result"]
    assert latest["passed"] == 0 and latest["failed"] == 2
    path.write_text("id,status\n1,active\n2,active\n", encoding="utf-8")
    repaired = services.refresh("weekly")
    assert repaired["quality"]["passed_rules"] == 1
    assert repaired["quality"]["failed_rules"] == []
    assert services.quality_report("weekly")["rules"][0]["last_result"]["passed"] == 1


def test_agent_refresh_can_skip_quality_checks(services, tmp_path):
    path = tmp_path / "weekly.csv"
    path.write_text("id\n1\n", encoding="utf-8")
    services.ingest_file(str(path), "weekly")
    services.quality_define("weekly", "id unique", "unique", {"column": "id"})
    path.write_text("id\n1\n1\n", encoding="utf-8")
    result = asyncio.run(call_tool(services, "data_refresh", {"dataset": "weekly", "check_quality": False}))
    assert result["row_count"] == 2 and "quality" not in result
    assert services.quality_report("weekly")["rules"][0]["last_result"] is None


def test_refresh_keeps_new_data_when_saved_rule_no_longer_applies(services, tmp_path):
    path = tmp_path / "weekly.csv"
    path.write_text("id\n1\n", encoding="utf-8")
    services.ingest_file(str(path), "weekly")
    services.quality_define("weekly", "id unique", "unique", {"column": "id"})
    path.write_text("key\na\nb\n", encoding="utf-8")
    result = services.refresh("weekly")
    assert result["row_count"] == 2
    assert result["quality"]["error"]
    assert services.preview("weekly")["rows"][0]["key"] == "a"


def test_accepted_values(services, ds):
    services.quality_define(ds, "status ok", "accepted_values", {"column": "status", "values": ["active", "pending"]})
    result = services.quality_run(ds)
    assert result["results"][0]["passed"] is False


def test_range(services, ds):
    services.quality_define(ds, "age range", "range", {"column": "age", "min": 0, "max": 120})
    result = services.quality_run(ds)
    assert result["results"][0]["failed"] == 1


def test_regex(services, ds):
    services.quality_define(ds, "email format", "regex", {"column": "email", "pattern": "^[^@]+@[^@]+$"})
    result = services.quality_run(ds)
    assert result["results"][0]["failed"] == 0  # nulls are ignored by the rule, only 2 non-null emails checked


def test_row_count(services, ds):
    services.quality_define(ds, "min rows", "row_count", {"min": 10})
    result = services.quality_run(ds)
    assert result["results"][0]["passed"] is False


def test_freshness(services, ds):
    services.quality_define(ds, "fresh", "freshness", {"column": "updated", "max_age_days": 1})
    result = services.quality_run(ds)
    assert result["results"][0]["passed"] is False  # 2024 dates are long past "1 day old" from now()


def test_referential(services, ds, tmp_path):
    path = tmp_path / "statuses.csv"
    path.write_text("status\nactive\npending\n", encoding="utf-8")
    services.ingest_file(str(path), "statuses")
    services.quality_define(ds, "status exists", "referential",
                              {"column": "status", "ref_table": "statuses", "ref_column": "status"})
    result = services.quality_run(ds)
    assert result["results"][0]["failed"] == 1  # "unknown" has no match


def test_custom_sql(services, ds):
    services.quality_define(ds, "custom", "custom_sql", {"sql": "SELECT * FROM __table__ WHERE age > 100"})
    result = services.quality_run(ds)
    assert result["results"][0]["failed"] == 1


def test_report_and_delete(services, ds):
    r = services.quality_define(ds, "rule1", "not_null", {"column": "email"})
    report = services.quality_report(ds)
    assert len(report["rules"]) == 1
    services.quality_delete(r["rule_id"])
    report2 = services.quality_report(ds)
    assert len(report2["rules"]) == 0


def test_run_with_no_rules_raises(services, ds):
    with pytest.raises(Exception):
        services.quality_run(ds)


# ---- 'column'/'columns' accepted interchangeably on every rule kind -------


def test_unique_accepts_singular_column_alias(services, ds):
    # the real-world failure this guards against: a caller reaches for the
    # natural singular word even though 'columns' is unique's own key.
    services.quality_define(ds, "id unique (alias)", "unique", {"column": "id"})
    result = services.quality_run(ds)
    assert result["results"][0]["passed"] is True


def test_not_null_accepts_plural_columns_alias(services, ds):
    services.quality_define(ds, "email present (alias)", "not_null", {"columns": "email"})
    result = services.quality_run(ds)
    assert result["results"][0]["failed"] == 1


def test_not_null_accepts_single_item_list(services, ds):
    services.quality_define(ds, "email present (list)", "not_null", {"column": ["email"]})
    result = services.quality_run(ds)
    assert result["results"][0]["failed"] == 1


def test_single_column_rule_rejects_multiple_columns(services, ds):
    with pytest.raises(Exception):
        services.quality_define(ds, "bad", "range", {"columns": ["age", "id"], "min": 0})


def test_missing_column_and_columns_raises(services, ds):
    with pytest.raises(Exception):
        services.quality_define(ds, "bad", "not_null", {})
