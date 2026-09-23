"""Exploratory data analysis: correlation, null patterns + imputation preview,
outliers, normalization/encoding suggestions, and a 0-100 dataset quality
score with its components explained.

Pure `pandas`/`numpy`/`scipy` functions over an in-memory DataFrame — the
DuckDB table behind it is never mutated by anything in this module.
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy import stats as _stats

from . import LabError

__all__ = [
    "correlation_matrix", "null_patterns", "imputation_preview", "outliers_report",
    "encoding_suggestions", "quality_score", "eda_profile",
]

_INVALID_PLACEHOLDERS = {
    "null", "n/a", "na", "none", "unknown", "unk", "?", "-", "--", "missing", "#n/a", "nan",
}


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _categorical_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c])
            and not pd.api.types.is_datetime64_any_dtype(df[c])]


def _py(v: Any) -> Any:
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else round(float(v), 6)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------

def correlation_matrix(df: pd.DataFrame, columns: Optional[list[str]] = None, top_n: int = 15) -> dict:
    """Pearson + Spearman correlation matrices over numeric columns, plus the
    strongest pairs by |Pearson r| (excluding the diagonal)."""
    cols = columns or _numeric_columns(df)
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise LabError(f"unknown column(s): {missing}")
    cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    if len(cols) < 2:
        raise LabError("need at least 2 numeric columns for a correlation matrix")
    work = df[cols]
    pearson = work.corr(method="pearson")
    spearman = work.corr(method="spearman")
    pairs = []
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            r = pearson.loc[a, b]
            if pd.notna(r):
                pairs.append({"a": a, "b": b, "pearson": _py(r), "spearman": _py(spearman.loc[a, b])})
    pairs.sort(key=lambda p: -abs(p["pearson"] or 0))
    return {
        "columns": cols,
        "pearson": [[_py(v) for v in row] for row in pearson.values],
        "spearman": [[_py(v) for v in row] for row in spearman.values],
        "top_pairs": pairs[:top_n],
    }


# ---------------------------------------------------------------------------
# Null patterns + imputation preview
# ---------------------------------------------------------------------------

def null_patterns(df: pd.DataFrame, top_n: int = 10) -> dict:
    """Per-column null counts/rates plus which column pairs tend to be null
    together (Pearson correlation of each column's null indicator)."""
    n = len(df)
    per_column = []
    for c in df.columns:
        nulls = int(df[c].isna().sum())
        per_column.append({"column": c, "nulls": nulls, "null_rate": round(nulls / n, 4) if n else 0.0})
    null_cols = [c for c in df.columns if df[c].isna().any()]
    co_occurrence = []
    if len(null_cols) >= 2:
        ind = df[null_cols].isna().astype(float)
        corr = ind.corr()
        for i, a in enumerate(null_cols):
            for b in null_cols[i + 1:]:
                r = corr.loc[a, b]
                if pd.notna(r) and abs(r) > 0.05:
                    co_occurrence.append({"a": a, "b": b, "correlation": _py(r)})
        co_occurrence.sort(key=lambda p: -abs(p["correlation"] or 0))
    return {"row_count": n, "columns": per_column, "co_occurrence": co_occurrence[:top_n]}


def _stats_of(series: pd.Series) -> dict:
    non_null = series.dropna()
    if non_null.empty:
        return {"count": 0}
    if pd.api.types.is_numeric_dtype(non_null):
        return {"count": int(len(non_null)), "mean": _py(non_null.mean()), "sd": _py(non_null.std()),
                "min": _py(non_null.min()), "max": _py(non_null.max())}
    top = non_null.astype(str).value_counts().head(5)
    return {"count": int(len(non_null)), "top_values": [{"value": k, "count": int(v)} for k, v in top.items()]}


_IMPUTE_STRATEGIES = ("mean", "median", "mode", "constant", "ffill")


def imputation_preview(df: pd.DataFrame, column: str, strategy: str = "mean",
                        constant_value: Any = None) -> dict:
    """Preview filling one column's nulls with a strategy: before/after stats.
    Never mutates `df` (works on a copy of the single column)."""
    if column not in df.columns:
        raise LabError(f"unknown column: {column}")
    if strategy not in _IMPUTE_STRATEGIES:
        raise LabError(f"unknown strategy: {strategy}; choose from {_IMPUTE_STRATEGIES}")
    series = df[column]
    before = _stats_of(series)
    n_missing = int(series.isna().sum())
    if n_missing == 0:
        return {"column": column, "strategy": strategy, "n_missing": 0, "before": before, "after": before,
                "note": "no missing values to impute"}
    is_numeric = pd.api.types.is_numeric_dtype(series)
    if strategy in ("mean", "median") and not is_numeric:
        raise LabError(f"strategy {strategy!r} needs a numeric column")
    filled = series.copy()
    if strategy == "mean":
        filled = filled.fillna(filled.mean())
    elif strategy == "median":
        filled = filled.fillna(filled.median())
    elif strategy == "mode":
        mode = filled.mode(dropna=True)
        filled = filled.fillna(mode.iloc[0] if not mode.empty else None)
    elif strategy == "constant":
        if constant_value is None:
            raise LabError("strategy 'constant' needs constant_value")
        filled = filled.fillna(constant_value)
    else:  # ffill
        filled = filled.ffill().bfill()
    after = _stats_of(filled)
    return {"column": column, "strategy": strategy, "n_missing": n_missing,
            "before": before, "after": after}


# ---------------------------------------------------------------------------
# Outliers
# ---------------------------------------------------------------------------

def outliers_report(df: pd.DataFrame, columns: Optional[list[str]] = None) -> dict:
    """IQR and z-score outlier counts per numeric column, plus box-plot stats."""
    cols = columns or _numeric_columns(df)
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise LabError(f"unknown column(s): {missing}")
    out = []
    for c in cols:
        series = df[c].dropna()
        if not pd.api.types.is_numeric_dtype(df[c]) or series.empty:
            continue
        q1, median, q3 = series.quantile([0.25, 0.5, 0.75])
        iqr = q3 - q1
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        iqr_outliers = series[(series < lo) | (series > hi)]
        sd = series.std()
        z = (series - series.mean()) / sd if sd and sd > 0 else pd.Series([0.0] * len(series), index=series.index)
        z_outliers = series[z.abs() > 3]
        whisker_lo = series[series >= lo].min() if not series[series >= lo].empty else lo
        whisker_hi = series[series <= hi].max() if not series[series <= hi].empty else hi
        out.append({
            "column": c, "n": int(len(series)),
            "iqr": {"count": int(len(iqr_outliers)), "rate": round(len(iqr_outliers) / len(series), 4),
                    "lower_fence": _py(lo), "upper_fence": _py(hi)},
            "zscore": {"count": int(len(z_outliers)), "rate": round(len(z_outliers) / len(series), 4)},
            "box_plot": {"min": _py(series.min()), "q1": _py(q1), "median": _py(median), "q3": _py(q3),
                         "max": _py(series.max()), "whisker_low": _py(whisker_lo), "whisker_high": _py(whisker_hi)},
        })
    return {"columns": out}


# ---------------------------------------------------------------------------
# Normalization / encoding suggestions
# ---------------------------------------------------------------------------

def is_id_like(series: pd.Series, n_rows: int) -> bool:
    if pd.api.types.is_numeric_dtype(series):
        return False
    nunique = series.nunique(dropna=True)
    return n_rows > 0 and nunique > 30 and nunique > 0.05 * n_rows


def encoding_suggestions(df: pd.DataFrame, columns: Optional[list[str]] = None) -> dict:
    """Per-column suggestion: how to normalize a numeric column, or encode a
    categorical one, based on shape/scale/cardinality."""
    cols = columns or list(df.columns)
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise LabError(f"unknown column(s): {missing}")
    n = len(df)
    out = []
    for c in cols:
        series = df[c]
        non_null = series.dropna()
        if non_null.empty:
            out.append({"column": c, "kind": "empty", "suggestion": "no non-null values; nothing to suggest"})
            continue
        if pd.api.types.is_numeric_dtype(series):
            if is_id_like(series, n):
                out.append({"column": c, "kind": "id_like", "suggestion": "treat as an identifier, not a feature"})
                continue
            skew = float(_stats.skew(non_null.astype(float))) if len(non_null) > 2 else 0.0
            lo, hi = float(non_null.min()), float(non_null.max())
            has_zero_or_neg = lo <= 0
            if abs(skew) > 1.0:
                method = "yeo_johnson" if has_zero_or_neg else "log"
            else:
                method = "standard" if abs(skew) < 0.5 else "minmax"
            out.append({"column": c, "kind": "numeric", "skew": round(skew, 3), "min": lo, "max": hi,
                        "suggestion": method})
        else:
            if is_id_like(series, n):
                out.append({"column": c, "kind": "id_like", "suggestion": "treat as an identifier, not a feature"})
                continue
            nunique = int(non_null.nunique())
            if nunique <= 12:
                out.append({"column": c, "kind": "categorical", "cardinality": nunique, "suggestion": "one_hot"})
            else:
                out.append({"column": c, "kind": "categorical", "cardinality": nunique,
                            "suggestion": "target_encoding or frequency_encoding (high cardinality)"})
    return {"columns": out}


# ---------------------------------------------------------------------------
# Quality score
# ---------------------------------------------------------------------------

def _validity_component(df: pd.DataFrame) -> float:
    """Fraction of non-null string cells that don't look like a placeholder
    for missing data ("N/A", "unknown", "-999", stray whitespace-only, ...) —
    a generic proxy for "the value that's there actually looks valid"."""
    text_cols = _categorical_columns(df)
    if not text_cols:
        return 1.0
    total = 0
    bad = 0
    for c in text_cols:
        non_null = df[c].dropna().astype(str)
        total += len(non_null)
        normalized = non_null.str.strip().str.lower()
        bad += int((normalized.isin(_INVALID_PLACEHOLDERS) | (normalized == "")).sum())
    return 1.0 - (bad / total if total else 0.0)


def quality_score(df: pd.DataFrame) -> dict:
    """A 0-100 dataset quality score with each component (0-1) explained:
    completeness, uniqueness (row duplication), validity (type/placeholder
    consistency), outliers (IQR rate on numeric columns), constant columns."""
    n_rows, n_cols = len(df), len(df.columns)
    if n_rows == 0 or n_cols == 0:
        return {"score": 0.0, "components": {}, "row_count": n_rows, "column_count": n_cols}

    total_cells = n_rows * n_cols
    null_cells = int(df.isna().sum().sum())
    completeness = 1.0 - (null_cells / total_cells if total_cells else 0.0)

    dup_rows = int(df.duplicated().sum())
    uniqueness = 1.0 - (dup_rows / n_rows if n_rows else 0.0)

    validity = _validity_component(df)

    numeric_cols = _numeric_columns(df)
    if numeric_cols:
        rates = []
        for c in numeric_cols:
            series = df[c].dropna()
            if len(series) < 4:
                continue
            q1, q3 = series.quantile([0.25, 0.75])
            iqr = q3 - q1
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            rates.append(float(((series < lo) | (series > hi)).mean()))
        outlier_rate = float(np.mean(rates)) if rates else 0.0
    else:
        outlier_rate = 0.0
    outliers_component = 1.0 - min(1.0, outlier_rate * 4)  # 25%+ outliers on average -> 0

    constant_cols = sum(1 for c in df.columns if df[c].nunique(dropna=True) <= 1)
    constant_component = 1.0 - (constant_cols / n_cols if n_cols else 0.0)

    weights = {"completeness": 0.30, "uniqueness": 0.20, "validity": 0.20,
               "outliers": 0.15, "constant_columns": 0.15}
    components = {"completeness": completeness, "uniqueness": uniqueness, "validity": validity,
                  "outliers": outliers_component, "constant_columns": constant_component}
    score = sum(components[k] * weights[k] for k in weights) * 100
    return {
        "score": round(score, 1),
        "weights": weights,
        "components": {k: round(v, 4) for k, v in components.items()},
        "row_count": n_rows, "column_count": n_cols,
        "duplicate_rows": dup_rows, "null_cells": null_cells,
        "constant_columns": [c for c in df.columns if df[c].nunique(dropna=True) <= 1],
    }


def eda_profile(df: pd.DataFrame) -> dict:
    """The full EDA bundle: correlation (if >= 2 numeric columns), null
    patterns, outliers, encoding suggestions and the quality score."""
    out: dict[str, Any] = {"row_count": len(df), "column_count": len(df.columns)}
    numeric_cols = _numeric_columns(df)
    if len(numeric_cols) >= 2:
        out["correlation"] = correlation_matrix(df, numeric_cols)
    out["null_patterns"] = null_patterns(df)
    out["outliers"] = outliers_report(df, numeric_cols)
    out["encoding_suggestions"] = encoding_suggestions(df)
    out["quality_score"] = quality_score(df)
    return out
