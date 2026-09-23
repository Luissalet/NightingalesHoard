import React, { useEffect, useMemo, useState } from "react";
import { api } from "../../api.js";
import { useApp } from "../../App.jsx";
import { EmptyState } from "../../components/ui.jsx";
import VegaChart from "../../components/VegaChart.jsx";
import { formatNumber } from "../../format.js";
import { SectionCard, ColumnSelect, SimpleTable, BarRows, InstallHint } from "./labShared.jsx";
import { lineSpec } from "./vegaSpecs.js";

const INNER_TABS = ["train", "registry", "tune", "explain"];

function useBackends() {
  const [backends, setBackends] = useState([]);
  useEffect(() => {
    api.lab.backends().then((r) => {
      const merged = new Map();
      for (const t of ["regression", "classification"]) {
        for (const b of r[t] || []) {
          const existing = merged.get(b.name);
          merged.set(b.name, { name: b.name, available: (existing?.available ?? true) && b.available, note: b.note || existing?.note || "" });
        }
      }
      setBackends([...merged.values()]);
    }).catch(() => {});
  }, []);
  return backends;
}

export default function LabModels({ dataset, datasets }) {
  const { t, lang, notify } = useApp();
  const [inner, setInner] = useState("train");
  const backends = useBackends();
  const [registry, setRegistry] = useState([]);
  const [selected, setSelected] = useState([]);
  const [compared, setCompared] = useState(null);
  const [error, setError] = useState(null);

  const loadRegistry = async () => {
    try {
      const r = await api.lab.modelsList();
      setRegistry(r.models);
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    loadRegistry();
  }, []);

  const datasetById = useMemo(() => {
    const m = new Map();
    datasets.forEach((d) => m.set(d.id, d.name));
    return m;
  }, [datasets]);

  return (
    <div className="flex flex-col gap-4" data-testid="lab-models">
      <nav className="seg self-start" role="tablist">
        {INNER_TABS.map((it) => (
          <button key={it} type="button" aria-pressed={inner === it} onClick={() => setInner(it)} data-testid={`lab-models-inner-${it}`}>
            {t(it === "train" ? "lab_models_train_title" : it === "registry" ? "lab_models_registry_title" : it === "tune" ? "lab_models_tune_title" : "lab_models_explain_title")}
          </button>
        ))}
      </nav>

      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}

      {inner === "train" && <TrainPanel dataset={dataset} datasets={datasets} backends={backends} onTrained={loadRegistry} />}

      {inner === "registry" && (
        <SectionCard testId="lab-registry">
          {registry.length === 0 ? (
            <EmptyState>{t("lab_models_registry_empty")}</EmptyState>
          ) : (
            <>
              <SimpleTable
                columns={[
                  { key: "sel", label: "" },
                  { key: "id", label: t("lab_models_registry_id") },
                  { key: "name", label: t("common_name") },
                  { key: "backend", label: t("lab_models_registry_backend") },
                  { key: "dataset_id", label: t("lab_models_registry_dataset") },
                  { key: "dataset_version", label: t("lab_models_registry_version") },
                  { key: "metrics", label: t("lab_models_registry_metrics") },
                  { key: "created_at", label: t("lab_models_registry_created") },
                  { key: "actions", label: "" },
                ]}
                keyField="id"
                rows={registry}
                renderCell={(row, key) => {
                  if (key === "sel") {
                    return (
                      <input
                        type="checkbox"
                        checked={selected.includes(row.id)}
                        onChange={(e) => setSelected((s) => (e.target.checked ? [...s, row.id] : s.filter((x) => x !== row.id)))}
                      />
                    );
                  }
                  if (key === "dataset_id") return datasetById.get(row.dataset_id) || `#${row.dataset_id}`;
                  if (key === "metrics") {
                    return Object.entries(row.metrics || {}).filter(([, v]) => typeof v === "number").slice(0, 3)
                      .map(([k, v]) => `${k}: ${formatNumber(v, lang, { maximumFractionDigits: 3 })}`).join(" · ");
                  }
                  if (key === "created_at") return row.created_at ? new Date(row.created_at * 1000 || row.created_at).toLocaleString(lang === "es" ? "es-ES" : "en-US") : "—";
                  if (key === "actions") {
                    return (
                      <button
                        type="button"
                        className="btn-link text-xs"
                        onClick={async () => {
                          await api.lab.modelDelete(row.id);
                          notify(t("lab_models_registry_deleted"));
                          setSelected((s) => s.filter((x) => x !== row.id));
                          loadRegistry();
                        }}
                      >
                        {t("lab_models_registry_delete")}
                      </button>
                    );
                  }
                  return row[key];
                }}
              />
              <button
                type="button"
                className="btn btn-sm self-start"
                disabled={selected.length < 2}
                onClick={async () => setCompared((await api.lab.modelsCompare(selected)).models)}
                data-testid="lab-registry-compare"
              >
                {t("lab_models_registry_compare")}
              </button>
              {compared && (
                <SimpleTable
                  columns={[{ key: "id", label: "ID" }, { key: "backend", label: t("lab_models_registry_backend") }, { key: "metrics", label: t("lab_models_registry_metrics") }]}
                  keyField="id"
                  rows={compared}
                  renderCell={(row, key) => (key === "metrics" ? Object.entries(row.metrics || {}).filter(([, v]) => typeof v === "number").map(([k, v]) => `${k}: ${formatNumber(v, lang, { maximumFractionDigits: 3 })}`).join(" · ") : row[key])}
                />
              )}
            </>
          )}
        </SectionCard>
      )}

      {inner === "tune" && <TunePanel dataset={dataset} backends={backends} onTuned={loadRegistry} />}

      {inner === "explain" && <ExplainPanel registry={registry} datasets={datasets} />}
    </div>
  );
}

function TrainPanel({ dataset, datasets, backends, onTrained }) {
  const { t, lang, notify } = useApp();
  const [target, setTarget] = useState("");
  const [features, setFeatures] = useState([]);
  const [backend, setBackend] = useState("random_forest");
  const [testSize, setTestSize] = useState(0.2);
  const [seed, setSeed] = useState(42);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const columns = (datasets.find((d) => d.name === dataset)?.columns || []).map((c) => c.name);

  const train = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.lab.train({
        dataset, target, features: features.length ? features : undefined, backend,
        test_size: Number(testSize), seed: Number(seed),
      });
      setResult(r);
      notify(t("lab_models_train_button"));
      await onTrained();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <SectionCard title={t("lab_models_train_title")} testId="lab-train">
      <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
        <div>
          <label className="label">{t("lab_models_target")}</label>
          <ColumnSelect columns={columns} value={target} onChange={setTarget} testId="lab-train-target" />
        </div>
        <div>
          <label className="label">{t("lab_models_features")}</label>
          <select className="field" multiple value={features} size={Math.min(6, columns.length || 1)}
                  onChange={(e) => setFeatures(Array.from(e.target.selectedOptions, (o) => o.value))} data-testid="lab-train-features">
            {columns.filter((c) => c !== target).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <div className="help">{t("lab_models_features_hint")}</div>
        </div>
        <div>
          <label className="label">{t("lab_models_backends_title")}</label>
          <select className="field" value={backend} onChange={(e) => setBackend(e.target.value)} data-testid="lab-train-backend">
            {backends.map((b) => (
              <option key={b.name} value={b.name} disabled={!b.available}>{b.name}{!b.available ? ` (${t("lab_models_backend_unavailable")})` : ""}</option>
            ))}
          </select>
          {!backends.find((b) => b.name === backend)?.available && <InstallHint note={backends.find((b) => b.name === backend)?.note} />}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label">{t("lab_models_test_size")}</label>
            <input className="field" type="number" step="0.05" min="0.05" max="0.5" value={testSize} onChange={(e) => setTestSize(e.target.value)} />
          </div>
          <div>
            <label className="label">{t("lab_models_seed")}</label>
            <input className="field" type="number" value={seed} onChange={(e) => setSeed(e.target.value)} />
          </div>
        </div>
      </div>
      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}
      <div>
        <button type="button" className="btn btn-primary" onClick={train} disabled={busy || !target} data-testid="lab-train-run">
          {busy ? t("common_loading") : t("lab_models_train_button")}
        </button>
      </div>
      {result && (
        <div className="panel-white flex flex-col gap-2" data-testid="lab-train-result">
          <div className="flex flex-wrap gap-3 text-[13px]">
            <span className="chip chip-accent">model #{result.model_id}</span>
            <span className="chip">{result.task}</span>
            <span className="chip">{result.backend}</span>
            {Object.entries(result.metrics || {}).map(([k, v]) => (typeof v === "number" ? <span key={k}><b className="num">{formatNumber(v, lang, { maximumFractionDigits: 4 })}</b> {k}</span> : null))}
          </div>
          {result.feature_importance?.length > 0 && (
            <BarRows lang={lang} max={Math.max(...result.feature_importance.map((f) => f.importance), 0.0001)}
                     rows={result.feature_importance.slice(0, 10).map((f) => ({ label: f.feature, value: f.importance }))} />
          )}
        </div>
      )}
    </SectionCard>
  );
}

function TunePanel({ dataset, backends, onTuned }) {
  const { t, lang, notify } = useApp();
  const [target, setTarget] = useState("");
  const [backend, setBackend] = useState("random_forest");
  const [nTrials, setNTrials] = useState(20);
  const [timeout_, setTimeout_] = useState("");
  const [cv, setCv] = useState(5);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const tune = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.lab.tune({
        dataset, target, backend, n_trials: Number(nTrials), cv: Number(cv),
        timeout: timeout_ ? Number(timeout_) : undefined,
      });
      setResult(r);
      notify(t("lab_models_tune_button"));
      await onTuned();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <SectionCard title={t("lab_models_tune_title")} testId="lab-tune">
      <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
        <div>
          <label className="label">{t("lab_models_target")}</label>
          <input className="field" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="target column" data-testid="lab-tune-target" />
        </div>
        <div>
          <label className="label">{t("lab_models_backends_title")}</label>
          <select className="field" value={backend} onChange={(e) => setBackend(e.target.value)}>
            {backends.map((b) => (
              <option key={b.name} value={b.name} disabled={!b.available}>{b.name}{!b.available ? ` (${t("lab_models_backend_unavailable")})` : ""}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">{t("lab_models_tune_trials")}</label>
          <input className="field" type="number" value={nTrials} onChange={(e) => setNTrials(e.target.value)} />
        </div>
        <div>
          <label className="label">{t("lab_models_tune_cv")}</label>
          <input className="field" type="number" value={cv} onChange={(e) => setCv(e.target.value)} />
        </div>
        <div>
          <label className="label">{t("lab_models_tune_timeout")}</label>
          <input className="field" type="number" value={timeout_} onChange={(e) => setTimeout_(e.target.value)} />
        </div>
      </div>
      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}
      <div>
        <button type="button" className="btn btn-primary" onClick={tune} disabled={busy || !target} data-testid="lab-tune-run">
          {busy ? t("common_loading") : t("lab_models_tune_button")}
        </button>
      </div>
      {result && (
        <div className="panel-white flex flex-col gap-3" data-testid="lab-tune-result">
          <div className="flex flex-wrap gap-3 text-[13px]">
            <span className="chip chip-accent">model #{result.model_id}</span>
            <span className="chip">{result.tuning.method}</span>
            <span>{t("lab_models_tune_best")}: <b className="num">{formatNumber(result.tuning.best_cv_score, lang, { maximumFractionDigits: 4 })}</b></span>
          </div>
          <div className="help">{Object.entries(result.tuning.best_params || {}).map(([k, v]) => `${k}=${v}`).join(", ")}</div>
          {result.tuning.trials?.length > 0 && (
            <VegaChart spec={lineSpec(result.tuning.trials.map((tr) => ({ x: tr.trial, y: tr.score })), { xTitle: "trial", yTitle: "score", points: true })} height={200} />
          )}
        </div>
      )}
    </SectionCard>
  );
}

function ExplainPanel({ registry, datasets }) {
  const { t, lang } = useApp();
  const [modelId, setModelId] = useState("");
  const [datasetName, setDatasetName] = useState("");
  const [sampleSize, setSampleSize] = useState(200);
  const [rowIndex, setRowIndex] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const explain = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.lab.explain(Number(modelId), {
        dataset: datasetName || undefined, sample_size: Number(sampleSize),
        row_index: rowIndex !== "" ? Number(rowIndex) : undefined,
      });
      setResult(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  if (registry.length === 0) return <EmptyState>{t("lab_models_none")}</EmptyState>;

  return (
    <SectionCard title={t("lab_models_explain_title")} testId="lab-explain">
      <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
        <div>
          <label className="label">{t("lab_models_explain_model")}</label>
          <select className="field" value={modelId} onChange={(e) => setModelId(e.target.value)} data-testid="lab-explain-model">
            <option value="">{t("lab_models_pick_model")}</option>
            {registry.map((m) => (
              <option key={m.id} value={m.id}>#{m.id} — {m.backend}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">{t("lab_models_explain_dataset")} ({t("lab_optional")})</label>
          <select className="field" value={datasetName} onChange={(e) => setDatasetName(e.target.value)}>
            <option value="">—</option>
            {datasets.map((d) => (
              <option key={d.name} value={d.name}>{d.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Sample size</label>
          <input className="field" type="number" value={sampleSize} onChange={(e) => setSampleSize(e.target.value)} />
        </div>
        <div>
          <label className="label">{t("lab_models_explain_row_index")} ({t("lab_optional")})</label>
          <input className="field" type="number" value={rowIndex} onChange={(e) => setRowIndex(e.target.value)} />
        </div>
      </div>
      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}
      <div>
        <button type="button" className="btn btn-primary" onClick={explain} disabled={busy || !modelId} data-testid="lab-explain-run">
          {busy ? t("common_loading") : t("lab_models_explain_button")}
        </button>
      </div>
      {result && (
        <div className="flex flex-col gap-3" data-testid="lab-explain-result">
          <span className="chip chip-accent self-start">{t(`lab_models_explain_method_${result.method}`)}</span>
          <div>
            <div className="help mb-1">{t("lab_models_explain_global")}</div>
            <BarRows lang={lang} max={Math.max(...result.global_importance.map((f) => Math.abs(f.importance)), 0.0001)}
                     rows={result.global_importance.slice(0, 12).map((f) => ({ label: f.feature, value: f.importance }))} />
          </div>
          {result.row_explanation && (
            <div>
              <div className="help mb-1">{t("lab_models_explain_row")} (base {formatNumber(result.row_explanation.base_value, lang, { maximumFractionDigits: 3 })})</div>
              <SimpleTable
                columns={[{ key: "feature", label: "feature" }, { key: "value", label: "value" }, { key: "contribution", label: "contribution" }]}
                keyField="feature"
                rows={result.row_explanation.contributions}
                renderCell={(row, key) => (typeof row[key] === "number" ? formatNumber(row[key], lang, { maximumFractionDigits: 4 }) : row[key])}
              />
            </div>
          )}
        </div>
      )}
    </SectionCard>
  );
}
