/** Vega-Lite spec builders for the Lab tabs. Every chart in Lab renders
 * through the app's one charting library (vega-embed via VegaChart.jsx) —
 * these builders just shape Lab's own JSON responses into Vega-Lite specs
 * with the same look as the server-built charts in workbench/charts.py
 * (accent rose -> gold ramp, same font/axis/legend theme), so a Lab chart
 * reads as part of the same app as the Charts page. */

const ACCENT = "#7a1f4a";
const GOLD = "#e0a048";
const PALETTE = ["#7a1f4a", "#e0a048", "#c94f7c", "#8a6a1f", "#4a1330", "#e0679a", "#9c3b64", "#c98a2c"];
const DANGER = "#8a2f26";

const VEGA_CONFIG = {
  background: "transparent",
  font: "system-ui, -apple-system, 'Segoe UI', sans-serif",
  range: { category: PALETTE },
  numberFormat: ",.3~f",
  mark: { color: ACCENT },
  axis: { labelColor: "#6b5560", titleColor: "#241419", gridColor: "#00000014", domainColor: "#00000030", tickColor: "#00000030", labelFontSize: 10.5 },
  legend: { labelColor: "#6b5560", titleColor: "#241419" },
  view: { stroke: "transparent" },
  title: { color: "#241419", fontSize: 13, fontWeight: 600 },
};

function base(values, height = 280) {
  return {
    $schema: "https://vega.github.io/schema/vega-lite/v5.json",
    data: { values },
    width: "container",
    height,
    config: JSON.parse(JSON.stringify(VEGA_CONFIG)),
  };
}

/** Correlation matrix (or any square/rect numeric matrix) as a heatmap.
 * `matrix` is a 2D array aligned with `columns`; diverging so -1..1 reads
 * clearly (rose = negative, gold = positive). */
export function correlationHeatmapSpec(columns, matrix) {
  const values = [];
  columns.forEach((a, i) => {
    columns.forEach((b, j) => {
      const v = matrix?.[i]?.[j];
      if (typeof v === "number") values.push({ a, b, value: v });
    });
  });
  const spec = base(values, Math.max(220, columns.length * 32));
  spec.mark = { type: "rect", tooltip: true };
  spec.encoding = {
    x: { field: "a", type: "nominal", sort: null, title: null },
    y: { field: "b", type: "nominal", sort: null, title: null },
    color: { field: "value", type: "quantitative", scale: { domain: [-1, 1], range: [DANGER, "#f7edf2", GOLD] }, legend: { title: "r" } },
    tooltip: [{ field: "a", title: "" }, { field: "b", title: "" }, { field: "value", type: "quantitative", format: ".3f" }],
  };
  return spec;
}

/** A box plot drawn from pre-computed per-column summary stats (min, q1,
 * median, q3, max, whisker_low, whisker_high) — the EDA endpoint returns the
 * five-number summary, not raw samples, so this layers rule + bar + tick
 * rather than using Vega-Lite's own `boxplot` mark (which expects raw rows). */
export function boxPlotFromStatsSpec(columns) {
  const values = columns.map((c) => ({ column: c.column, ...c.box_plot }));
  const spec = base(values, 280);
  spec.encoding = { x: { field: "column", type: "nominal", sort: null, title: null } };
  spec.layer = [
    { mark: { type: "rule", color: "#9c8790" }, encoding: { y: { field: "whisker_low", type: "quantitative", title: null }, y2: { field: "whisker_high" } } },
    { mark: { type: "bar", size: 18, color: ACCENT, opacity: 0.85, tooltip: true }, encoding: { y: { field: "q1", type: "quantitative" }, y2: { field: "q3" } } },
    { mark: { type: "tick", color: "#fff", size: 18, thickness: 2 }, encoding: { y: { field: "median", type: "quantitative" } } },
  ];
  return spec;
}

/** A generic vertical bar chart: `rows` = [{label, value}], sorted by value
 * unless `sort` is false. Used for outlier counts, null rates, quality
 * component breakdowns, feature importance. */
export function barSpec(rows, { xTitle = null, yTitle = null, sort = "-y", horizontal = false, color = ACCENT, format = ".2~f" } = {}) {
  const spec = base(rows, Math.max(160, rows.length * 26));
  spec.mark = { type: "bar", color, tooltip: true };
  if (horizontal) {
    spec.encoding = {
      y: { field: "label", type: "nominal", sort, title: yTitle },
      x: { field: "value", type: "quantitative", title: xTitle, format },
    };
  } else {
    spec.encoding = {
      x: { field: "label", type: "nominal", sort, title: xTitle },
      y: { field: "value", type: "quantitative", title: yTitle, format },
    };
  }
  return spec;
}

/** Pre-binned histogram: `counts`/`edges` as returned by the diagnostics
 * residual histogram (edges has one more entry than counts). */
export function histogramSpec(counts, edges, xTitle = "value") {
  const values = counts.map((c, i) => ({ bin_start: edges[i], bin_end: edges[i + 1], count: c }));
  const spec = base(values, 220);
  spec.mark = { type: "bar", color: ACCENT, tooltip: true };
  spec.encoding = {
    x: { field: "bin_start", type: "quantitative", title: xTitle, bin: { binned: true } },
    x2: { field: "bin_end" },
    y: { field: "count", type: "quantitative", title: "count" },
  };
  return spec;
}

/** Scatter with an optional y=x identity line (predicted vs actual) or a
 * horizontal zero line (residuals vs predicted). */
export function scatterSpec(rows, xField, yField, { identityLine = false, zeroLine = false, xTitle, yTitle } = {}) {
  const spec = base(rows, 300);
  const nums = rows.flatMap((r) => [r[xField], r[yField]]).filter((v) => typeof v === "number");
  const lo = nums.length ? Math.min(...nums) : 0;
  const hi = nums.length ? Math.max(...nums) : 1;
  const points = {
    mark: { type: "point", filled: true, opacity: 0.55, size: 28, tooltip: true, color: ACCENT },
    encoding: {
      x: { field: xField, type: "quantitative", title: xTitle ?? xField, axis: { tickCount: 6 } },
      y: { field: yField, type: "quantitative", title: yTitle ?? yField, axis: { tickCount: 6 } },
    },
  };
  spec.layer = [points];
  if (identityLine) {
    spec.layer.push({
      data: { values: [{ v: lo }, { v: hi }] },
      mark: { type: "line", strokeDash: [4, 3], color: "#9c8790" },
      encoding: { x: { field: "v", type: "quantitative" }, y: { field: "v", type: "quantitative" } },
    });
  }
  if (zeroLine) {
    spec.layer.push({
      data: { values: [{ v: lo }, { v: hi }] },
      mark: { type: "rule", strokeDash: [4, 3], color: "#9c8790" },
      encoding: { y: { datum: 0 } },
    });
  }
  return spec;
}

/** A Q-Q plot: sample residual quantiles vs theoretical normal quantiles,
 * with a reference y=x-ish line through the data's own range. */
export function qqSpec(theoretical, sample) {
  const rows = theoretical.map((t, i) => ({ theoretical: t, sample: sample[i] }));
  return scatterSpec(rows, "theoretical", "sample", { xTitle: "theoretical quantile", yTitle: "sample quantile" });
}

/** A line/overlay chart for one or more series: `rows` = [{x, y, series?}]. */
export function lineSpec(rows, { xTitle, yTitle, temporal = false, points = false } = {}) {
  const spec = base(rows, 280);
  const hasSeries = rows.some((r) => r.series !== undefined);
  spec.mark = { type: points ? "line" : "line", point: points, tooltip: true };
  spec.encoding = {
    x: { field: "x", type: temporal ? "temporal" : "quantitative", title: xTitle },
    y: { field: "y", type: "quantitative", title: yTitle },
  };
  if (hasSeries) spec.encoding.color = { field: "series", type: "nominal" };
  return spec;
}

/** Calibration curve: predicted probability vs observed fraction, with the
 * perfectly-calibrated diagonal for reference. */
export function calibrationSpec(meanPredicted, fractionPositive) {
  const rows = meanPredicted.map((m, i) => ({ x: m, y: fractionPositive[i] }));
  return scatterSpec(rows, "x", "y", { identityLine: true, xTitle: "mean predicted probability", yTitle: "observed fraction positive" });
}

/** Confusion matrix as a labeled heatmap. */
export function confusionMatrixSpec(labels, matrix) {
  const values = [];
  labels.forEach((actual, i) => {
    labels.forEach((predicted, j) => {
      values.push({ actual, predicted, count: matrix?.[i]?.[j] ?? 0 });
    });
  });
  const spec = base(values, Math.max(200, labels.length * 46));
  spec.layer = [
    {
      mark: "rect",
      encoding: {
        x: { field: "predicted", type: "nominal", title: "predicted" },
        y: { field: "actual", type: "nominal", title: "actual", sort: null },
        color: { field: "count", type: "quantitative", scale: { range: ["#f7edf2", ACCENT] }, legend: null },
      },
    },
    {
      mark: { type: "text" },
      encoding: {
        x: { field: "predicted", type: "nominal" },
        y: { field: "actual", type: "nominal", sort: null },
        text: { field: "count", type: "quantitative" },
        color: { condition: { test: "datum.count > 0", value: "#241419" }, value: "#6b5560" },
      },
    },
  ];
  return spec;
}

/** Grouped bar for train/CV/holdout score comparison. */
export function fitBarSpec(train, cv, holdout) {
  const rows = [
    { label: "train", value: train },
    { label: "cv", value: cv },
    { label: "holdout", value: holdout },
  ].filter((r) => typeof r.value === "number");
  return barSpec(rows, { sort: null, format: ".3f" });
}

/** Learning curve: train score and CV score vs training set size. */
export function learningCurveSpec(curve) {
  const rows = [];
  curve.forEach((p) => {
    rows.push({ x: p.train_size, y: p.train_score, series: "train" });
    rows.push({ x: p.train_size, y: p.cv_score, series: "cv" });
  });
  return lineSpec(rows, { xTitle: "training rows", yTitle: "score", points: true });
}

/** A generic scatter of arbitrary 2D points, for optimize/Pareto search
 * results (non-dominated points highlighted vs. the rest). */
export function paretoScatterSpec(front, all, obj0Title, obj1Title) {
  const rows = [
    ...(all || []).map((p) => ({ x: p.objectives[0], y: p.objectives[1], group: "candidate" })),
    ...front.map((p) => ({ x: p.objectives[0], y: p.objectives[1], group: "front" })),
  ];
  const spec = base(rows, 320);
  spec.mark = { type: "point", filled: true, tooltip: true };
  spec.encoding = {
    x: { field: "x", type: "quantitative", title: obj0Title },
    y: { field: "y", type: "quantitative", title: obj1Title },
    color: { field: "group", type: "nominal", scale: { domain: ["candidate", "front"], range: ["#c9b8c2", ACCENT] } },
    size: { field: "group", type: "nominal", scale: { domain: ["candidate", "front"], range: [24, 80] }, legend: null },
  };
  return spec;
}

/** Distribution overlay for drift comparison: two datasets' values for one
 * column, as overlapping density-ish histograms (equal-width bins computed
 * client side from each side's own min/max is avoided — instead this plots
 * each side's already-summarized mean/sd as a simple bar pair, which is what
 * the drift endpoint actually gives us per column). */
export function driftBarSpec(rows) {
  const values = [];
  rows.forEach((r) => {
    values.push({ column: r.column, side: "A", value: r.mean_a });
    values.push({ column: r.column, side: "B", value: r.mean_b });
  });
  const spec = base(values, Math.max(180, rows.length * 30));
  spec.mark = { type: "bar", tooltip: true };
  spec.encoding = {
    y: { field: "column", type: "nominal", sort: null },
    x: { field: "value", type: "quantitative", title: "mean" },
    color: { field: "side", type: "nominal" },
    yOffset: { field: "side" },
  };
  return spec;
}
