"""Descriptive statistics and hypothesis tests, backed by NumPy/SciPy.

Every test names itself, reports the standard numbers (statistic, p-value,
effect size where standard, n per group) and adds one neutral, textbook
sentence of interpretation — deliberately not a verdict, since only the
human knows what the numbers are for.

Data can be given as inline `data`/`data2` lists, or resolved from a
registered dataset + column via the `data` engine's `Catalog`.
"""
from __future__ import annotations

import math
import re
from typing import Any, Optional

import numpy as np
from scipy import stats as sp

from .data import Catalog, DataError

__all__ = ["run", "StatsError", "TESTS"]

TESTS = (
    "describe", "ttest_1samp", "ttest_ind", "ttest_rel", "mannwhitneyu",
    "wilcoxon", "chi2_contingency", "fisher_exact", "pearson", "spearman",
    "linregress", "proportion_ci", "normal_ci", "binom_test",
)


class StatsError(ValueError):
    pass


def _resolve_column(catalog: Catalog, dataset: str, column: str, where: Optional[str] = None) -> list[float]:
    safe_col = _quote_ident(column)
    sql = f'SELECT {safe_col} FROM {_quote_ident(dataset)} WHERE {safe_col} IS NOT NULL'
    if where:
        _validate_where(where)
        sql += f" AND ({where})"
    result = catalog.query_all(sql)
    return _floats([row[column] for row in result["rows"]], f"column {column!r}")


def _resolve_paired(
    catalog: Catalog, dataset: str, column: str, column2: str, where: Optional[str] = None
) -> tuple[list[float], list[float]]:
    """Like `_resolve_column` but for two columns at once, dropping a row
    when EITHER is NULL - not each column's NULLs independently, which used
    to leave two same-test samples of different lengths (e.g. "got 411 and
    406") purely because the NULLs happened to fall on different rows."""
    c1, c2 = _quote_ident(column), _quote_ident(column2)
    sql = f"SELECT {c1} AS a, {c2} AS b FROM {_quote_ident(dataset)} WHERE {c1} IS NOT NULL AND {c2} IS NOT NULL"
    if where:
        _validate_where(where)
        sql += f" AND ({where})"
    result = catalog.query_all(sql)
    d1: list[float] = []
    d2: list[float] = []
    for row in result["rows"]:
        try:
            a, b = float(row["a"]), float(row["b"])
        except (TypeError, ValueError) as exc:
            raise StatsError(f"columns {column!r}/{column2!r} must contain only numbers") from exc
        if a == a and b == b:  # skip NaN (float('nan') != float('nan'))
            d1.append(a)
            d2.append(b)
    return d1, d2


def _get_data_pair(
    data: Optional[list[float]],
    data2: Optional[list[float]],
    catalog: Optional[Catalog],
    dataset: Optional[str],
    column: Optional[str],
    column2: Optional[str],
    where: Optional[str],
) -> tuple[list[float], list[float]]:
    """Resolve two same-length samples for a paired/bivariate test.

    Inline `data`/`data2` are trusted as already paired (the caller sent
    them in matching order); a dataset's two columns are paired by row in
    the same query, so a NULL in either drops that row from both sides."""
    if data is not None or data2 is not None:
        return _floats(data or [], "data"), _floats(data2 or [], "data2")
    if dataset and column and column2:
        if catalog is None:
            raise StatsError("dataset/column given but no data catalog is available")
        return _resolve_paired(catalog, dataset, column, column2, where)
    raise StatsError("provide either inline data/data2 or dataset+column+column2")


def _resolve_grouped(catalog: Catalog, dataset: str, column: str, group_by: str, where: Optional[str] = None):
    safe_col = _quote_ident(column)
    safe_group = _quote_ident(group_by)
    sql = f"SELECT {safe_group} AS grp, {safe_col} AS val FROM {_quote_ident(dataset)} WHERE {safe_col} IS NOT NULL"
    if where:
        _validate_where(where)
        sql += f" AND ({where})"
    result = catalog.query_all(sql)
    groups: dict[str, list[float]] = {}
    for row in result["rows"]:
        groups.setdefault(str(row["grp"]), []).extend(_floats([row["val"]], f"column {column!r}"))
    return groups


def _quote_ident(name: str) -> str:
    if not name or not all(c.isalnum() or c == "_" for c in name):
        raise StatsError(f"invalid identifier: {name!r}")
    return f'"{name}"'


_WHERE_BANNED = re.compile(
    r";|--|/\*|\b(attach|detach|pragma|insert|update|delete|drop|create|copy|install|load|set|call|export|"
    r"select|read_csv|read_csv_auto|read_parquet|read_json|read_json_auto|read_text|read_blob|glob)\b",
    re.I,
)


def _validate_where(where: str) -> None:
    # The whole query still goes through the read-only SQL gate; this only keeps
    # `where` what the name says: a row filter such as "region = 'North' AND units > 3".
    if _WHERE_BANNED.search(where):
        raise StatsError("'where' may only be a simple row filter, e.g. \"region = 'North' AND units > 3\"")


def _floats(values: list, label: str) -> list[float]:
    out = []
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError) as exc:
            raise StatsError(f"{label} must contain only numbers; got {v!r}") from exc
        if f != f:
            continue
        out.append(f)
    return out


def _get_data(
    values: Optional[list[float]],
    catalog: Optional[Catalog],
    dataset: Optional[str],
    column: Optional[str],
    where: Optional[str],
) -> list[float]:
    if values is not None:
        return _floats(values, "data")
    if dataset and column:
        if catalog is None:
            raise StatsError("dataset/column given but no data catalog is available")
        return _resolve_column(catalog, dataset, column, where)
    raise StatsError("provide either inline data or dataset+column")


_EFFECT = {
    "difference": ("the difference is", "no statistically significant difference was found"),
    "correlation": ("the correlation is statistically different from zero", "no statistically significant correlation was found"),
    "slope": ("the slope is statistically different from zero", "no statistically significant linear trend was found"),
    "association": ("the association between the two variables is", "no statistically significant association was found"),
    "proportion": ("the observed proportion differs from p0", "no statistically significant difference from p0 was found"),
}


def _interp_p(p: float, alpha: float = 0.05, what: str = "difference") -> str:
    yes, no = _EFFECT[what]
    if not yes.endswith(("zero", "p0")):
        yes = f"{yes} statistically significant"
    if p < alpha:
        return (f"p = {p:.4g} < {alpha}: {yes} at the {int(alpha*100)}% level; "
                "this says nothing about effect size or cause.")
    return (f"p = {p:.4g} >= {alpha}: {no} at the {int(alpha*100)}% level; "
            "absence of evidence is not evidence of absence.")


def _need(d: list[float], n: int, test: str) -> None:
    if len(d) < n:
        raise StatsError(f"{test} needs at least {n} numeric values per sample, got {len(d)}")


def _finite(result: dict[str, Any]) -> dict[str, Any]:
    """NaN/inf cannot travel as JSON and mean the test was undefined for this data."""
    for key in ("statistic", "p_value", "r", "rho", "slope"):
        v = result.get(key)
        if isinstance(v, float) and not math.isfinite(v):
            raise StatsError(
                f"{result.get('test', 'the test')} is undefined for this data ({key} = {v}); "
                "typical causes: a constant sample (zero variance) or too few values"
            )
    for key, v in list(result.items()):
        if isinstance(v, float) and not math.isfinite(v):
            result[key] = None
    return result


def _describe(data: list[float]) -> dict[str, Any]:
    arr = np.asarray(data, dtype=float)
    n = arr.size
    if n == 0:
        raise StatsError("no data")
    q1, med, q3 = np.percentile(arr, [25, 50, 75])
    return {
        "n": int(n),
        "mean": float(np.mean(arr)),
        "sd": float(np.std(arr, ddof=1)) if n > 1 else 0.0,
        "median": float(med),
        "iqr": float(q3 - q1),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "skew": float(sp.skew(arr)) if n > 2 else None,
    }


def run(test: str, **kwargs: Any) -> dict[str, Any]:
    try:
        return _finite(_run(test, **kwargs))
    except (StatsError, DataError):
        raise
    except (ValueError, TypeError, ZeroDivisionError, FloatingPointError) as exc:
        raise StatsError(f"{test}: {exc}") from exc


def _run(
    test: str,
    *,
    data: Optional[list[float]] = None,
    data2: Optional[list[float]] = None,
    dataset: Optional[str] = None,
    column: Optional[str] = None,
    column2: Optional[str] = None,
    group_by: Optional[str] = None,
    where: Optional[str] = None,
    catalog: Optional[Catalog] = None,
    mu: float = 0.0,
    confidence: float = 0.95,
    successes: Optional[int] = None,
    trials: Optional[int] = None,
    p0: float = 0.5,
) -> dict[str, Any]:
    if test not in TESTS:
        raise StatsError(f"unknown test: {test}; choose one of {', '.join(TESTS)}")
    if not 0 < confidence < 1:
        raise StatsError("confidence must be between 0 and 1, e.g. 0.95")

    if test == "describe":
        d = _get_data(data, catalog, dataset, column, where)
        return {"test": "describe", **_describe(d)}

    if test == "ttest_1samp":
        d = _get_data(data, catalog, dataset, column, where)
        _need(d, 2, "ttest_1samp")
        stat, p = sp.ttest_1samp(d, mu)
        return {
            "test": "One-sample t-test", "statistic": float(stat), "p_value": float(p),
            "n": len(d), "mean": float(np.mean(d)), "mu": mu,
            "interpretation": _interp_p(float(p)),
        }

    if test in ("ttest_ind", "mannwhitneyu"):
        if group_by and dataset and column:
            groups = _resolve_grouped(catalog, dataset, column, group_by, where)
            if len(groups) != 2:
                found = sorted(groups)
                example = ", ".join("'" + str(g).replace("'", "''") + "'" for g in found[:2])
                raise StatsError(
                    f"group_by must select exactly 2 groups, found {len(groups)}: {found}; "
                    f"keep two with where, e.g. where=\"{group_by} IN ({example})\""
                )
            (name1, d1), (name2, d2) = sorted(groups.items())
        else:
            d1 = _get_data(data, catalog, dataset, column, where)
            d2 = _get_data(data2, catalog, dataset, column2, where)
            name1, name2 = "group1", "group2"
        _need(d1, 2, test)
        _need(d2, 2, test)
        if test == "ttest_ind":
            res = sp.ttest_ind(d1, d2, equal_var=False)
            pooled_sd = math.sqrt((np.var(d1, ddof=1) + np.var(d2, ddof=1)) / 2)
            cohens_d = (np.mean(d1) - np.mean(d2)) / pooled_sd if pooled_sd else 0.0
            return {
                "test": "Welch's two-sample t-test", "statistic": float(res.statistic),
                "p_value": float(res.pvalue), "df": float(res.df),
                "n1": len(d1), "n2": len(d2), "mean1": float(np.mean(d1)), "mean2": float(np.mean(d2)),
                "group1": name1, "group2": name2, "cohens_d": float(cohens_d),
                "interpretation": _interp_p(float(res.pvalue)),
            }
        res = sp.mannwhitneyu(d1, d2, alternative="two-sided")
        return {
            "test": "Mann-Whitney U", "statistic": float(res.statistic), "p_value": float(res.pvalue),
            "n1": len(d1), "n2": len(d2), "group1": name1, "group2": name2,
            "rank_biserial": float(1 - 2 * res.statistic / (len(d1) * len(d2))),
            "interpretation": _interp_p(float(res.pvalue)),
        }

    if test == "ttest_rel":
        d1, d2 = _get_data_pair(data, data2, catalog, dataset, column, column2, where)
        if len(d1) != len(d2):
            raise StatsError("paired t-test needs equal-length samples")
        res = sp.ttest_rel(d1, d2)
        return {
            "test": "Paired t-test", "statistic": float(res.statistic), "p_value": float(res.pvalue),
            "n": len(d1), "mean_diff": float(np.mean(np.array(d1) - np.array(d2))),
            "interpretation": _interp_p(float(res.pvalue)),
        }

    if test == "wilcoxon":
        one_sample = data2 is None and not column2
        if one_sample:
            d1 = _get_data(data, catalog, dataset, column, where)
            d2 = None
        else:
            d1, d2 = _get_data_pair(data, data2, catalog, dataset, column, column2, where)
        if d2 is not None and len(d1) != len(d2):
            raise StatsError("wilcoxon on two samples needs paired, equal-length samples")
        res = sp.wilcoxon([v - mu for v in d1]) if one_sample else sp.wilcoxon(d1, d2)
        return {
            "test": "Wilcoxon signed-rank" + (" (one sample vs mu)" if one_sample else " (paired)"),
            "statistic": float(res.statistic), "p_value": float(res.pvalue),
            "n": len(d1), "interpretation": _interp_p(float(res.pvalue)),
        }

    if test == "chi2_contingency":
        if data is None:
            raise StatsError("chi2_contingency needs 'data' as a 2D contingency table, e.g. [[10,20],[15,25]]")
        table = np.asarray(data, dtype=float)
        chi2, p, dof, expected = sp.chi2_contingency(table)
        return {
            "test": "Chi-squared test of independence", "statistic": float(chi2), "p_value": float(p),
            "dof": int(dof), "expected": expected.tolist(),
            "interpretation": _interp_p(float(p), what="association"),
        }

    if test == "fisher_exact":
        if data is None:
            raise StatsError("fisher_exact needs 'data' as a 2x2 table, e.g. [[8,2],[1,9]]")
        table = np.asarray(data, dtype=float)
        odds_ratio, p = sp.fisher_exact(table)
        return {
            "test": "Fisher's exact test", "odds_ratio": float(odds_ratio), "p_value": float(p),
            "interpretation": _interp_p(float(p), what="association"),
        }

    if test in ("pearson", "spearman", "linregress"):
        d1, d2 = _get_data_pair(data, data2, catalog, dataset, column, column2, where)
        if len(d1) != len(d2):
            raise StatsError(f"{test} needs two equal-length samples (x in data/column, y in data2/column2); got {len(d1)} and {len(d2)}")
        _need(d1, 3, test)
        if test == "pearson":
            r, p = sp.pearsonr(d1, d2)
            return {"test": "Pearson correlation", "r": float(r), "p_value": float(p), "n": len(d1),
                    "interpretation": _interp_p(float(p), what="correlation")}
        if test == "spearman":
            rho, p = sp.spearmanr(d1, d2)
            return {"test": "Spearman correlation", "rho": float(rho), "p_value": float(p), "n": len(d1),
                    "interpretation": _interp_p(float(p), what="correlation")}
        res = sp.linregress(d1, d2)
        t_crit = sp.t.ppf(1 - (1 - confidence) / 2, len(d1) - 2)
        return {
            "test": "Linear regression (least squares, y = slope*x + intercept)",
            "slope": float(res.slope), "intercept": float(res.intercept),
            "slope_ci_low": float(res.slope - t_crit * res.stderr),
            "slope_ci_high": float(res.slope + t_crit * res.stderr),
            "confidence": confidence,
            "r_squared": float(res.rvalue ** 2), "p_value": float(res.pvalue), "stderr": float(res.stderr),
            "n": len(d1), "interpretation": _interp_p(float(res.pvalue), what="slope"),
        }

    if test == "proportion_ci":
        _check_counts(successes, trials, "proportion_ci")
        lo, hi = _wilson_ci(successes, trials, confidence)
        return {
            "test": "Wilson score confidence interval", "proportion": successes / trials,
            "successes": successes, "trials": trials, "confidence": confidence, "ci_low": lo, "ci_high": hi,
        }

    if test == "normal_ci":
        d = _get_data(data, catalog, dataset, column, where)
        _need(d, 2, "normal_ci")
        arr = np.asarray(d, dtype=float)
        mean = float(np.mean(arr))
        sem = sp.sem(arr)
        lo, hi = sp.t.interval(confidence, len(arr) - 1, loc=mean, scale=sem)
        return {
            "test": "Mean confidence interval (t-distribution)", "mean": mean, "n": len(arr),
            "confidence": confidence, "ci_low": float(lo), "ci_high": float(hi),
        }

    if test == "binom_test":
        _check_counts(successes, trials, "binom_test")
        if not 0 <= p0 <= 1:
            raise StatsError("p0 must be a probability between 0 and 1")
        res = sp.binomtest(successes, trials, p0)
        return {
            "test": "Binomial test", "p_value": float(res.pvalue), "successes": successes,
            "trials": trials, "p0": p0, "proportion": successes / trials,
            "interpretation": _interp_p(float(res.pvalue), what="proportion"),
        }

    raise StatsError(f"unhandled test: {test}")


def _check_counts(successes: Optional[int], trials: Optional[int], test: str) -> None:
    if successes is None or trials is None:
        raise StatsError(f"{test} needs successes and trials (whole numbers), e.g. successes=42, trials=100")
    if trials <= 0 or successes < 0 or successes > trials:
        raise StatsError(f"{test} needs 0 <= successes <= trials and trials > 0; got {successes}/{trials}")


def _wilson_ci(successes: int, trials: int, confidence: float) -> tuple[float, float]:
    if trials <= 0:
        raise StatsError("trials must be positive")
    z = sp.norm.ppf(1 - (1 - confidence) / 2)
    p = successes / trials
    denom = 1 + z ** 2 / trials
    centre = p + z ** 2 / (2 * trials)
    adj = z * math.sqrt(p * (1 - p) / trials + z ** 2 / (4 * trials ** 2))
    lo = (centre - adj) / denom
    hi = (centre + adj) / denom
    return float(max(0.0, lo)), float(min(1.0, hi))
