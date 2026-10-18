"""Ask your data: a natural-language question answered by one SQL query the shared model writes.

No FastAPI imports here (`api.py` wires this to `POST /api/ui/data_ask`, UI
only — the agent already writes SQL itself via `data_query`, so there is no
new MCP tool for this). The model only ever sees each chosen dataset's
schema, per-column profile and up to 5 sample rows (`Catalog.describe`) —
never the full table — and must answer with exactly one fenced ```sql```
block. That query runs through the same read-only gate as every other query
(`Catalog.query`); a failing query gets exactly one retry with the error
message appended to the prompt.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from ..hoard_link import Link
from ..hoard_link.errors import BackendError, Unavailable
from .data import Catalog, DataError, is_numeric_type, is_temporal_type

__all__ = ["AskError", "ask", "suggest_chart"]

_SQL_FENCE = re.compile(r"```(?:sql)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)
_MAX_ROWS = 200
_MAX_PROMPT_CHARS = 6000

_SYSTEM_PROMPT = (
    "You write exactly one read-only SQL query (DuckDB dialect) to answer a "
    "question about the dataset(s) described below. Use only SELECT / WITH / "
    "DESCRIBE / SUMMARIZE / EXPLAIN - never a write statement. Reply with "
    "exactly one fenced ```sql code block containing the query, and nothing "
    "else outside it."
)


class AskError(DataError):
    """Every failure of the ask-your-data feature (a `DataError`, so `api.py`'s shared engine-error handling already covers it)."""


def _extract_sql(text: Optional[str]) -> Optional[str]:
    """Only a fenced ```sql block counts - free text is not risked as a query."""
    if not text:
        return None
    m = _SQL_FENCE.search(text)
    if not m:
        return None
    candidate = m.group(1).strip().rstrip(";").strip()
    return candidate or None


def _describe_for_prompt(catalog: Catalog, name: str) -> str:
    d = catalog.describe(name)
    lines = [f'Dataset "{d["name"]}" ({d["row_count"]} rows):']
    for c in d["columns"]:
        p = d["profile"].get(c["name"], {})
        bits = [c["type"]]
        if p.get("min") is not None:
            bits.append(f'range {p["min"]}..{p.get("max")}')
        if p.get("top_values"):
            top = ", ".join(f'{v["value"]}({v["count"]})' for v in p["top_values"][:3])
            bits.append(f"top: {top}")
        lines.append(f'  - {c["name"]}: {", ".join(bits)}')
    if d["sample_rows"]:
        cols = [c["name"] for c in d["columns"]]
        lines.append("  sample rows (" + " | ".join(cols) + "):")
        for row in d["sample_rows"]:
            lines.append("  " + " | ".join(str(row.get(c, "")) for c in cols))
    return "\n".join(lines)


def _schema_prompt(catalog: Catalog, names: list[str]) -> str:
    text = "\n\n".join(_describe_for_prompt(catalog, n) for n in names)
    if len(text) > _MAX_PROMPT_CHARS:
        text = text[:_MAX_PROMPT_CHARS] + "\n…"
    return text


def suggest_chart(columns: list[dict]) -> Optional[dict]:
    """A cheap heuristic guess at kind/x/y from the result columns, to prefill the Chart builder."""
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


async def ask(catalog: Catalog, link: Link, question: str, dataset_names: list[str]) -> dict:
    if not question or not question.strip():
        raise AskError("the question is empty")

    names = list(dataset_names) if dataset_names else [d["name"] for d in catalog.list_datasets()]
    if not names:
        raise AskError("no datasets are registered yet: add one on the Data screen first")
    for n in names:
        catalog.describe(n)  # raises a clear "unknown dataset" error early if a name is wrong

    resolution = await link.resolve("llm")
    if not resolution.resolved:
        raise AskError(f"no language model is available: {resolution.reason}")

    schema_text = _schema_prompt(catalog, names)

    def _messages(extra: str = "") -> list[dict[str, str]]:
        content = f"{schema_text}\n\nQuestion: {question}{extra}"
        return [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": content}]

    try:
        chat_result = await link.chat(_messages(), capability="llm", temperature=0.0, max_tokens=500, effort="high")
    except (Unavailable, BackendError) as exc:
        raise AskError(f"the language model call failed: {exc}") from exc

    sql = _extract_sql(chat_result.text)
    if not sql:
        raise AskError("the model did not answer with a SQL query in a fenced ```sql block")

    try:
        result = catalog.query(sql, limit=_MAX_ROWS)
    except DataError as first_error:
        retry_note = (
            f"\n\nYour previous query failed:\n{sql}\nError: {first_error}\n"
            "Fix it and answer again with exactly one fenced ```sql block."
        )
        try:
            chat_result = await link.chat(_messages(retry_note), capability="llm", temperature=0.0, max_tokens=500,
                                          effort="high")
        except (Unavailable, BackendError) as exc:
            raise AskError(f"the language model retry failed: {exc}") from exc
        sql = _extract_sql(chat_result.text)
        if not sql:
            raise AskError("the model did not answer with a SQL query on retry") from first_error
        result = catalog.query(sql, limit=_MAX_ROWS)  # a second failure is reported as-is, no further retry

    return {
        "question": question,
        "datasets": names,
        "sql": sql,
        "model": chat_result.model,
        "chart_suggestion": suggest_chart(result["columns"]),
        **result,
    }
