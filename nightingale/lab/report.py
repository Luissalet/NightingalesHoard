"""Lab PDF report: profile + quality score + model metrics + diagnostics plots
+ optimization results, rendered with matplotlib (`PdfPages`) into the app's
exports folder. Every section is optional — the report only draws what it was
given, so it works whether it's called after an EDA pass, a model evaluation,
an optimization run, or all three.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import matplotlib

matplotlib.use("Agg")  # headless everywhere, including Windows: never opens a GUI window
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

__all__ = ["build_report"]


def _title_page(pdf: PdfPages, dataset: str, sections: list[str]) -> None:
    fig = plt.figure(figsize=(8.5, 11))
    fig.text(0.1, 0.85, "Nightingale's Hoard — Lab report", fontsize=20, weight="bold")
    fig.text(0.1, 0.80, f"Dataset: {dataset}", fontsize=13)
    fig.text(0.1, 0.75, "Sections:", fontsize=11, weight="bold")
    for i, s in enumerate(sections):
        fig.text(0.12, 0.71 - i * 0.03, f"- {s}", fontsize=10)
    plt.axis("off")
    pdf.savefig(fig)
    plt.close(fig)


def _quality_page(pdf: PdfPages, quality: dict) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.5, 5))
    fig.suptitle("Data quality score")
    components = quality.get("components", {})
    if components:
        ax1.barh(list(components.keys()), [v * 100 for v in components.values()], color="#4C72B0")
        ax1.set_xlim(0, 100)
        ax1.set_xlabel("score (0-100)")
    score = quality.get("score", 0)
    ax2.text(0.5, 0.5, f"{score:.0f}/100", fontsize=36, ha="center", va="center", weight="bold")
    ax2.axis("off")
    fig.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def _correlation_page(pdf: PdfPages, correlation: dict) -> None:
    cols = correlation.get("columns", [])
    matrix = correlation.get("pearson", [])
    if not cols or not matrix:
        return
    fig, ax = plt.subplots(figsize=(8.5, 8.5))
    im = ax.imshow(matrix, cmap="coolwarm", vmin=-1, vmax=1)
    ax.set_xticks(range(len(cols)))
    ax.set_xticklabels(cols, rotation=90, fontsize=7)
    ax.set_yticks(range(len(cols)))
    ax.set_yticklabels(cols, fontsize=7)
    ax.set_title("Pearson correlation")
    fig.colorbar(im, ax=ax, fraction=0.046)
    fig.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def _model_metrics_page(pdf: PdfPages, model: dict) -> None:
    fig = plt.figure(figsize=(8.5, 6))
    fig.text(0.1, 0.9, f"Model: {model.get('backend', model.get('algorithm', '?'))} "
                        f"({model.get('task', '?')})", fontsize=14, weight="bold")
    metrics = model.get("metrics", {})
    y = 0.8
    for k, v in metrics.items():
        if isinstance(v, dict):
            continue
        fig.text(0.12, y, f"{k}: {v}", fontsize=11)
        y -= 0.05
    plt.axis("off")
    pdf.savefig(fig)
    plt.close(fig)


def _diagnostics_page(pdf: PdfPages, diagnostics: dict) -> None:
    predicted_vs_actual = diagnostics.get("predicted_vs_actual")
    residuals = diagnostics.get("residuals", {})
    if predicted_vs_actual:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.5, 4.5))
        actual = [p["actual"] for p in predicted_vs_actual]
        predicted = [p["predicted"] for p in predicted_vs_actual]
        ax1.scatter(actual, predicted, s=8, alpha=0.6)
        lims = [min(actual + predicted), max(actual + predicted)]
        ax1.plot(lims, lims, "r--", linewidth=1)
        ax1.set_xlabel("actual")
        ax1.set_ylabel("predicted")
        ax1.set_title("Predicted vs actual")
        hist = residuals.get("histogram", {})
        if hist.get("counts"):
            edges = hist["edges"]
            centers = [(edges[i] + edges[i + 1]) / 2 for i in range(len(edges) - 1)]
            ax2.bar(centers, hist["counts"], width=(edges[1] - edges[0]) if len(edges) > 1 else 1)
            ax2.set_title("Residual histogram")
        fig.tight_layout()
        pdf.savefig(fig)
        plt.close(fig)
    elif diagnostics.get("metrics", {}).get("confusion_matrix"):
        cm = diagnostics["metrics"]["confusion_matrix"]
        fig, ax = plt.subplots(figsize=(6, 6))
        im = ax.imshow(cm["matrix"], cmap="Blues")
        ax.set_xticks(range(len(cm["labels"])))
        ax.set_xticklabels(cm["labels"], rotation=45, fontsize=8)
        ax.set_yticks(range(len(cm["labels"])))
        ax.set_yticklabels(cm["labels"], fontsize=8)
        ax.set_title("Confusion matrix")
        fig.colorbar(im, ax=ax, fraction=0.046)
        fig.tight_layout()
        pdf.savefig(fig)
        plt.close(fig)


def _optimization_page(pdf: PdfPages, optimization: dict) -> None:
    suggestions = optimization.get("suggested_points", [])
    fig = plt.figure(figsize=(8.5, 6))
    fig.text(0.1, 0.92, f"Optimization ({optimization.get('direction', '?')})", fontsize=14, weight="bold")
    best = optimization.get("refined_best") or optimization.get("best_in_search")
    if best:
        fig.text(0.1, 0.86, f"Best predicted value: {best.get('predicted_value')}", fontsize=11)
    y = 0.78
    for i, s in enumerate(suggestions[:8]):
        fig.text(0.1, y, f"{i + 1}. predicted={s['predicted_value']}  (+/- {s['uncertainty']})", fontsize=9)
        y -= 0.04
    plt.axis("off")
    pdf.savefig(fig)
    plt.close(fig)


def build_report(out_path: Path, dataset: str, eda: Optional[dict] = None, model: Optional[dict] = None,
                  diagnostics: Optional[dict] = None, optimization: Optional[dict] = None) -> Path:
    """Render every supplied section into one PDF at `out_path` and return it."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sections = []
    if eda:
        sections.append("Data quality & EDA")
    if model:
        sections.append("Model metrics")
    if diagnostics:
        sections.append("Model diagnostics")
    if optimization:
        sections.append("Optimization")
    if not sections:
        sections.append("(empty report — no sections supplied)")

    with PdfPages(out_path) as pdf:
        _title_page(pdf, dataset, sections)
        if eda:
            quality = eda.get("quality_score")
            if quality:
                _quality_page(pdf, quality)
            correlation = eda.get("correlation")
            if correlation:
                _correlation_page(pdf, correlation)
        if model:
            _model_metrics_page(pdf, model)
        if diagnostics:
            _diagnostics_page(pdf, diagnostics)
        if optimization:
            _optimization_page(pdf, optimization)
    return out_path
