"""Lab PDF report: renders whatever sections are supplied into a real PDF
file under the given path (matplotlib PdfPages)."""

import numpy as np
import pandas as pd

from nightingale.lab import eda, registry, report


def test_build_report_with_eda_and_model_sections(tmp_path):
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"x1": rng.normal(0, 1, 200), "x2": rng.normal(0, 1, 200)})
    df["y"] = 2 * df["x1"] - df["x2"] + rng.normal(0, 0.1, 200)
    eda_result = eda.eda_profile(df)
    model_result = registry.train_model(df, "y", ["x1", "x2"], "regression", "random_forest", seed=0)

    out_path = tmp_path / "report.pdf"
    path = report.build_report(out_path, "demo", eda=eda_result, model=model_result)
    assert path.exists()
    assert path.stat().st_size > 500  # a real, non-empty PDF
    with path.open("rb") as fh:
        assert fh.read(4) == b"%PDF"


def test_build_report_empty_still_produces_a_pdf(tmp_path):
    out_path = tmp_path / "empty.pdf"
    path = report.build_report(out_path, "demo")
    assert path.exists()
    with path.open("rb") as fh:
        assert fh.read(4) == b"%PDF"


def test_build_report_creates_parent_directories(tmp_path):
    out_path = tmp_path / "nested" / "dir" / "report.pdf"
    path = report.build_report(out_path, "demo")
    assert path.exists()
