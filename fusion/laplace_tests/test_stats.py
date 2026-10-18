from pathlib import Path

import pytest
import scipy.stats as sp

from laplaces_hoard.engines import stats
from laplaces_hoard.engines.data import Catalog


def test_welch_ttest_matches_scipy_exactly():
    d1, d2 = [1, 2, 3, 4, 5], [2, 3, 4, 5, 9]
    r = stats.run("ttest_ind", data=d1, data2=d2)
    ref = sp.ttest_ind(d1, d2, equal_var=False)
    assert r["statistic"] == pytest.approx(ref.statistic)
    assert r["p_value"] == pytest.approx(ref.pvalue)
    assert r["df"] == pytest.approx(ref.df)


def test_describe_basic_stats():
    r = stats.run("describe", data=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert r["n"] == 10
    assert r["mean"] == 5.5
    assert r["median"] == 5.5


def test_pearson_correlation():
    r = stats.run("pearson", data=[1, 2, 3, 4], data2=[2, 4, 6, 9])
    ref = sp.pearsonr([1, 2, 3, 4], [2, 4, 6, 9])
    assert r["r"] == pytest.approx(ref.statistic)


def test_proportion_ci_is_wilson_score_and_json_safe():
    r = stats.run("proportion_ci", successes=45, trials=100)
    assert isinstance(r["ci_low"], float)
    assert isinstance(r["ci_high"], float)
    assert 0.3 < r["ci_low"] < r["proportion"] < r["ci_high"] < 0.6


def test_stats_from_dataset_column_with_group_by(data_dir: Path, sample_csv: Path):
    cat = Catalog(data_dir)
    cat.register(str(sample_csv), name="sample")
    r = stats.run(
        "ttest_ind", dataset="sample", column="amount", group_by="region", catalog=cat,
        where="region IN ('North', 'South')",
    )
    assert r["n1"] == 2 and r["n2"] == 2


def test_where_clause_rejects_injection_attempts(data_dir: Path, sample_csv: Path):
    cat = Catalog(data_dir)
    cat.register(str(sample_csv), name="sample")
    with pytest.raises(stats.StatsError):
        stats.run(
            "ttest_ind", dataset="sample", column="amount", group_by="region", catalog=cat,
            where="1=1; DROP TABLE sample",
        )


def test_unknown_test_name_is_rejected():
    with pytest.raises(stats.StatsError):
        stats.run("not_a_real_test", data=[1, 2, 3])


# -- regressions found in review ------------------------------------------

def test_undefined_test_is_an_error_not_a_nan():
    # a constant sample used to produce NaN, which crashed the JSON response
    with pytest.raises(stats.StatsError, match="undefined"):
        stats.run("pearson", data=[1, 1, 1], data2=[1, 2, 3])


def test_impossible_counts_are_rejected():
    with pytest.raises(stats.StatsError, match="successes <= trials"):
        stats.run("proportion_ci", successes=5, trials=3)
    with pytest.raises(stats.StatsError, match="successes"):
        stats.run("binom_test", successes=None, trials=10)


def test_correlation_interpretation_talks_about_correlation():
    r = stats.run("pearson", data=[1, 2, 3, 4, 5, 6], data2=[2, 4, 5, 8, 10, 12])
    assert "correlation" in r["interpretation"]
    assert "difference" not in r["interpretation"]


def test_linregress_reports_a_slope_confidence_interval():
    x = [1, 2, 3, 4, 5, 6, 7, 8]
    y = [2.1, 3.9, 6.2, 7.8, 10.1, 12.2, 13.8, 16.1]
    r = stats.run("linregress", data=x, data2=y)
    ref = sp.linregress(x, y)
    t = sp.t.ppf(0.975, len(x) - 2)
    assert r["slope_ci_low"] == pytest.approx(ref.slope - t * ref.stderr)
    assert r["slope_ci_high"] == pytest.approx(ref.slope + t * ref.stderr)


def test_wilcoxon_one_sample_against_mu():
    r = stats.run("wilcoxon", data=[5.1, 4.9, 5.6, 5.8, 6.0, 6.2, 5.9, 6.4], mu=5.0)
    ref = sp.wilcoxon([v - 5.0 for v in [5.1, 4.9, 5.6, 5.8, 6.0, 6.2, 5.9, 6.4]])
    assert r["p_value"] == pytest.approx(ref.pvalue)


def test_non_numeric_data_is_an_actionable_error():
    with pytest.raises(stats.StatsError, match="only numbers"):
        stats.run("describe", data=[1, "two", 3])


def test_where_filter_rejects_subqueries_but_allows_column_names_with_keywords(tmp_path):
    import csv
    from laplaces_hoard.engines.data import Catalog

    path = tmp_path / "t.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["last_update", "v"])
        for i in range(10):
            w.writerow([i, i * 2])
    cat = Catalog(tmp_path / "data")
    cat.register(str(path))
    r = stats.run("describe", dataset="t", column="v", where="last_update > 4", catalog=cat)
    assert r["n"] == 5
    with pytest.raises(stats.StatsError):
        stats.run("describe", dataset="t", column="v", where="v IN (SELECT 1)", catalog=cat)
