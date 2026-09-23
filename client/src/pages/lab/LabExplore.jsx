import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../api.js";
import { useApp } from "../../App.jsx";
import { EmptyState } from "../../components/ui.jsx";
import VegaChart from "../../components/VegaChart.jsx";
import { formatNumber } from "../../format.js";
import { QualityGauge, BarRows, SectionCard, ColumnSelect, SimpleTable } from "./labShared.jsx";
import { correlationHeatmapSpec, boxPlotFromStatsSpec, barSpec } from "./vegaSpecs.js";

const QUALITY_COMPONENT_KEYS = ["completeness", "uniqueness", "validity", "outliers", "constant_columns"];
const IMPUTE_METHODS = ["value", "mean", "median", "mode", "forward"];

/** Maps an EDA encoding suggestion to a one-click transform step, wherever
 * the existing step engine has a matching op — nothing here bypasses the
 * normal dataset transform endpoint, so it lands in the recipe with the
 * usual undo. */
function suggestionToStep(entry) {
  const col = entry.column;
  if (entry.suggestion === "log") return { op: "derive", params: { name: `${col}_log`, expr: `ln("${col}")` } };
  if (entry.suggestion === "sqrt") return { op: "derive", params: { name: `${col}_sqrt`, expr: `sqrt("${col}")` } };
  if (entry.suggestion === "one_hot" || entry.suggestion === "cast") return null; // no single-step equivalent
  return null;
}

export default function LabExplore({ dataset }) {
  const { t, lang, notify, refreshDatasets } = useApp();
  const [eda, setEda] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [corrMethod, setCorrMethod] = useState("pearson");
  const [outlierMethod, setOutlierMethod] = useState("iqr");
  const [imputeColumn, setImputeColumn] = useState("");
  const [imputeStrategy, setImputeStrategy] = useState("mean");
  const [imputePreview, setImputePreview] = useState(null);
  const [applyingStep, setApplyingStep] = useState(null);

  const load = async () => {
    if (!dataset) return;
    setBusy(true);
    try {
      const r = await api.lab.eda(dataset);
      setEda(r);
      setError(null);
      const firstNullable = r.null_patterns?.columns?.find((c) => c.nulls > 0);
      setImputeColumn(firstNullable ? firstNullable.column : "");
      setImputePreview(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset]);

  const nullColumns = useMemo(() => (eda?.null_patterns?.columns || []).filter((c) => c.nulls > 0), [eda]);

  const runImputePreview = async () => {
    if (!imputeColumn) return;
    setError(null);
    try {
      const r = await api.transform(dataset, "fill_null", { column: imputeColumn, strategy: imputeStrategy }, true);
      setImputePreview(r);
    } catch (e) {
      setError(e.message);
    }
  };
  const applyImpute = async () => {
    if (!imputeColumn) return;
    setBusy(true);
    try {
      await api.transform(dataset, "fill_null", { column: imputeColumn, strategy: imputeStrategy }, false);
      notify(t("lab_explore_impute_applied"));
      await refreshDatasets();
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const applySuggestion = async (entry) => {
    const step = suggestionToStep(entry);
    if (!step) return;
    setApplyingStep(entry.column);
    try {
      await api.transform(dataset, step.op, step.params, false);
      notify(t("lab_explore_suggestions_applied"));
      await refreshDatasets();
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setApplyingStep(null);
    }
  };

  if (!dataset) return <EmptyState>{t("lab_explore_empty")}</EmptyState>;
  if (error) return <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>;
  if (!eda) return <EmptyState>{t("common_loading")}</EmptyState>;

  const q = eda.quality_score;
  const componentRows = QUALITY_COMPONENT_KEYS.filter((k) => q.components[k] !== undefined).map((k) => ({
    label: t(`lab_explore_quality_${k}`), value: q.components[k],
    color: q.components[k] >= 0.85 ? "var(--ok-ink)" : q.components[k] >= 0.6 ? "var(--warn-ink)" : "var(--danger-ink)",
  }));

  const outlierRows = (eda.outliers?.columns || []).map((c) => ({
    label: c.column,
    value: outlierMethod === "iqr" ? c.iqr?.count ?? 0 : c.zscore?.count ?? 0,
  }));

  return (
    <div className="flex flex-col gap-4" data-testid="lab-explore">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SectionCard title={t("lab_explore_quality_title")} testId="lab-quality-score">
          <div className="flex flex-wrap items-center gap-6">
            <QualityGauge score={q.score} />
            <div className="flex-1 min-w-[220px]">
              <div className="help mb-2">{t("lab_explore_quality_components")}</div>
              <BarRows rows={componentRows} lang={lang} max={1} />
            </div>
          </div>
          <div className="flex flex-wrap gap-4 text-[12px]" style={{ color: "var(--supporting-ink)" }}>
            <span>{t("lab_explore_quality_duplicate_rows")}: <b className="num">{formatNumber(q.duplicate_rows, lang)}</b></span>
            <span>{t("lab_explore_quality_null_cells")}: <b className="num">{formatNumber(q.null_cells, lang)}</b></span>
            {q.constant_columns.length > 0 && (
              <span>{t("lab_explore_quality_constant_columns")}: <b>{q.constant_columns.join(", ")}</b></span>
            )}
          </div>
        </SectionCard>

        <SectionCard
          title={t("lab_explore_correlation_title")}
          testId="lab-correlation"
          action={
            <div className="seg" role="tablist">
              <button type="button" aria-pressed={corrMethod === "pearson"} onClick={() => setCorrMethod("pearson")}>{t("lab_explore_correlation_pearson")}</button>
              <button type="button" aria-pressed={corrMethod === "spearman"} onClick={() => setCorrMethod("spearman")}>{t("lab_explore_correlation_spearman")}</button>
            </div>
          }
        >
          {eda.correlation.columns.length >= 2 ? (
            <>
              <VegaChart spec={correlationHeatmapSpec(eda.correlation.columns, eda.correlation[corrMethod])} height={Math.max(220, eda.correlation.columns.length * 32)} />
              {eda.correlation.top_pairs?.length > 0 && (
                <div>
                  <div className="help mb-1">{t("lab_explore_correlation_top_pairs")}</div>
                  <SimpleTable
                    columns={[{ key: "pair", label: "" }, { key: "pearson", label: "Pearson" }, { key: "spearman", label: "Spearman" }]}
                    keyField="pair"
                    rows={eda.correlation.top_pairs.map((p) => ({ pair: `${p.a} — ${p.b}`, pearson: formatNumber(p.pearson, lang, { maximumFractionDigits: 3 }), spearman: formatNumber(p.spearman, lang, { maximumFractionDigits: 3 }) }))}
                  />
                </div>
              )}
            </>
          ) : (
            <EmptyState>{t("lab_explore_correlation_empty")}</EmptyState>
          )}
        </SectionCard>
      </div>

      <SectionCard title={t("lab_explore_nulls_title")} testId="lab-nulls">
        {eda.null_patterns.columns.length === 0 ? (
          <EmptyState>{t("lab_explore_nulls_none")}</EmptyState>
        ) : (
          <>
            <SimpleTable
              columns={[
                { key: "column", label: t("lab_explore_nulls_column") },
                { key: "nulls", label: "#" },
                { key: "null_rate", label: t("lab_explore_nulls_rate") },
              ]}
              keyField="column"
              rows={eda.null_patterns.columns}
              renderCell={(row, key) => (key === "null_rate" ? `${(row.null_rate * 100).toFixed(1)}%` : formatNumber(row[key], lang))}
            />
            {eda.null_patterns.co_occurrence?.length > 0 && (
              <div className="help">
                {t("lab_explore_nulls_cooccurrence")}: {eda.null_patterns.co_occurrence.map((c) => `${c.a} ↔ ${c.b} (${formatNumber(c.correlation, lang, { maximumFractionDigits: 2 })})`).join(", ")}
              </div>
            )}

            {nullColumns.length > 0 && (
              <div className="panel flex flex-col gap-3" data-testid="lab-impute">
                <div className="text-[13px] font-semibold">{t("lab_explore_impute_title")}</div>
                <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
                  <div>
                    <label className="label">{t("lab_explore_impute_column")}</label>
                    <ColumnSelect columns={nullColumns.map((c) => c.column)} value={imputeColumn} onChange={(v) => { setImputeColumn(v); setImputePreview(null); }} allowEmpty={false} testId="lab-impute-column" />
                  </div>
                  <div>
                    <label className="label">{t("lab_explore_impute_method")}</label>
                    <select className="field" value={imputeStrategy} onChange={(e) => { setImputeStrategy(e.target.value); setImputePreview(null); }} data-testid="lab-impute-strategy">
                      {IMPUTE_METHODS.map((m) => (
                        <option key={m} value={m}>{m}</option>
                      ))}
                    </select>
                  </div>
                  <div className="flex items-end gap-2">
                    <button type="button" className="btn" onClick={runImputePreview} data-testid="lab-impute-preview">{t("lab_explore_impute_preview")}</button>
                    <button type="button" className="btn btn-primary" onClick={applyImpute} disabled={!imputePreview || busy} data-testid="lab-impute-apply">{t("lab_explore_impute_apply")}</button>
                  </div>
                </div>
                {imputePreview && (
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2 text-[12.5px]" data-testid="lab-impute-result">
                    <div>
                      <div className="help mb-1">{t("lab_explore_impute_before")}</div>
                      <div>{t("lab_explore_nulls_rate")}: <b className="num">{((nullColumns.find((c) => c.column === imputeColumn)?.null_rate || 0) * 100).toFixed(1)}%</b></div>
                    </div>
                    <div>
                      <div className="help mb-1">{t("lab_explore_impute_after")}</div>
                      <div>{t("lab_explore_nulls_rate")}: <b className="num">0.0%</b></div>
                      <div className="help">{t("common_rows")}: {formatNumber(imputePreview.row_count_after, lang)}</div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </SectionCard>

      <SectionCard
        title={t("lab_explore_outliers_title")}
        testId="lab-outliers"
        action={
          <div className="seg" role="tablist">
            <button type="button" aria-pressed={outlierMethod === "iqr"} onClick={() => setOutlierMethod("iqr")}>{t("lab_explore_outliers_method")}: IQR</button>
            <button type="button" aria-pressed={outlierMethod === "zscore"} onClick={() => setOutlierMethod("zscore")}>Z-score</button>
          </div>
        }
      >
        {(eda.outliers?.columns || []).length === 0 ? (
          <EmptyState>{t("lab_explore_outliers_none")}</EmptyState>
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <VegaChart spec={boxPlotFromStatsSpec(eda.outliers.columns)} />
            <VegaChart spec={barSpec(outlierRows, { yTitle: t("lab_explore_outliers_count") })} />
          </div>
        )}
      </SectionCard>

      <SectionCard title={t("lab_explore_suggestions_title")} testId="lab-suggestions">
        {(eda.encoding_suggestions?.columns || []).length === 0 ? (
          <EmptyState>{t("lab_explore_suggestions_none")}</EmptyState>
        ) : (
          <div className="panel-white divide-y" style={{ borderColor: "var(--line)" }}>
            {eda.encoding_suggestions.columns.map((s) => {
              const step = suggestionToStep(s);
              return (
                <div className="row flex-wrap" key={s.column}>
                  <span className="chip">{s.kind}</span>
                  <span className="font-medium">{s.column}</span>
                  <span className="help">{s.suggestion}</span>
                  <div className="ml-auto shrink-0">
                    {step ? (
                      <button type="button" className="btn btn-sm" disabled={applyingStep === s.column} onClick={() => applySuggestion(s)} data-testid={`lab-suggestion-apply-${s.column}`}>
                        {t("lab_explore_suggestions_apply")}
                      </button>
                    ) : (
                      <span className="help">{t("lab_explore_suggestions_manual")}</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>
    </div>
  );
}
