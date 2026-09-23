"""Quality rules: define/run/report for every rule kind."""

import pytest


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
