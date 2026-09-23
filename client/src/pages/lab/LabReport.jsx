import React, { useEffect, useState } from "react";
import { api } from "../../api.js";
import { useApp } from "../../App.jsx";
import { SectionCard } from "./labShared.jsx";

export default function LabReport({ dataset, datasets }) {
  const { t, notify } = useApp();
  const [registry, setRegistry] = useState([]);
  const [modelId, setModelId] = useState("");
  const [evalDataset, setEvalDataset] = useState("");
  const [includeOptimize, setIncludeOptimize] = useState(false);
  const [direction, setDirection] = useState("maximize");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  useEffect(() => {
    api.lab.modelsList(dataset).then((r) => setRegistry(r.models)).catch(() => {});
  }, [dataset]);

  const generate = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await api.lab.report({
        dataset, model_id: modelId ? Number(modelId) : undefined, eval_dataset: evalDataset || undefined,
        optimize: includeOptimize, optimize_params: includeOptimize ? { direction } : undefined,
      });
      setResult(r);
      notify(t("lab_report_generated"));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <SectionCard title={t("lab_report_title")} testId="lab-report">
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <div>
          <label className="label">{t("lab_report_model")}</label>
          <select className="field" value={modelId} onChange={(e) => setModelId(e.target.value)} data-testid="lab-report-model">
            <option value="">—</option>
            {registry.map((m) => (
              <option key={m.id} value={m.id}>#{m.id} — {m.backend}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">{t("lab_report_eval_dataset")}</label>
          <select className="field" value={evalDataset} onChange={(e) => setEvalDataset(e.target.value)} disabled={!modelId}>
            <option value="">—</option>
            {datasets.map((d) => (
              <option key={d.name} value={d.name}>{d.name}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col justify-end gap-1">
          <label className="flex items-center gap-2 text-[13px]">
            <input type="checkbox" checked={includeOptimize} onChange={(e) => setIncludeOptimize(e.target.checked)} disabled={!modelId} />
            {t("lab_report_include_optimize")}
          </label>
          {includeOptimize && (
            <select className="field" value={direction} onChange={(e) => setDirection(e.target.value)}>
              <option value="maximize">{t("lab_optimize_maximize")}</option>
              <option value="minimize">{t("lab_optimize_minimize")}</option>
            </select>
          )}
        </div>
      </div>
      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}
      <div>
        <button type="button" className="btn btn-primary" onClick={generate} disabled={busy || !dataset} data-testid="lab-report-generate">
          {busy ? t("common_loading") : t("lab_report_generate")}
        </button>
      </div>
      {result && (
        <div className="help" data-testid="lab-report-result">
          <a className="btn-link" href={api.lab.reportDownloadUrl(result.path)} target="_blank" rel="noreferrer" data-testid="lab-report-open">
            {t("lab_report_open")}
          </a>
        </div>
      )}
    </SectionCard>
  );
}
