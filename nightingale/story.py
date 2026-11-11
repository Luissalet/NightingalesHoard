"""Relato con los datos: an ordered list of findings computed from a dataset's
current version, each with its chart, one sentence that states the exact
figure the chart shows, and the query that produces it.

Nothing here is invented. Every finding comes from an aggregate query over the
dataset view, and only enters the story when it clears a stated minimum of
statistical support:

* ``trend`` — Pearson correlation of the period series against its index, at
  least five periods and p < 0.05.
* ``groups`` — Welch's t-test between the highest and the lowest group mean,
  at least two groups of five rows each, p < 0.05 and a gap of at least 10 %.
* ``outliers`` — rows outside 1.5 IQR, at least one and at most 10 % of the
  non-null values (beyond that it is the shape of the distribution, not an
  outlier).
* ``concentration`` — the largest category holds at least 40 % of the rows,
  with at least three categories.
* ``change`` — Welch's t-test between the two halves of the time span, at
  least ten rows each, p < 0.05 and a change of at least 10 %.

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

from .texts import t
from .workbench.charts import ChartSpec, run_chart, to_vega_lite
from .workbench.engine import Engine, is_numeric_type, is_temporal_type, q

__all__ = [
    "StoryError", "MIN_ROWS", "MAX_FINDINGS", "build_findings", "render_finding",
    "verify_statement", "numbers_in", "parse_number", "format_number",
]

MIN_ROWS = 20
MIN_GROUP_ROWS = 5
MIN_HALF_ROWS = 10
MAX_GROUPS = 12
MAX_FINDINGS = 8
ALPHA = 0.05
MIN_RELATIVE = 0.10
MAX_OUTLIER_SHARE = 0.10
MIN_TOP_SHARE = 0.40


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
            sql = (f"SELECT date_trunc('{grain}', {q(date_col)}) AS period, AVG({q(value_col)}) AS value, "
                   f"COUNT(*) AS n FROM {q(dataset)} "
                   f"WHERE {q(date_col)} IS NOT NULL AND {q(value_col)} IS NOT NULL "
                   f"GROUP BY 1 ORDER BY 1")
            series = engine.query(sql, limit=400)["rows"]
            if len(series) < 5:
                continue
            values = [float(row["value"]) for row in series if row["value"] is not None]
            if len(values) < 5:
                continue
            r, p = _pearson([float(i) for i in range(len(values))], values)
            if p >= ALPHA or abs(r) < 0.5:
                continue
            labels = [_period_label(row["period"], grain) for row in series]
            first, last = values[0], values[-1]
            change = (last - first) / abs(first) if first else None
            rows = [{"x": label, "y": round(value, 4)} for label, value in zip(labels, values)]
            spec = ChartSpec(kind="line", dataset=dataset, x=date_col, y=value_col, agg="avg",
                             title=t("story.title_trend", None, y=value_col, x=date_col))
            findings.append({
                "id": f"trend:{value_col}:{date_col}",
                "kind": "trend",
                "columns": {"x": date_col, "y": value_col},
                "grain": grain,
                "direction": "up" if r > 0 else "down",
                "figures": [_fig("first", first), _fig("last", last), _fig("change", change * 100 if change is not None else None, 1),
                            _fig("periods", len(values), 0), _p_fig(p)],
                "evidence": {"test": "pearson", "r": round(r, 4), "p": round(p, 6), "periods": len(values), "grain": grain},
                "chart": _chart(spec, rows, [{"name": "x", "type": "VARCHAR"}, {"name": "y", "type": "DOUBLE"}]),
                "query": _query(sql),
                "strength": -math.log10(max(p, 1e-12)) + abs(r),
                "extra_texts": [labels[0], labels[-1]],
            })
    return findings


def _groups(engine: Engine, dataset: str, columns: list[dict], roles: dict) -> list[dict]:
    findings = []
    for group_col in roles["categorical"][:3]:
        for value_col in roles["numeric"][:2]:
            sql = (f"SELECT {q(group_col)} AS grp, AVG({q(value_col)}) AS mean, COUNT(*) AS n, "
                   f"STDDEV_SAMP({q(value_col)}) AS sd FROM {q(dataset)} "
                   f"WHERE {q(group_col)} IS NOT NULL AND {q(value_col)} IS NOT NULL "
                   f"GROUP BY 1 HAVING COUNT(*) >= {MIN_GROUP_ROWS} ORDER BY mean DESC")
            groups = engine.query(sql, limit=MAX_GROUPS)["rows"]
            if len(groups) < 2:
                continue
            high, low = groups[0], groups[-1]
            if high["mean"] is None or low["mean"] is None:
                continue
            statistic, p = _welch(float(high["mean"]), float(high["sd"] or 0), int(high["n"]),
                                  float(low["mean"]), float(low["sd"] or 0), int(low["n"]))
            if p >= ALPHA:
                continue
            gap = float(high["mean"]) - float(low["mean"])
            relative = gap / abs(float(low["mean"])) if low["mean"] else None
            if relative is None or abs(relative) < MIN_RELATIVE:
                continue
            rows = [{"x": str(row["grp"]), "y": round(float(row["mean"]), 4)} for row in groups]
            spec = ChartSpec(kind="bar", dataset=dataset, x=group_col, y=value_col, agg="avg",
                             title=t("story.title_groups", None, y=value_col, x=group_col))
            findings.append({
                "id": f"groups:{value_col}:{group_col}",
                "kind": "groups",
                "columns": {"group": group_col, "y": value_col},
                "figures": [_fig("highest", float(high["mean"])), _fig("lowest", float(low["mean"])),
                            _fig("gap", gap), _fig("relative", relative * 100, 1),
                            _fig("groups", len(groups), 0), _p_fig(p)],
                "evidence": {"test": "welch", "t": round(statistic, 4), "p": round(p, 6),
                             "groups": len(groups), "high": str(high["grp"]), "low": str(low["grp"])},
                "chart": _chart(spec, rows, [{"name": "x", "type": "VARCHAR"}, {"name": "y", "type": "DOUBLE"}]),
                "query": _query(sql),
                "strength": -math.log10(max(p, 1e-12)) + abs(relative),
                "extra_texts": [str(high["grp"]), str(low["grp"])],
            })
    return findings


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
            "strength": share * 10,
            "extra_texts": [],
        })
    return findings


def _concentration(engine: Engine, dataset: str, columns: list[dict], roles: dict) -> list[dict]:
    findings = []
    for group_col in roles["categorical"][:3]:
        sql = (f"SELECT {q(group_col)} AS grp, COUNT(*) AS n FROM {q(dataset)} "
               f"WHERE {q(group_col)} IS NOT NULL GROUP BY 1 ORDER BY n DESC")
        groups = engine.query(sql, limit=MAX_GROUPS)["rows"]
        if len(groups) < 3:
            continue
        total = sum(int(row["n"]) for row in groups)
        if total < MIN_ROWS:
            continue
        top = int(groups[0]["n"])
        share = top / total
        if share < MIN_TOP_SHARE:
            continue
        top_three = sum(int(row["n"]) for row in groups[:3]) / total
        rows = [{"x": str(row["grp"]), "y": int(row["n"])} for row in groups]
        spec = ChartSpec(kind="bar", dataset=dataset, x=group_col, agg="count",
                         title=t("story.title_concentration", None, x=group_col))
        findings.append({
            "id": f"concentration:{group_col}",
            "kind": "concentration",
            "columns": {"group": group_col},
            "figures": [_fig("share", share * 100, 1), _fig("top", top, 0), _fig("rows", total, 0),
                        _fig("top_three", top_three * 100, 1), _fig("groups", len(groups), 0)],
            "evidence": {"test": "share", "top": str(groups[0]["grp"]), "share": round(share, 4),
                         "groups": len(groups), "rows": total},
            "chart": _chart(spec, rows, [{"name": "x", "type": "VARCHAR"}, {"name": "y", "type": "BIGINT"}]),
            "query": _query(sql),
            "strength": share,
            "extra_texts": [str(groups[0]["grp"])],
        })
    return findings


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
                            _fig("relative", relative * 100, 1), _p_fig(p)],
                "evidence": {"test": "welch", "t": round(statistic, 4), "p": round(p, 6),
                             "before_rows": int(before["n"]), "after_rows": int(after["n"])},
                "chart": _chart(spec, rows, [{"name": "x", "type": "VARCHAR"}, {"name": "y", "type": "DOUBLE"}]),
                "query": _query(sql),
                "strength": -math.log10(max(p, 1e-12)) + abs(relative),
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

    found: list[dict] = []
    for builder in (_trend, _groups, _outliers, _concentration, _change):
        try:
            found.extend(builder(engine, dataset, columns, roles))
        except StoryError:
            raise
        except Exception:  # noqa: BLE001 - one unsupported column must not lose the rest of the story
            continue
    found.sort(key=lambda item: item["strength"], reverse=True)
    kept = found[:MAX_FINDINGS]
    for finding in kept:
        finding["statement"] = None
        finding["statement_source"] = "template"
        finding["statement_lang"] = lang
    return {"dataset": dataset, "rows": int(total or 0), "findings": kept,
            "considered": len(found), "roles": roles}


def render_finding(finding: dict, lang: str = "en") -> dict:
    """The finding as the screen and the export show it: title, sentence and
    figures with their text in ``lang``. A sentence written in another language
    (or by the model) is kept only for the language it was written in."""
    figures = {figure["key"]: figure for figure in finding.get("figures", [])}
    columns = finding.get("columns", {})
    # the titles name the two columns the same way whatever the finding calls them
    title = t(f"story.title_{finding['kind']}", lang, x=columns.get("x") or columns.get("group"),
              y=columns.get("y"))
    template = _template(finding, lang, figures)
    stored = finding.get("statement")
    statement = stored if stored and finding.get("statement_lang") == lang else template
    return {
        **finding,
        "title": title,
        "template": template,
        "statement": statement,
        "statement_source": finding.get("statement_source") or "template",
        "figures": [
            {**figure, "text": format_number(figure.get("value"), figure.get("decimals", 2), lang)}
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
    if kind == "trend":
        key = "story.trend_up" if finding.get("direction") == "up" else "story.trend_down"
        return t(key, lang, y=columns.get("y"), x=columns.get("x"),
                 grain=t(f"story.grain_{finding.get('grain', 'month')}", lang),
                 first=text("first"), last=text("last"), change=text("change"),
                 periods=text("periods"), p=text("p"))
    if kind == "groups":
        return t("story.groups", lang, y=columns.get("y"), x=columns.get("group"),
                 high=finding["evidence"]["high"], low=finding["evidence"]["low"],
                 highest=text("highest"), lowest=text("lowest"), gap=text("gap"),
                 relative=text("relative"), groups=text("groups"), p=text("p"))
    if kind == "outliers":
        return t("story.outliers", lang, y=columns.get("y"), outliers=text("outliers"),
                 share=text("share"), rows=text("rows"), max=text("max"), q3=text("q3"))
    if kind == "concentration":
        return t("story.concentration", lang, x=columns.get("group"), top=finding["evidence"]["top"],
                 share=text("share"), top_count=text("top"), rows=text("rows"),
                 top_three=text("top_three"), groups=text("groups"))
    if kind == "change":
        key = "story.change_up" if finding.get("direction") == "up" else "story.change_down"
        return t(key, lang, y=columns.get("y"), before=text("before"), after=text("after"),
                 relative=text("relative"), p=text("p"))
    return ""


def render_story(story: dict, lang: str = "en") -> dict:
    return {**story, "findings": [render_finding(finding, lang) for finding in story.get("findings", [])]}