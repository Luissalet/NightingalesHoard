"""Relato con los datos: an ordered list of findings computed from a dataset's
current version, each with its chart, one sentence that states the exact
figure the chart shows, and the query that produces it.

Nothing here is invented. Every finding comes from an aggregate query over the
dataset view, and only enters the story when it clears a stated minimum of
statistical support:

* ``trend`` — Pearson correlation of the period series against its index, at
  least five periods and p < 0.05, on the period total and on the period
  average of each numeric column, and again inside each of the largest groups
  of the first categorical columns (a total can hide a product that falls
  while the rest holds).
* ``groups`` — Welch's t-test between the highest and the lowest group, at
  least two groups of five rows each, p < 0.05 and a gap of at least 10 %.
  Run on the group average and on the group total: a product can lead the
  money without its average order being any bigger.
* ``outliers`` — rows outside 1.5 IQR, at least one and at most 10 % of the
  non-null values (beyond that it is the shape of the distribution, not an
  outlier).
* ``concentration`` — the largest category holds at least 40 % of the amount
  (or of the rows), or the three largest hold at least 60 %, with at least
  three categories.
* ``change`` — Welch's t-test between the two halves of the time span, at
  least ten rows each, p < 0.05 and a change of at least 10 %.

Findings are ordered strongest first by ``-log10(p) + size``: how unlikely the
pattern is under the null of its test, plus how large the effect is (the
correlation, the relative gap, the top-three share, the outlier share). A kind
with no test of its own (outliers) contributes its size alone.

The language model of the family may only rewrite a sentence over figures that
are already computed: :func:`verify_statement` re-reads every number in the
text and rejects the rewrite when one of them is not one of the finding's
figures, so the template stays. The chart data is the same aggregate the
sentence quotes, so the two cannot drift apart.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Optional

from .texts import CATALOGUE, t
from .workbench.charts import ChartSpec, run_chart, to_vega_lite
from .workbench.engine import Engine, is_numeric_type, is_temporal_type, q

__all__ = [
    "StoryError", "MIN_ROWS", "MAX_FINDINGS", "build_findings", "render_finding",
    "render_story", "verify_statement", "numbers_in", "parse_number", "format_number",
]

MIN_ROWS = 20
MIN_GROUP_ROWS = 5
MIN_HALF_ROWS = 10
MIN_PERIODS = 5
MIN_PERIOD_ROWS = 5
MIN_TREND_R = 0.5
TREND_GROUPS = 5
MAX_GROUPS = 12
MAX_FINDINGS = 8
ALPHA = 0.05
MIN_RELATIVE = 0.10
MAX_OUTLIER_SHARE = 0.10
MIN_TOP_SHARE = 0.40
MIN_TOP_THREE_SHARE = 0.60


class StoryError(ValueError):
    pass


# ---- numbers -----------------------------------------------------------------

def format_number(value: Optional[float], decimals: int = 2, lang: str = "en") -> str:
    """Thousands-grouped number with the marks of ``lang`` (es: 1.234,56)."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return ""
    text = f"{float(value):,.{decimals}f}"
    if lang == "es":
        text = text.translate(str.maketrans({",": "\u0000", ".": ","})).replace("\u0000", ".")
    return text


_NUMBER = re.compile(r"(?<![\w.])-?\d[\d.,]*")


def numbers_in(text: str) -> list[str]:
    """Every number written in ``text``, as it appears (1.234,56 / 1,234.56 / 62)."""
    found = []
    for match in _NUMBER.finditer(str(text or "").replace("\u2212", "-")):
        token = match.group(0).rstrip(".,")
        if token and token != "-":
            found.append(token)
    return found


def parse_number(token: str, lang: str = "en") -> Optional[float]:
    """Read a number written with the marks of ``lang``; None when it is not one."""
    text = str(token).replace("\u2212", "-").strip()
    if not text:
        return None
    if lang == "es":
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def verify_statement(text: str, figures: Iterable[dict], lang: str = "en",
                     extra: Iterable[str] = ()) -> dict:
    """Check that every number in ``text`` is one of the finding's figures.

    ``figures`` are ``{"value": float, "decimals": int}``; ``extra`` holds texts
    whose numbers are allowed too (period labels such as ``2024-01``). Returns
    ``{"ok": bool, "checked": int, "unsupported": [tokens]}`` — a rewrite with
    any unsupported number is discarded and the template stays.

    A sentence with no number at all is rejected too: the finding's sentence has
    to state the figure the chart shows, and a text that quotes none of them
    says nothing that can be checked.
    """
    allowed: set[float] = set()
    for figure in figures:
        value = figure.get("value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            allowed.add(round(float(value), 4))
    for chunk in extra:
        for token in numbers_in(chunk):
            parsed = parse_number(token, lang)
            if parsed is not None:
                allowed.add(round(parsed, 4))
    unsupported = []
    checked = 0
    for token in numbers_in(text):
        parsed = parse_number(token, lang)
        if parsed is None:
            continue
        checked += 1
        if round(parsed, 4) not in allowed:
            unsupported.append(token)
    return {"ok": not unsupported and checked > 0, "checked": checked, "unsupported": unsupported}


# ---- column roles ------------------------------------------------------------

def column_roles(columns: list[dict]) -> dict:
    """Split a version's columns into numeric, temporal and categorical ones."""
    numeric, temporal, categorical = [], [], []
    for column in columns:
        name, kind = column.get("name"), str(column.get("type") or "")
        if not name:
            continue
        if is_numeric_type(kind):
            numeric.append(name)
        elif is_temporal_type(kind):
            temporal.append(name)
        else:
            categorical.append(name)
    return {"numeric": numeric, "temporal": temporal, "categorical": categorical}


def _fig(key: str, value: Optional[float], decimals: int = 2) -> dict:
    """One figure of a finding: the key names it in the template, the value is
    rounded to what the sentence shows so the check compares like with like."""
    if value is None:
        return {"key": key, "value": None, "decimals": decimals}
    return {"key": key, "value": round(float(value), decimals), "decimals": decimals}


def _p_fig(p_value: float) -> dict:
    """The p-value as the sentence quotes it: rounded UP to four decimals and
    never below 0.0001, so "p <= {p}" is always true of the test that produced it."""
    ceiling = max(math.ceil(float(p_value) * 10000) / 10000, 0.0001)
    return _fig("p", ceiling, 4)


def _chart(spec: ChartSpec, rows: list[dict], columns: list[dict]) -> dict:
    data = {"rows": rows, "columns": columns}
    return {"spec": spec.to_dict(), "rows": rows, "columns": columns, "vega_lite": to_vega_lite(spec, data)}


def _query(sql: str) -> dict:
    return {"sql": sql, "operation": "data_query", "params": {"sql": sql}}


def _literal(value: Any) -> str:
    """A SQL string literal for a value that came out of the dataset itself."""
    return "'" + str(value).replace("'", "''") + "'"


def _strength(p_value: float, size: float) -> float:
    """Order findings: how unlikely the pattern is under the null, plus how big it is."""
    return -math.log10(max(float(p_value), 1e-12)) + min(abs(float(size)), 1.0)


def _welch(mean_a: float, sd_a: float, n_a: int, mean_b: float, sd_b: float, n_b: int) -> tuple[float, float]:
    """Welch's t statistic and two-sided p-value from summary statistics."""
    from scipy import stats

    var_a, var_b = (sd_a or 0.0) ** 2 / n_a, (sd_b or 0.0) ** 2 / n_b
    se = math.sqrt(var_a + var_b)
    if se <= 0:
        return 0.0, 1.0
    statistic = (mean_a - mean_b) / se
    denominator = (var_a ** 2) / max(n_a - 1, 1) + (var_b ** 2) / max(n_b - 1, 1)
    df = (var_a + var_b) ** 2 / denominator if denominator > 0 else max(n_a + n_b - 2, 1)
    return statistic, float(2 * stats.t.sf(abs(statistic), df))


def _welch_totals(sum_a: float, sd_a: float, n_a: int, sum_b: float, sd_b: float, n_b: int) -> tuple[float, float]:
    """Welch's test for the difference of two group totals: the variance of a
    total is n times the variance of one row, so a group that leads the money
    is tested on the money, not on the size of its average order."""
    from scipy import stats

    var_a, var_b = n_a * (sd_a or 0.0) ** 2, n_b * (sd_b or 0.0) ** 2
    se = math.sqrt(var_a + var_b)
    if se <= 0:
        return 0.0, 1.0
    statistic = (sum_a - sum_b) / se
    denominator = (var_a ** 2) / max(n_a - 1, 1) + (var_b ** 2) / max(n_b - 1, 1)
    df = (var_a + var_b) ** 2 / denominator if denominator > 0 else max(n_a + n_b - 2, 1)
    return statistic, float(2 * stats.t.sf(abs(statistic), df))


def _binom_sf(k: int, n: int, p: float) -> float:
    """P(X >= k) for X ~ Binomial(n, p): how unlikely the top category's lead is
    when every category had the same chance."""
    from scipy import stats

    if n <= 0 or not 0 < p < 1:
        return 1.0
    return float(stats.binom.sf(max(int(k) - 1, 0), int(n), p))


def _pearson(xs: list[float], ys: list[float]) -> tuple[float, float]:
    from scipy import stats

    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return 0.0, 1.0
    result = stats.pearsonr(xs, ys)
    return float(result[0]), float(result[1])


# ---- the five findings -------------------------------------------------------

def _trend(engine: Engine, dataset: str, columns: list[dict], roles: dict) -> list[dict]:
    findings = []
    for date_col in roles["temporal"][:2]:
        span = engine.query(
            f"SELECT MIN({q(date_col)}) AS lo, MAX({q(date_col)}) AS hi, COUNT(*) AS n "
            f"FROM {q(dataset)} WHERE {q(date_col)} IS NOT NULL", limit=1)
        if not span["rows"] or not span["rows"][0]["lo"]:
            continue
        lo, hi, total = span["rows"][0]["lo"], span["rows"][0]["hi"], span["rows"][0]["n"]
        if total < MIN_ROWS:
            continue
        days = _days_between(lo, hi)
        grain = "year" if days > 1100 else ("month" if days > 120 else "day")
        for value_col in roles["numeric"][:2]:
            for measure in ("sum", "avg"):
                finding = _trend_series(engine, dataset, date_col, value_col, grain, measure)
                if finding:
                    findings.append(finding)
        # a trend the whole dataset does not show can still be there inside one
        # group: the total hides a product that falls while the rest holds
        for group_col in roles["categorical"][:2]:
            for group in _top_groups(engine, dataset, group_col, TREND_GROUPS):
                for value_col in roles["numeric"][:2]:
                    finding = _trend_series(engine, dataset, date_col, value_col, grain, "sum",
                                            group_col=group_col, group=group)
                    if finding:
                        findings.append(finding)
    return findings


def _trend_series(engine: Engine, dataset: str, date_col: str, value_col: str, grain: str,
                  measure: str, group_col: Optional[str] = None, group: Any = None) -> Optional[dict]:
    """One period series and its Pearson test; None when it does not clear the support."""
    agg = "SUM" if measure == "sum" else "AVG"
    where = f"{q(date_col)} IS NOT NULL AND {q(value_col)} IS NOT NULL"
    if group_col is not None:
        where += f" AND {q(group_col)} = {_literal(group)}"
    sql = (f"SELECT date_trunc('{grain}', {q(date_col)}) AS period, {agg}({q(value_col)}) AS value, "
           f"COUNT(*) AS n FROM {q(dataset)} WHERE {where} GROUP BY 1 ORDER BY 1")
    series = engine.query(sql, limit=400)["rows"]
    if len(series) < MIN_PERIODS:
        return None
    # a period total over two or three rows is noise, not a trend: every period
    # has to hold enough rows for the figure to mean something
    if any(int(row["n"] or 0) < MIN_PERIOD_ROWS for row in series):
        return None
    values = [float(row["value"]) for row in series if row["value"] is not None]
    if len(values) < MIN_PERIODS:
        return None
    r, p = _pearson([float(i) for i in range(len(values))], values)
    if p >= ALPHA or abs(r) < MIN_TREND_R:
        return None
    labels = [_period_label(row["period"], grain) for row in series]
    first, last = values[0], values[-1]
    change = (last - first) / abs(first) if first else None
    if change is None:
        return None
    rows = [{"x": label, "y": round(value, 4)} for label, value in zip(labels, values)]
    spec = ChartSpec(kind="line", dataset=dataset, x=date_col, y=value_col, agg=measure,
                     title=t("story.title_trend", None, y=value_col, x=date_col))
    return {
        "id": f"trend:{value_col}:{date_col}:{measure}" + (f":{group}" if group is not None else ""),
        "kind": "trend",
        "columns": {"x": date_col, "y": value_col, "group": group_col},
        "measure": measure,
        "group": None if group is None else str(group),
        "grain": grain,
        "direction": "up" if change >= 0 else "down",
        "figures": [_fig("first", first), _fig("last", last), _fig("change", abs(change) * 100, 1),
                    _fig("periods", len(values), 0), _p_fig(p)],
        "evidence": {"test": "pearson", "r": round(r, 4), "p": round(p, 6), "periods": len(values),
                     "grain": grain, "measure": measure, "group": None if group is None else str(group)},
        "chart": _chart(spec, rows, [{"name": "x", "type": "VARCHAR"}, {"name": "y", "type": "DOUBLE"}]),
        "query": _query(sql),
        "strength": _strength(p, abs(r)),
        "extra_texts": [labels[0], labels[-1]] + ([str(group)] if group is not None else []),
    }


def _top_groups(engine: Engine, dataset: str, group_col: str, limit: int) -> list:
    """The largest groups of a column, by row count: enough rows for a series."""
    sql = (f"SELECT {q(group_col)} AS grp, COUNT(*) AS n FROM {q(dataset)} "
           f"WHERE {q(group_col)} IS NOT NULL GROUP BY 1 HAVING COUNT(*) >= {MIN_GROUP_ROWS} "
           f"ORDER BY n DESC LIMIT {int(limit)}")
    return [row["grp"] for row in engine.query(sql, limit=limit)["rows"]]


def _by_weight(engine: Engine, dataset: str, numeric: list[str]) -> list[str]:
    """The numeric columns ordered by their total, largest first: the column
    that carries the most is the one a reader means by "the amount"."""
    if len(numeric) < 2:
        return list(numeric)
    sums = ", ".join(f"SUM({q(name)}) AS c{index}" for index, name in enumerate(numeric))
    row = engine.query(f"SELECT {sums} FROM {q(dataset)}", limit=1)["rows"]
    if not row:
        return list(numeric)
    totals = {name: abs(float(row[0].get(f"c{index}") or 0)) for index, name in enumerate(numeric)}
    return sorted(numeric, key=lambda name: totals.get(name, 0.0), reverse=True)


def _groups(engine: Engine, dataset: str, columns: list[dict], roles: dict) -> list[dict]:
    findings = []
    for group_col in roles["categorical"][:3]:
        for value_col in roles["numeric"][:2]:
            for measure in ("avg", "sum"):
                finding = _group_gap(engine, dataset, group_col, value_col, measure)
                if finding:
                    findings.append(finding)
    return findings


def _group_gap(engine: Engine, dataset: str, group_col: str, value_col: str,
               measure: str) -> Optional[dict]:
    """The highest and the lowest group, on their average or on their total."""
    agg = "AVG" if measure == "avg" else "SUM"
    sql = (f"SELECT {q(group_col)} AS grp, {agg}({q(value_col)}) AS value, COUNT(*) AS n, "
           f"STDDEV_SAMP({q(value_col)}) AS sd FROM {q(dataset)} "
           f"WHERE {q(group_col)} IS NOT NULL AND {q(value_col)} IS NOT NULL "
           f"GROUP BY 1 HAVING COUNT(*) >= {MIN_GROUP_ROWS} ORDER BY value DESC")
    groups = engine.query(sql, limit=MAX_GROUPS)["rows"]
    if len(groups) < 2:
        return None
    high, low = groups[0], groups[-1]
    if high["value"] is None or low["value"] is None:
        return None
    test = _welch if measure == "avg" else _welch_totals
    statistic, p = test(float(high["value"]), float(high["sd"] or 0), int(high["n"]),
                        float(low["value"]), float(low["sd"] or 0), int(low["n"]))
    if p >= ALPHA:
        return None
    gap = float(high["value"]) - float(low["value"])
    relative = gap / abs(float(low["value"])) if low["value"] else None
    if relative is None or abs(relative) < MIN_RELATIVE:
        return None
    rows = [{"x": str(row["grp"]), "y": round(float(row["value"]), 4)} for row in groups]
    spec = ChartSpec(kind="bar", dataset=dataset, x=group_col, y=value_col, agg=measure,
                     title=t("story.title_groups", None, y=value_col, x=group_col))
    return {
        "id": f"groups:{value_col}:{group_col}:{measure}",
        "kind": "groups",
        "columns": {"group": group_col, "y": value_col},
        "measure": measure,
        "figures": [_fig("highest", float(high["value"])), _fig("lowest", float(low["value"])),
                    _fig("gap", gap), _fig("relative", abs(relative) * 100, 1),
                    _fig("groups", len(groups), 0), _p_fig(p)],
        "evidence": {"test": "welch", "t": round(statistic, 4), "p": round(p, 6), "groups": len(groups),
                     "high": str(high["grp"]), "low": str(low["grp"]), "measure": measure},
        "chart": _chart(spec, rows, [{"name": "x", "type": "VARCHAR"}, {"name": "y", "type": "DOUBLE"}]),
        "query": _query(sql),
        "strength": _strength(p, abs(relative)),
        "extra_texts": [str(high["grp"]), str(low["grp"])],
    }


def _outliers(engine: Engine, dataset: str, columns: list[dict], roles: dict) -> list[dict]:
    findings = []
    for value_col in roles["numeric"][:3]:
        sql = (f"WITH s AS (SELECT {q(value_col)} AS v FROM {q(dataset)} WHERE {q(value_col)} IS NOT NULL), "
               f"b AS (SELECT QUANTILE_CONT(v, 0.25) AS q1, QUANTILE_CONT(v, 0.75) AS q3, COUNT(*) AS n, "
               f"MIN(v) AS vmin, MAX(v) AS vmax FROM s) "
               f"SELECT b.q1, b.q3, b.n, b.vmin, b.vmax, "
               f"(SELECT COUNT(*) FROM s, b WHERE s.v > b.q3 + 1.5 * (b.q3 - b.q1) "
               f"OR s.v < b.q1 - 1.5 * (b.q3 - b.q1)) AS outliers FROM b")
        row = engine.query(sql, limit=1)["rows"]
        if not row:
            continue
        stats_row = row[0]
        total, outliers = int(stats_row["n"] or 0), int(stats_row["outliers"] or 0)
        if total < MIN_ROWS or outliers < 1:
            continue
        share = outliers / total
        if share > MAX_OUTLIER_SHARE:
            continue
        spec = ChartSpec(kind="box", dataset=dataset, y=value_col,
                         title=t("story.title_outliers", None, y=value_col))
        try:
            box = run_chart(engine, dataset, spec)
        except Exception:  # noqa: BLE001 - a box that cannot be drawn is not a finding
            continue
        findings.append({
            "id": f"outliers:{value_col}",
            "kind": "outliers",
            "columns": {"y": value_col},
            "figures": [_fig("outliers", outliers, 0), _fig("share", share * 100, 1),
                        _fig("rows", total, 0), _fig("max", float(stats_row["vmax"])),
                        _fig("q3", float(stats_row["q3"]))],
            "evidence": {"test": "iqr", "q1": stats_row["q1"], "q3": stats_row["q3"],
                         "outliers": outliers, "rows": total},
            "chart": _chart(spec, box["rows"], box["columns"]),
            "query": _query(sql),
            "strength": _strength(1.0, share),
            "extra_texts": [],
        })
    return findings


def _concentration(engine: Engine, dataset: str, columns: list[dict], roles: dict) -> list[dict]:
    findings = []
    for group_col in roles["categorical"][:3]:
        # the amount says more than the row count: try the numeric columns
        # first and fall back to counting rows only when none of them works
        for value_col in roles["numeric"][:2] + [None]:
            finding = _concentration_of(engine, dataset, group_col, value_col)
            if finding:
                findings.append(finding)
                break  # one concentration per column
    return findings


def _concentration_of(engine: Engine, dataset: str, group_col: str,
                      value_col: Optional[str]) -> Optional[dict]:
    """How much of the amount (or of the rows) the largest categories hold."""
    amount = f"SUM({q(value_col)})" if value_col else "COUNT(*)"
    sql = (f"SELECT {q(group_col)} AS grp, {amount} AS value, COUNT(*) AS n FROM {q(dataset)} "
           f"WHERE {q(group_col)} IS NOT NULL GROUP BY 1 ORDER BY value DESC")
    groups = engine.query(sql, limit=MAX_GROUPS)["rows"]
    if len(groups) < 3:
        return None
    total = sum(float(row["value"] or 0) for row in groups)
    rows_total = sum(int(row["n"]) for row in groups)
    if rows_total < MIN_ROWS or total <= 0:
        return None
    top = float(groups[0]["value"] or 0)
    top_three = sum(float(row["value"] or 0) for row in groups[:3])
    share, top_three_share = top / total, top_three / total
    if share < MIN_TOP_SHARE and top_three_share < MIN_TOP_THREE_SHARE:
        return None
    # the top category against an even split: how unlikely this lead is by chance
    p = _binom_sf(int(groups[0]["n"]), rows_total, 1 / len(groups))
    running = 0.0
    rows = []
    for row in groups:
        value = float(row["value"] or 0)
        running += value
        rows.append({"x": str(row["grp"]), "y": round(value / total * 100, 2),
                     "cumulative": round(running / total * 100, 2)})
    spec = ChartSpec(kind="pareto", dataset=dataset, x=group_col, y=value_col,
                     agg="sum" if value_col else "count",
                     title=t("story.title_concentration", None, x=group_col))
    figures = [_fig("top_share", share * 100, 1), _fig("top_three_share", top_three_share * 100, 1),
               _fig("top", int(groups[0]["n"]), 0), _fig("rows", rows_total, 0),
               _fig("groups", len(groups), 0)]
    if value_col:
        figures += [_fig("top_value", top), _fig("total", total)]
    return {
        "id": f"concentration:{group_col}:{value_col or 'rows'}",
        "kind": "concentration",
        "columns": {"group": group_col, "y": value_col},
        "measure": "sum" if value_col else "count",
        "figures": figures,
        "evidence": {"test": "share", "top": str(groups[0]["grp"]), "share": round(share, 4),
                     "top_three": round(top_three_share, 4), "groups": len(groups),
                     "rows": rows_total, "p": round(p, 6)},
        "chart": _chart(spec, rows, [{"name": "x", "type": "VARCHAR"}, {"name": "y", "type": "DOUBLE"},
                                     {"name": "cumulative", "type": "DOUBLE"}]),
        "query": _query(sql),
        "strength": _strength(p, top_three_share),
        "extra_texts": [str(groups[0]["grp"])],
    }


def _change(engine: Engine, dataset: str, columns: list[dict], roles: dict) -> list[dict]:
    findings = []
    for date_col in roles["temporal"][:2]:
        for value_col in roles["numeric"][:2]:
            sql = (f"WITH s AS (SELECT {q(date_col)} AS t, {q(value_col)} AS v FROM {q(dataset)} "
                   f"WHERE {q(date_col)} IS NOT NULL AND {q(value_col)} IS NOT NULL), "
                   f"m AS (SELECT MEDIAN(t) AS cut FROM s) "
                   f"SELECT CASE WHEN t <= (SELECT cut FROM m) THEN 'before' ELSE 'after' END AS half, "
                   f"AVG(v) AS mean, COUNT(*) AS n, STDDEV_SAMP(v) AS sd, MIN(t) AS t0, MAX(t) AS t1 "
                   f"FROM s GROUP BY 1")
            halves = {row["half"]: row for row in engine.query(sql, limit=2)["rows"]}
            before, after = halves.get("before"), halves.get("after")
            if not before or not after:
                continue
            if int(before["n"]) < MIN_HALF_ROWS or int(after["n"]) < MIN_HALF_ROWS:
                continue
            if before["mean"] is None or after["mean"] is None:
                continue
            statistic, p = _welch(float(after["mean"]), float(after["sd"] or 0), int(after["n"]),
                                  float(before["mean"]), float(before["sd"] or 0), int(before["n"]))
            if p >= ALPHA:
                continue
            delta = float(after["mean"]) - float(before["mean"])
            relative = delta / abs(float(before["mean"])) if before["mean"] else None
            if relative is None or abs(relative) < MIN_RELATIVE:
                continue
            rows = [{"x": t("story.half_before", None), "y": round(float(before["mean"]), 4)},
                    {"x": t("story.half_after", None), "y": round(float(after["mean"]), 4)}]
            spec = ChartSpec(kind="bar", dataset=dataset, x=date_col, y=value_col, agg="avg",
                             title=t("story.title_change", None, y=value_col))
            findings.append({
                "id": f"change:{value_col}:{date_col}",
                "kind": "change",
                "columns": {"x": date_col, "y": value_col},
                "direction": "up" if delta > 0 else "down",
                "figures": [_fig("before", float(before["mean"])), _fig("after", float(after["mean"])),
                            _fig("relative", abs(relative) * 100, 1), _p_fig(p)],
                "evidence": {"test": "welch", "t": round(statistic, 4), "p": round(p, 6),
                             "before_rows": int(before["n"]), "after_rows": int(after["n"])},
                "chart": _chart(spec, rows, [{"name": "x", "type": "VARCHAR"}, {"name": "y", "type": "DOUBLE"}]),
                "query": _query(sql),
                "strength": _strength(p, abs(relative)),
                "extra_texts": [],
            })
    return findings


def _days_between(lo: Any, hi: Any) -> float:
    from datetime import date, datetime

    def as_date(value):
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value)[:10]
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None

    first, last = as_date(lo), as_date(hi)
    if first is None or last is None:
        return 0.0
    return float((last - first).days)


def _period_label(value: Any, grain: str) -> str:
    text = str(value or "")
    if grain == "year":
        return text[:4]
    if grain == "month":
        return text[:7]
    return text[:10]


# ---- the story ---------------------------------------------------------------

def build_findings(engine: Engine, dataset: str, columns: list[dict], lang: str = "en") -> dict:
    """Every finding the dataset supports, strongest first, capped at MAX_FINDINGS."""
    roles = column_roles(columns)
    if not roles["numeric"]:
        raise StoryError(t("story.no_numeric", lang))
    total = engine.query(f"SELECT COUNT(*) AS n FROM {q(dataset)}", limit=1)["rows"][0]["n"]
    if int(total or 0) < MIN_ROWS:
        raise StoryError(t("story.too_few_rows", lang, rows=int(total or 0), minimum=MIN_ROWS))
    try:
        # the column that carries the most money is the one the story leads with
        roles["numeric"] = _by_weight(engine, dataset, roles["numeric"])
    except Exception:  # noqa: BLE001 - the column order is a preference, not a requirement
        pass

    found: list[dict] = []
    for builder in (_trend, _groups, _outliers, _concentration, _change):
        try:
            found.extend(builder(engine, dataset, columns, roles))
        except StoryError:
            raise
        except Exception:  # noqa: BLE001 - one unsupported column must not lose the rest of the story
            continue
    found.sort(key=lambda item: item["strength"], reverse=True)
    kept = _keep(found)
    for finding in kept:
        finding["statement"] = None
        finding["statement_source"] = "template"
        finding["statement_lang"] = lang
    return {"dataset": dataset, "rows": int(total or 0), "findings": kept,
            "considered": len(found), "roles": roles, "lang": lang}


def _keep(found: list[dict]) -> list[dict]:
    """The strongest findings, but one of every kind first: a story that says
    the same thing eight times is worse than one that says five different
    things, and a kind with a single finding (the outliers) would otherwise be
    pushed out by the many variants of a trend."""
    kept: list[dict] = []
    seen: set[tuple] = set()
    for finding in found:
        key = _dedupe_key(finding)
        if key in seen:
            continue
        kept.append(finding)
        seen.add(key)
        if len(kept) >= MAX_FINDINGS:
            return kept
    for finding in found:
        key = _dedupe_key(finding)
        if key in seen:
            continue
        kept.append(finding)
        seen.add(key)
        if len(kept) >= MAX_FINDINGS:
            break
    kept.sort(key=lambda item: item["strength"], reverse=True)
    return kept


def _dedupe_key(finding: dict) -> tuple:
    """What makes two findings the same story: one trend per group, one group
    comparison, one concentration per column, one outlier finding, one change."""
    kind = finding["kind"]
    if kind == "trend":
        return (kind, finding.get("group"))
    if kind == "groups":
        # the average and the total of two different columns are two findings
        return (kind, (finding.get("columns") or {}).get("group"),
                (finding.get("columns") or {}).get("y"))
    if kind == "concentration":
        return (kind, (finding.get("columns") or {}).get("group"))
    return (kind,)


def _label(key: str, lang: str) -> str:
    """The figure's name in ``lang``; the key itself when it has no entry yet."""
    return t(f"story.fig_{key}", lang) if f"story.fig_{key}" in CATALOGUE else key


def _title(finding: dict, lang: str) -> str:
    """The finding's title, naming the columns the way the sentence does."""
    columns = finding.get("columns", {})
    kind = finding["kind"]
    if kind == "trend" and finding.get("group"):
        return t("story.title_trend_group", lang, y=columns.get("y"), x=columns.get("x"),
                 group=finding["group"])
    if kind == "groups" and finding.get("measure") == "sum":
        return t("story.title_groups_total", lang, y=columns.get("y"), x=columns.get("group"))
    if kind == "concentration" and columns.get("y"):
        return t("story.title_concentration_amount", lang, x=columns.get("group"), y=columns.get("y"))
    return t(f"story.title_{kind}", lang, x=columns.get("x") or columns.get("group"),
             y=columns.get("y"))


def render_finding(finding: dict, lang: str = "en") -> dict:
    """The finding as the screen and the export show it: title, sentence and
    figures with their text and their label in ``lang``. A sentence written in
    another language (or by the model) is kept only for the language it was
    written in."""
    figures = {figure["key"]: figure for figure in finding.get("figures", [])}
    title = _title(finding, lang)
    template = _template(finding, lang, figures)
    stored = finding.get("statement")
    statement = stored if stored and finding.get("statement_lang") == lang else template
    chart = finding.get("chart") or {}
    if chart:
        # the chart carries the same title as the finding, in the same language
        chart = {**chart, "spec": {**(chart.get("spec") or {}), "title": title}}
        if chart.get("vega_lite"):
            chart["vega_lite"] = {**chart["vega_lite"], "title": title}
    return {
        **finding,
        "title": title,
        "template": template,
        "statement": statement,
        "statement_source": finding.get("statement_source") or "template",
        "chart": chart,
        "figures": [
            {**figure, "label": _label(figure["key"], lang),
             "text": format_number(figure.get("value"), figure.get("decimals", 2), lang)}
            for figure in finding.get("figures", [])
        ],
    }


def _template(finding: dict, lang: str, figures: dict) -> str:
    """The sentence the finding always has: the figures written exactly as the
    chart and the figure list show them, so the text and the picture agree."""
    def text(key: str, decimals: int = 2) -> str:
        figure = figures.get(key) or {}
        return format_number(figure.get("value"), figure.get("decimals", decimals), lang)

    kind = finding["kind"]
    columns = finding.get("columns", {})
    measure = t(f"story.measure_{finding.get('measure') or 'avg'}", lang)
    if kind == "trend":
        key = "story.trend_up" if finding.get("direction") == "up" else "story.trend_down"
        if finding.get("group"):
            key += "_group"
        return t(key, lang, y=columns.get("y"), x=columns.get("x"), group=finding.get("group") or "",
                 measure=measure, grain=t(f"story.grain_{finding.get('grain', 'month')}", lang),
                 first=text("first"), last=text("last"), change=text("change"),
                 periods=text("periods"), p=text("p"))
    if kind == "groups":
        key = "story.groups_total" if finding.get("measure") == "sum" else "story.groups"
        return t(key, lang, y=columns.get("y"), x=columns.get("group"),
                 high=finding["evidence"]["high"], low=finding["evidence"]["low"],
                 highest=text("highest"), lowest=text("lowest"), gap=text("gap"),
                 relative=text("relative"), groups=text("groups"), p=text("p"))
    if kind == "outliers":
        return t("story.outliers", lang, y=columns.get("y"), outliers=text("outliers"),
                 share=text("share"), rows=text("rows"), max=text("max"), q3=text("q3"))
    if kind == "concentration":
        if columns.get("y"):
            return t("story.concentration_amount", lang, x=columns.get("group"), y=columns.get("y"),
                     top=finding["evidence"]["top"], share=text("top_share"),
                     top_value=text("top_value"), total=text("total"),
                     top_three=text("top_three_share"), groups=text("groups"))
        return t("story.concentration_rows", lang, x=columns.get("group"), top=finding["evidence"]["top"],
                 share=text("top_share"), top_count=text("top"), rows=text("rows"),
                 top_three=text("top_three_share"), groups=text("groups"))
    if kind == "change":
        key = "story.change_up" if finding.get("direction") == "up" else "story.change_down"
        return t(key, lang, y=columns.get("y"), before=text("before"), after=text("after"),
                 relative=text("relative"), p=text("p"))
    return ""


def _short(finding: dict, lang: str) -> str:
    """The finding in one clause, built from the same figure texts the sentence
    quotes, so the headline and the summary cannot say anything new."""
    figures = {figure["key"]: figure.get("text") or "" for figure in finding.get("figures", [])}
    columns = finding.get("columns", {})
    kind = finding["kind"]
    verb = t("story.verb_up" if finding.get("direction") == "up" else "story.verb_down", lang)
    measure = t(f"story.measure_{finding.get('measure') or 'avg'}", lang)
    if kind == "trend":
        key = "story.short_trend_group" if finding.get("group") else "story.short_trend"
        return t(key, lang, y=columns.get("y"), group=finding.get("group") or "", verb=verb,
                 measure=measure, change=figures.get("change", ""), periods=figures.get("periods", ""))
    if kind == "groups":
        key = "story.short_groups_total" if finding.get("measure") == "sum" else "story.short_groups"
        return t(key, lang, y=columns.get("y"), high=finding["evidence"]["high"],
                 low=finding["evidence"]["low"], relative=figures.get("relative", ""))
    if kind == "concentration":
        key = "story.short_concentration_amount" if columns.get("y") else "story.short_concentration_rows"
        return t(key, lang, x=columns.get("group"), y=columns.get("y") or "",
                 top_three=figures.get("top_three_share", ""))
    if kind == "outliers":
        return t("story.short_outliers", lang, y=columns.get("y"), outliers=figures.get("outliers", ""),
                 rows=figures.get("rows", ""), share=figures.get("share", ""))
    if kind == "change":
        return t("story.short_change", lang, y=columns.get("y"), verb=verb,
                 relative=figures.get("relative", ""))
    return ""


def _sentence(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    return text[0].upper() + text[1:] + ("" if text.endswith(".") else ".")


def _lead_finding(story: dict, findings: list[dict]) -> Optional[dict]:
    """The finding the headline is about: the strongest one on the column that
    carries the most (the amount, not the row count), else the strongest."""
    if not findings:
        return None
    primary = (story.get("roles") or {}).get("numeric") or []
    if primary:
        for finding in findings:
            if (finding.get("columns") or {}).get("y") == primary[0]:
                return finding
    return findings[0]


def render_story(story: dict, lang: str = "en") -> dict:
    findings = [render_finding(finding, lang) for finding in story.get("findings", [])]
    lead = _lead_finding(story, findings)
    headline = ""
    if lead is not None:
        clause = _short(lead, lang)
        if clause:
            headline = t("story.headline", lang,
                         dataset=story.get("title") or story.get("dataset") or "", lead=clause)
    summary = []
    for finding in findings:
        if finding is lead:
            continue
        sentence = _sentence(_short(finding, lang))
        if sentence:
            summary.append(sentence)
        if len(summary) == 3:
            break
    return {**story, "findings": findings, "headline": headline, "summary": summary}