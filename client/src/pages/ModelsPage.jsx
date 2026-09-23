import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { EmptyState } from "../components/ui.jsx";

const TABS = ["supervised", "clustering", "pca", "anomaly", "forecast"];

function ColumnPicker({ label, columns, value, onChange, multi }) {
  if (multi) {
    return (
      <div>
        <label className="label">{label}</label>
        <select className="field" multiple value={value} size={Math.min(6, columns.length || 1)}
                onChange={(e) => onChange(Array.from(e.target.selectedOptions, (o) => o.value))}>
          {columns.map((c) => (
            <option key={c} value={c}>{c}</option>
          ))}
        </select>
        <div className="help">Ctrl/Cmd-click to select several; leave empty to use all other columns.</div>
      </div>
    );
  }
  return (
    <div>
      <label className="label">{label}</label>
      <select className="field" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {columns.map((c) => (
          <option key={c} value={c}>{c}</option>
        ))}
      </select>
    </div>
  );
}

export default function ModelsPage() {
  const { t, datasets, notify } = useApp();
  const [tab, setTab] = useState("supervised");
  const [active, setActive] = useState(null);
  const [target, setTarget] = useState("");
  const [features, setFeatures] = useState([]);
  const [algorithm, setAlgorithm] = useState("");
  const [k, setK] = useState("");
  const [contamination, setContamination] = useState(0.05);
  const [dateCol, setDateCol] = useState("");
  const [valueCol, setValueCol] = useState("");
  const [horizon, setHorizon] = useState(12);
  const [result, setResult] = useState(null);
  const [models, setModels] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!active && datasets.length > 0) setActive(datasets[0].name);
  }, [datasets, active]);

  const loadModels = async () => {
    try {
      const r = await api.models(active || undefined);
      setModels(r.models);
    } catch {
      /* non-fatal */
    }
  };
  useEffect(() => {
    loadModels();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const dataset = datasets.find((d) => d.name === active);
  const columnNames = (dataset?.columns || []).map((c) => c.name);

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      let r;
      if (tab === "supervised") {
        r = await api.modelTrain({ dataset: active, target, features: features.length ? features : undefined, algorithm: algorithm || undefined });
      } else if (tab === "clustering") {
        r = await api.modelCluster({ dataset: active, features, k: k ? Number(k) : undefined });
      } else if (tab === "pca") {
        r = await api.modelPca({ dataset: active, features });
      } else if (tab === "anomaly") {
        r = await api.modelAnomaly({ dataset: active, features, contamination: Number(contamination) });
      } else {
        r = await api.modelForecast({ dataset: active, date_col: dateCol, value_col: valueCol, horizon: Number(horizon) });
      }
      setResult(r);
      notify(t("common_train"));
      await loadModels();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (datasets.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-lg font-semibold">{t("models_title")}</h1>
        <EmptyState>{t("datasets_empty")}</EmptyState>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{t("models_title")}</h1>
        <select className="field" style={{ width: "auto" }} value={active || ""} onChange={(e) => setActive(e.target.value)}>
          {datasets.map((d) => (
            <option key={d.name} value={d.name}>{d.name}</option>
          ))}
        </select>
      </div>

      <div className="seg self-start" role="tablist">
        {TABS.map((tb) => (
          <button key={tb} type="button" aria-pressed={tab === tb} onClick={() => { setTab(tb); setResult(null); setError(null); }}>
            {t(`models_${tb === "supervised" ? "supervised" : tb === "clustering" ? "clustering" : tb}`)}
          </button>
        ))}
      </div>

      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}

      <div className="panel flex flex-col gap-3" data-testid={`model-form-${tab}`}>
        {tab === "supervised" && (
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            <ColumnPicker label={t("models_target")} columns={columnNames} value={target} onChange={setTarget} />
            <ColumnPicker label={t("models_features")} columns={columnNames.filter((c) => c !== target)} value={features} onChange={setFeatures} multi />
            <div>
              <label className="label">Algorithm (optional)</label>
              <select className="field" value={algorithm} onChange={(e) => setAlgorithm(e.target.value)}>
                <option value="">auto</option>
                <option value="linear">linear</option>
                <option value="logistic">logistic</option>
                <option value="random_forest">random_forest</option>
                <option value="gradient_boosting">gradient_boosting</option>
              </select>
            </div>
          </div>
        )}
        {tab === "clustering" && (
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            <ColumnPicker label={t("models_features")} columns={columnNames} value={features} onChange={setFeatures} multi />
            <div>
              <label className="label">k (optional; auto by silhouette)</label>
              <input className="field" type="number" value={k} onChange={(e) => setK(e.target.value)} />
            </div>
          </div>
        )}
        {tab === "pca" && (
          <div className="grid grid-cols-1 gap-2">
            <ColumnPicker label={t("models_features")} columns={columnNames} value={features} onChange={setFeatures} multi />
          </div>
        )}
        {tab === "anomaly" && (
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            <ColumnPicker label={t("models_features")} columns={columnNames} value={features} onChange={setFeatures} multi />
            <div>
              <label className="label">Contamination (0-0.5)</label>
              <input className="field" type="number" step="0.01" min="0" max="0.5" value={contamination} onChange={(e) => setContamination(e.target.value)} />
            </div>
          </div>
        )}
        {tab === "forecast" && (
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            <ColumnPicker label="Date column" columns={columnNames} value={dateCol} onChange={setDateCol} />
            <ColumnPicker label="Value column" columns={columnNames} value={valueCol} onChange={setValueCol} />
            <div>
              <label className="label">Horizon (periods)</label>
              <input className="field" type="number" value={horizon} onChange={(e) => setHorizon(e.target.value)} />
            </div>
          </div>
        )}
        <div>
          <button type="button" className="btn btn-primary" onClick={run} disabled={busy} data-testid="model-run">
            {busy ? t("common_loading") : t("common_train")}
          </button>
        </div>
      </div>

      {result && <ModelResult tab={tab} result={result} />}

      {models.length > 0 && (
        <div className="panel-white divide-y" style={{ borderColor: "var(--line)" }} data-testid="model-list">
          {models.map((m) => (
            <div className="row" key={m.id}>
              <span className="chip">{m.kind}</span>
              <span className="font-medium">{m.name}</span>
              <span className="help ml-auto shrink-0">{Object.entries(m.metrics || {}).slice(0, 2).map(([k2, v2]) => `${k2}: ${typeof v2 === "number" ? v2 : ""}`).join(" · ")}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ModelResult({ tab, result }) {
  if (tab === "supervised") {
    return (
      <div className="panel-white flex flex-col gap-2" data-testid="model-result">
        <div className="flex flex-wrap gap-3 text-[13px]">
          <span className="chip chip-accent">{result.task}</span>
          <span className="chip">{result.algorithm}</span>
          {Object.entries(result.metrics || {}).map(([k, v]) =>
            typeof v === "number" ? <span key={k}><b className="num">{v}</b> {k}</span> : null,
          )}
        </div>
        {result.feature_importance?.length > 0 && (
          <div>
            <div className="help mb-1">Feature importance</div>
            {result.feature_importance.slice(0, 10).map((f) => (
              <div key={f.feature} className="flex items-center gap-2 text-[12px]">
                <span className="w-32 truncate">{f.feature}</span>
                <div className="h-2 flex-1 rounded" style={{ background: "var(--bar-bg)" }}>
                  <div className="h-2 rounded" style={{ width: `${Math.min(100, f.importance * 100)}%`, background: "var(--accent)" }} />
                </div>
                <span className="num shrink-0">{f.importance}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }
  if (tab === "clustering") {
    return (
      <div className="panel-white flex flex-col gap-2" data-testid="model-result">
        <div>Chosen k: <b className="num">{result.k}</b></div>
        <div className="help">Silhouette by k: {result.silhouette?.map((s) => `k=${s.k}:${s.silhouette ?? "—"}`).join("  ")}</div>
      </div>
    );
  }
  if (tab === "pca") {
    return (
      <div className="panel-white" data-testid="model-result">
        <div>Explained variance ratio: <span className="num">{result.explained_variance_ratio?.join(", ")}</span></div>
      </div>
    );
  }
  if (tab === "anomaly") {
    return (
      <div className="panel-white" data-testid="model-result">
        <div><b className="num">{result.n_anomalies}</b> anomalies found (contamination {result.contamination})</div>
      </div>
    );
  }
  return (
    <div className="panel-white" data-testid="model-result">
      <div>Method: <span className="chip">{result.method}</span> · seasonal period {result.seasonal_period}</div>
      <div className="mt-2 overflow-auto">
        <table className="w-full border-collapse text-[12.5px]">
          <thead>
            <tr>
              <th className="border-b p-1 text-left" style={{ borderColor: "var(--line)" }}>Date</th>
              <th className="border-b p-1 text-left" style={{ borderColor: "var(--line)" }}>Forecast</th>
              <th className="border-b p-1 text-left" style={{ borderColor: "var(--line)" }}>Range</th>
            </tr>
          </thead>
          <tbody>
            {result.forecast?.slice(0, 12).map((f) => (
              <tr key={f.date}>
                <td className="border-b p-1" style={{ borderColor: "var(--line)" }}>{f.date}</td>
                <td className="num border-b p-1" style={{ borderColor: "var(--line)" }}>{f.value}</td>
                <td className="num border-b p-1" style={{ borderColor: "var(--line)" }}>{f.lower} – {f.upper}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
