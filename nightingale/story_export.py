"""Export a story: one self-contained HTML file, and — when Cicero's Hoard
answers through the Hub — a presentation with one finding per slide.

The HTML carries everything it needs: the sentences, the figures and each
chart as an inline PNG rendered by the same matplotlib path the Charts page
uses, so the file opens on a machine with no Nightingale, no network and no
JavaScript. The presentation is built with Cicero's own tools (``deck_create``
then ``slides_add``), one slide per finding, and its chart blocks carry the
same categories and values the sentence quotes.
"""

from __future__ import annotations

import base64
import html
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import story as story_mod
from .hoard_link import family
from .texts import t
from .workbench.charts import ChartSpec, render_png

__all__ = ["render_html", "write_html", "build_presentation", "cicero_available"]

CICERO_APP = "cicero"


def _png(spec_dict: dict, rows: list[dict], columns: list[dict], lang: str) -> str:
    """The finding's chart as a base64 PNG, drawn from the rows the sentence quotes."""
    spec = ChartSpec.from_dict(spec_dict)
    try:
        png = render_png(spec, {"rows": rows, "columns": columns}, lang=lang)
    except Exception:  # noqa: BLE001 - a chart that cannot be drawn leaves the figures, not a broken page
        return ""
    return base64.b64encode(png).decode("ascii")


def _figure_rows(finding: dict) -> str:
    cells = []
    for figure in finding.get("figures", []):
        if figure.get("value") is None:
            continue
        cells.append(
            f'<tr><th scope="row">{html.escape(str(figure["key"]))}</th>'
            f'<td class="num">{html.escape(str(figure.get("text") or ""))}</td></tr>'
        )
    if not cells:
        return ""
    return f'<table class="figures"><tbody>{"".join(cells)}</tbody></table>'


def render_html(story: dict, lang: str = "en", generated_at: Optional[str] = None) -> str:
    """The whole story as one standalone HTML document."""
    rendered = story_mod.render_story(story, lang)
    stamp = generated_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    title = rendered.get("title") or rendered.get("dataset") or ""
    parts = []
    for index, finding in enumerate(rendered.get("findings", []), start=1):
        chart = finding.get("chart") or {}
        png = _png(chart.get("spec") or {}, chart.get("rows") or [], chart.get("columns") or [], lang)
        image = (f'<img class="chart" alt="{html.escape(finding.get("title") or "")}" '
                 f'src="data:image/png;base64,{png}">') if png else ""
        query = finding.get("query") or {}
        parts.append(f"""
    <section class="finding">
      <h2><span class="index">{index}</span>{html.escape(finding.get("title") or "")}</h2>
      <p class="statement">{html.escape(finding.get("statement") or "")}</p>
      {image}
      {_figure_rows(finding)}
      <details>
        <summary>{html.escape(t("story.export_query", lang))}</summary>
        <p class="op">{html.escape(str(query.get("operation") or ""))}</p>
        <pre>{html.escape(str(query.get("sql") or ""))}</pre>
      </details>
    </section>""")
    body = "".join(parts) or f'<p class="empty">{html.escape(t("story.no_findings", lang))}</p>'
    return f"""<!doctype html>
<html lang="{html.escape(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
  :root {{ --ink:#241419; --dim:#6b5560; --line:#e3d6da; --accent:#7a1f4a; --gold:#e0a048; --soft:#fbf6f8; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:40px 24px 64px; background:#fff; color:var(--ink);
         font:15px/1.6 "Source Sans 3", -apple-system, "Segoe UI", Roboto, Arial, sans-serif; }}
  main {{ max-width:820px; margin:0 auto; }}
  header {{ border-bottom:2px solid var(--accent); padding-bottom:16px; margin-bottom:32px; }}
  h1 {{ font:600 26px/1.2 "Cinzel", Georgia, serif; margin:0 0 6px; }}
  .meta {{ color:var(--dim); font-size:13px; }}
  .finding {{ border-top:1px solid var(--line); padding:24px 0 8px; }}
  .finding:first-of-type {{ border-top:0; }}
  h2 {{ font-size:17px; font-weight:600; margin:0 0 8px; display:flex; gap:10px; align-items:baseline; }}
  .index {{ color:var(--gold); font-variant-numeric:tabular-nums; }}
  .statement {{ margin:0 0 14px; }}
  .chart {{ display:block; width:100%; max-width:640px; height:auto; border:1px solid var(--line); border-radius:6px; }}
  .figures {{ border-collapse:collapse; margin:14px 0 0; font-size:13px; }}
  .figures th {{ text-align:left; font-weight:500; color:var(--dim); padding:3px 16px 3px 0; }}
  .figures td {{ padding:3px 0; }}
  .num {{ font-variant-numeric:tabular-nums; }}
  details {{ margin-top:14px; font-size:13px; }}
  summary {{ cursor:pointer; color:var(--accent); }}
  .op {{ color:var(--dim); margin:8px 0 4px; }}
  pre {{ background:var(--soft); border:1px solid var(--line); border-radius:6px; padding:10px 12px;
         overflow-x:auto; font-size:12px; line-height:1.5; }}
  .empty {{ color:var(--dim); }}
  footer {{ margin-top:40px; border-top:1px solid var(--line); padding-top:12px; color:var(--dim); font-size:12px; }}
</style>
</head>
<body>
<main>
  <header>
    <h1>{html.escape(title)}</h1>
    <div class="meta">{html.escape(str(rendered.get("dataset") or ""))} · {int(rendered.get("rows") or 0):,} {html.escape(t("story.export_rows", lang))} · {html.escape(stamp)}</div>
  </header>
{body}
  <footer>{html.escape(t("story.export_note", lang))}</footer>
</main>
</body>
</html>
"""


def write_html(path: Path, story: dict, lang: str = "en") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(story, lang), encoding="utf-8")
    return path


# ---- Cicero's Hoard ----------------------------------------------------------

def cicero_available() -> dict:
    """Whether Cicero's Hoard answers through the Hub, and why not when it does not."""
    answer = family.call(CICERO_APP, "cicero_status", {}, timeout=15.0)
    if answer.get("ok"):
        return {"available": True, "detail": ""}
    detail = answer.get("error") or answer.get("detail") or f"HTTP {answer.get('status')}"
    return {"available": False, "detail": str(detail)}


def _chart_block(finding: dict) -> Optional[dict]:
    """The finding's chart as a Cicero chart block: same categories, same values."""
    rows = (finding.get("chart") or {}).get("rows") or []
    if not rows:
        return None
    kind = (finding.get("chart") or {}).get("spec", {}).get("kind")
    categories = [str(row.get("x") or row.get("label") or "") for row in rows]
    values = [row.get("y") if row.get("y") is not None else row.get("value") for row in rows]
    if any(value is None for value in values):
        return None
    chart = {"bar": "bar", "line": "line", "area": "area", "pie": "pie", "donut": "donut"}.get(kind, "bar")
    return {"type": "chart", "chart": chart, "title": finding.get("title") or "",
            "categories": categories, "series": [{"name": finding.get("title") or "", "values": values}],
            "show_values": len(categories) <= 12}


def build_presentation(story: dict, lang: str = "en", title: str = "") -> dict:
    """One slide per finding, built with Cicero's own tools through the Hub.

    Returns ``{"ok": bool, "deck_id": ..., "detail": ...}``; a missing Cicero is
    reported, never worked around by writing a deck somewhere else.
    """
    rendered = story_mod.render_story(story, lang)
    findings = rendered.get("findings", [])
    if not findings:
        return {"ok": False, "detail": t("story.export_empty", lang)}
    available = cicero_available()
    if not available["available"]:
        return {"ok": False, "unavailable": True, "detail": t("story.cicero_unavailable", lang),
                "reason": available["detail"]}

    deck_title = title or rendered.get("title") or rendered.get("dataset") or ""
    created = family.call(CICERO_APP, "deck_create", {
        "title": deck_title[:200],
        "brief": t("story.cicero_brief", lang, dataset=rendered.get("dataset") or "",
                   rows=int(rendered.get("rows") or 0)),
        "audience": "", "tone": "", "language": lang,
        "slide_count": max(3, min(len(findings) + 1, 40)),
    }, timeout=60.0)
    if not created.get("ok"):
        return {"ok": False, "detail": t("story.cicero_failed", lang,
                                         detail=str(created.get("error") or created.get("detail") or created.get("status")))}
    deck = created.get("result") or {}
    deck_id = deck.get("deck_id") or deck.get("id") or ""
    if not deck_id:
        return {"ok": False, "detail": t("story.cicero_failed", lang, detail="no deck id in the answer")}

    slides = []
    for finding in findings:
        blocks = [{"type": "text", "text": finding.get("statement") or ""}]
        chart = _chart_block(finding)
        if chart:
            blocks.append(chart)
        slides.append({
            "layout": "chart" if chart else "bullets",
            "title": (finding.get("title") or "")[:200],
            "blocks": blocks,
            "notes": t("story.cicero_notes", lang, sql=str((finding.get("query") or {}).get("sql") or "")),
        })
    added = family.call(CICERO_APP, "slides_add", {
        "deck_id": deck_id,
        "batch_key": f"nightingale-story-{rendered.get('dataset')}-{len(findings)}",
        "slides": slides,
    }, timeout=120.0)
    if not added.get("ok"):
        return {"ok": False, "deck_id": deck_id,
                "detail": t("story.cicero_failed", lang,
                            detail=str(added.get("error") or added.get("detail") or added.get("status")))}
    return {"ok": True, "deck_id": deck_id, "slides": len(slides),
            "detail": t("story.cicero_deck", lang, deck_id=deck_id)}