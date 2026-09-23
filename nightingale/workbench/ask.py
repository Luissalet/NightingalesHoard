"""Ask your data: a natural-language question answered by one SQL query the
shared model writes, then run through the same read-only gate as `data_query`.

Adapted from Laplace's Hoard's `engines/ask.py` (same author, MIT): the
model only ever sees each dataset's schema, per-column profile and a few
sample rows — never the full table — and must answer with exactly one
fenced ```sql``` block. A failing query gets one retry with the error
appended. Nightingale additionally proposes a small *plan* (the SQL plus an
optional chart spec) that the caller applies only on confirmation — nothing
here writes to the workbench itself.
"""

from __future__ import annotations

import re
from typing import Optional

from ..hoard_link import Link
from ..hoard_link.errors import BackendError, Unavailable
from .charts import ChartSpec
from .engine import Engine, is_numeric_type, is_temporal_type

__all__ = ["AskError", "ask", "suggest_chart"]

_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)
_MAX_ROWS = 200
_MAX_PROMPT_CHARS = 6000

_SYSTEM_PROMPT = (
    "You write exactly one read-only SQL query (DuckDB dialect) over the dataset view(s) described "
    "below to answer a question. Use only SELECT / WITH / DESCRIBE / SUMMARIZE - never a write "
    "statement, and query the dataset by the view name given, not a file path. Reply with exactly "
    "one fenced ```sql code block containing the query, and nothing else outside it."
)


class AskError(ValueError):
    pass


def _extract_sql(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    m = _SQL_FENCE.search(text)
    if not m:
        return None
    candidate = m.group(1).strip().rstrip(";").strip()
    return candidate or None


def _describe_for_prompt(engine: Engine, view_name: str, columns: list[dict]) -> str:
    profile = engine.profile_table(view_name, columns)
    sample = engine.sample_rows(view_name, [c["name"] for c in columns], limit=5)
    lines = [f'Dataset "{view_name}":']
    for c in columns:
        p = profile.get(c["name"], {})
        bits = [c["type"]]
        if p.get("min") is not None:
            bits.append(f'range {p["min"]}..{p.get("max")}')
        if p.get("top_values"):
            top = ", ".join(f'{v["value"]}({v["count"]})' for v in p["top_values"][:3])
            bits.append(f"top: {top}")
        lines.append(f'  - {c["name"]}: {", ".join(bits)}')
    if sample:
        cols = [c["name"] for c in columns]
        lines.append("  sample rows (" + " | ".join(cols) + "):")
        for row in sample:
            lines.append("  " + " | ".join(str(row.get(c, "")) for c in cols))
    return "\n".join(lines)


def suggest_chart(columns: list[dict]) -> Optional[dict]:
    names = [c["name"] for c in columns]
    numeric = [c["name"] for c in columns if is_numeric_type(c["type"])]
    temporal = [c["name"] for c in columns if is_temporal_type(c["type"])]
    text = [n for n in names if n not in numeric and n not in temporal]
    if temporal and numeric:
        return {"kind": "line", "x": temporal[0], "y": numeric[0]}
    if text and numeric:
        return {"kind": "bar", "x": text[0], "y": numeric[0]}
    if len(numeric) >= 2:
        return {"kind": "scatter", "x": numeric[0], "y": numeric[1]}
    return None


async def ask(engine: Engine, link: Link, question: str, dataset_views: dict[str, list[dict]]) -> dict:
    if not question or not question.strip():
        raise AskError("the question is empty")
    if not dataset_views:
        raise AskError("no datasets are registered yet: ingest one first")

    schema_text = "\n\n".join(_describe_for_prompt(engine, name, cols) for name, cols in dataset_views.items())
    if len(schema_text) > _MAX_PROMPT_CHARS:
        schema_text = schema_text[:_MAX_PROMPT_CHARS] + "\n…"

    resolution = await link.resolve("llm")
    if not resolution.resolved:
        raise AskError(f"no language model is available: {resolution.reason}")

    def messages(extra: str = "") -> list[dict[str, str]]:
        return [{"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"{schema_text}\n\nQuestion: {question}{extra}"}]

    try:
        chat_result = await link.chat(messages(), capability="llm", temperature=0.0, max_tokens=500)
    except (Unavailable, BackendError) as exc:
        raise AskError(f"the language model call failed: {exc}") from exc

    sql = _extract_sql(chat_result.text)
    if not sql:
        raise AskError("the model did not answer with a SQL query in a fenced ```sql block")

    try:
        result = engine.query(sql, limit=_MAX_ROWS)
    except Exception as first_error:  # noqa: BLE001
        retry_note = (
            f"\n\nYour previous query failed:\n{sql}\nError: {first_error}\n"
            "Fix it and answer again with exactly one fenced ```sql block."
        )
        try:
            chat_result = await link.chat(messages(retry_note), capability="llm", temperature=0.0, max_tokens=500)
        except (Unavailable, BackendError) as exc:
            raise AskError(f"the language model retry failed: {exc}") from exc
        sql = _extract_sql(chat_result.text)
        if not sql:
            raise AskError("the model did not answer with a SQL query on retry") from first_error
        result = engine.query(sql, limit=_MAX_ROWS)

    return {
        "question": question, "datasets": list(dataset_views), "sql": sql, "model": chat_result.model,
        "chart_suggestion": suggest_chart(result["columns"]), **result,
    }
