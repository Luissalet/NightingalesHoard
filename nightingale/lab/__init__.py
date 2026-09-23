"""Lab: deeper data-science tooling built on top of the workbench.

Every module here is a set of pure functions over `pandas`/`numpy` (plus
`scipy`/`scikit-learn`) — no FastAPI, no DuckDB connection, no SQLite — so
`nightingale/services.py` can run them in the shared thread pool and tests can
call them directly on small in-memory frames, exactly like
`nightingale/workbench/models.py` does for the original model tools.

Optional third-party packages (`optuna`, `shap`, `xgboost`, `lightgbm`,
`joblib`) are imported defensively at module load time behind a `HAVE_*` flag;
every function that would use one degrades to a documented fallback instead of
raising an ImportError. See `requirements-lab.txt` and `docs/LAB.md`.
"""

from __future__ import annotations

__all__ = ["LabError"]


class LabError(ValueError):
    """Raised for any Lab-specific bad input; caught the same way as the
    workbench's DataError/ModelError/QualityError (-> HTTP 400)."""
