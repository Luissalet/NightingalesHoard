import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../api.js";
import { useApp } from "../../App.jsx";
import { EmptyState } from "../../components/ui.jsx";
import VegaChart from "../../components/VegaChart.jsx";
import { formatNumber } from "../../format.js";
import { SectionCard, ColumnSelect, StatTile } from "./labShared.jsx";
import {
  scatterSpec, histogramSpec, qqSpec, barSpec, lineSpec, fitBarSpec, learningCurveSpec,
  confusionMatrixSpec, calibrationSpec,
} from "./vegaSpecs.js";

export default function LabDiagnose({ datasets }) {
  const { t, lang } = useApp();
  const [registry, setRegistry] = useState([]);
  const [modelId, setModelId] = useState("");
  const [evalDataset, setEvalDataset] = useState("");
  const [dateCol, setDateCol] = useState("");
  const [groupCol, setGroupCol] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  useEffect(() => {
    api.lab.modelsList().then((r) => setRegistry(r.models)).catch(() => {});
  }, []);

  const model = registry.find((m) => String(m.id) === String(modelId));
  const trainingDataset = datasets.find((d) => d.id === model?.dataset_id);
  const evalColumns = (datasets.find((d) => d.name === evalDataset)?.columns || trainingDataset?.columns || []).map((c) => c.name);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.lab.evaluate(Number(modelId), {
        eval_dataset: evalDataset || undefined, date_col: dateCol || undefined, group_col: groupCol || undefined,
      });
      setResult(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (registry.length === 0) return <EmptyState>{t("lab_diagnose_none")}</EmptyState>;

  return (
    <div className="flex flex-col gap-4" data-testid="lab-diagnose">
      <SectionCard title={t("lab_diagnose_title")}>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          <div>
            <label className="label">{t("lab_diagnose_model")}</label>
            <select className="field" value={modelId} onChange={(e) => setModelId(e.target.value)} data-testid="lab-diagnose-model">
              <option value="">{t("lab_models_pick_model")}</option>
              {registry.map((m) => (
                <option key={m.id} value={m.id}>#{m.id} — {m.backend} ({m.task || m.kind})</option>
              ))}
            </select>
          </div>
          <div>
            <label className="label">{t("lab_diagnose_eval_dataset")}</label>
            <select className="field" value={evalDataset} onChange={(e) => setEvalDataset(e.target.value)}>
              <option value="">—</option>
              {datasets.map((d) => (
                <option key={d.name} value={d.name}>{d.name}</option>
              ))}
            </select>
            <div className="help">{t("lab_diagnose_eval_dataset_hint")}</div>
          </div>
          <div>
            <label className="label">{t("lab_diagnose_date_col")}</label>
            <ColumnSelect columns={evalColumns} value={dateCol} onChange={setDateCol} />
          </div>
          <div>
            <label className="label">{t("lab_diagnose_group_col")}</label>
            <ColumnSelect columns={evalColumns} value={groupCol} onChange={setGroupCol} />
          </div>
        </div>
        {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}
        <div>
          <button type="button" className="btn btn-primary" onClick={run} disabled={busy || !modelId} data-testid="lab-diagnose-run">
            {busy ? t("common_loading") : t("lab_diagnose_run")}
          </button>
        </div>
        {result?.note && <div className="help">{result.note}</div>}
      </SectionCard>

      {result && (result.task === "classification" ? <ClassificationResult result={result} lang={lang} t={t} /> : <RegressionResult result={result} lang={lang} t={t} />)}
    </div>
  );
}

function RegressionResult({ result, lang, t }) {
  const fit = result.fit_diagnosis || {};
  return (
    <>
      <SectionCard title={t("lab_diagnose_metrics")} testId="lab-diagnose-metrics">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          {Object.entries(result.metrics || {}).map(([k, v]) => (
            <StatTile key={k} label={k} value={typeof v === "number" ? formatNumber(v, lang, { maximumFractionDigits: 4 }) : "—"} />
          ))}
        </div>
      </SectionCard>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <SectionCard title={t("lab_diagnose_pred_vs_actual")}>
          <VegaChart spec={scatterSpec(result.predicted_vs_actual, "actual", "predicted", { identityLine: true })} />
        </SectionCard>
        <SectionCard title={t("lab_diagnose_residuals_vs_pred")}>
          <VegaChart spec={scatterSpec(result.residuals.vs_predicted, "predicted", "residual", { zeroLine: true })} />
        </SectionCard>
        <SectionCard title={t("lab_diagnose_residual_hist")}>
          <VegaChart spec={histogramSpec(result.residuals.histogram.counts, result.residuals.histogram.edges, "residual")} height={220} />
        </SectionCard>
        <SectionCard title={t("lab_diagnose_qq")}>
          <VegaChart spec={qqSpec(result.residuals.qq.theoretical, result.residuals.qq.sample)} />
        </SectionCard>
      </div>

      {result.error_by_group?.length > 0 && (
        <SectionCard title={t("lab_diagnose_error_by_group")}>
          <VegaChart spec={barSpec(result.error_by_group.map((g) => ({ label: g.group, value: g.mae })), { sort: null, yTitle: "mae" })} height={220} />
        </SectionCard>
      )}
      {result.bias_over_time?.length > 0 && (
        <SectionCard title={t("lab_diagnose_bias_over_time")}>
          <VegaChart spec={lineSpec(result.bias_over_time.map((p) => ({ x: p.date, y: p.rolling_mean_residual })), { xTitle: "date", yTitle: "rolling mean residual", temporal: true })} height={220} />
        </SectionCard>
      )}

      <SectionCard title={t("lab_diagnose_fit_title")} testId="lab-diagnose-fit">
        {fit.note ? (
          <div className="help">{fit.note}</div>
        ) : (
          <>
            <div className="chip chip-accent self-start">{fit.diagnosis}</div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <VegaChart spec={fitBarSpec(fit.train_score, fit.cv_score, fit.holdout_score)} height={200} />
              {fit.learning_curve?.length > 0 && <VegaChart spec={learningCurveSpec(fit.learning_curve)} height={200} />}
            </div>
          </>
        )}
      </SectionCard>
    </>
  );
}

function ClassificationResult({ result, lang, t }) {
  const cm = result.metrics.confusion_matrix;
  const fit = result.fit_diagnosis || {};
  return (
    <>
      <SectionCard title={t("lab_diagnose_metrics")} testId="lab-diagnose-metrics">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          {Object.entries(result.metrics || {}).map(([k, v]) => (typeof v === "number" ? <StatTile key={k} label={k} value={formatNumber(v, lang, { maximumFractionDigits: 4 })} /> : null))}
        </div>
        {typeof result.metrics.roc_auc === "number" && <div className="help">{t("lab_diagnose_roc")}: {t("lab_diagnose_roc_note")}</div>}
      </SectionCard>

      {cm && (
        <SectionCard title={t("lab_diagnose_confusion_matrix")}>
          <VegaChart spec={confusionMatrixSpec(cm.labels, cm.matrix)} height={Math.max(220, cm.labels.length * 46)} />
        </SectionCard>
      )}

      {result.calibration && (
        <SectionCard title={t("lab_diagnose_calibration")}>
          <VegaChart spec={calibrationSpec(result.calibration.mean_predicted, result.calibration.fraction_positive)} />
        </SectionCard>
      )}

      {result.error_by_group?.length > 0 && (
        <SectionCard title={t("lab_diagnose_error_by_group")}>
          <VegaChart spec={barSpec(result.error_by_group.map((g) => ({ label: g.group, value: g.accuracy })), { sort: null, yTitle: "accuracy" })} height={220} />
        </SectionCard>
      )}
      {result.bias_over_time?.length > 0 && (
        <SectionCard title={t("lab_diagnose_bias_over_time")}>
          <VegaChart spec={lineSpec(result.bias_over_time.map((p) => ({ x: p.date, y: p.rolling_error_rate })), { xTitle: "date", yTitle: "rolling error rate", temporal: true })} height={220} />
        </SectionCard>
      )}

      <SectionCard title={t("lab_diagnose_fit_title")}>
        {fit.note ? (
          <div className="help">{fit.note}</div>
        ) : (
          <>
            <div className="chip chip-accent self-start">{fit.diagnosis}</div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              <VegaChart spec={fitBarSpec(fit.train_score, fit.cv_score, fit.holdout_score)} height={200} />
              {fit.learning_curve?.length > 0 && <VegaChart spec={learningCurveSpec(fit.learning_curve)} height={200} />}
            </div>
          </>
        )}
      </SectionCard>
    </>
  );
}
