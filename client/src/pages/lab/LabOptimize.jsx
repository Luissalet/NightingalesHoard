import React, { useEffect, useState } from "react";
import { api } from "../../api.js";
import { useApp } from "../../App.jsx";
import { EmptyState } from "../../components/ui.jsx";
import VegaChart from "../../components/VegaChart.jsx";
import { formatNumber } from "../../format.js";
import { SectionCard, SimpleTable } from "./labShared.jsx";
import { paretoScatterSpec } from "./vegaSpecs.js";

/** Parses lines like "price <= 90" or "price + quantity >= 10" into the
 * `{coeffs, le|ge|eq}` shape `/api/lab/models/{id}/optimize` expects. */
function parseConstraints(text) {
  const lines = (text || "").split("\n").map((l) => l.trim()).filter(Boolean);
  return lines.map((line) => {
    const m = /^(.+?)\s*(<=|>=|=)\s*(-?\d+(?:\.\d+)?)$/.exec(line);
    if (!m) throw new Error(`invalid constraint: ${line}`);
    const [, lhs, op, num] = m;
    const coeffs = {};
    const terms = lhs.replace(/-/g, "+-").split("+").map((s) => s.trim()).filter(Boolean);
    for (const term of terms) {
      const tm = /^(-?\d+(?:\.\d+)?)\s*\*?\s*([A-Za-z_][\w]*)$/.exec(term) || /^([A-Za-z_][\w]*)$/.exec(term);
      if (!tm) throw new Error(`invalid term: ${term}`);
      if (tm.length === 2 && Number.isNaN(Number(tm[1]))) coeffs[tm[1]] = (coeffs[tm[1]] || 0) + 1;
      else coeffs[tm[2]] = (coeffs[tm[2]] || 0) + Number(tm[1]);
    }
    const key = op === "<=" ? "le" : op === ">=" ? "ge" : "eq";
    return { coeffs, [key]: Number(num) };
  });
}

export default function LabOptimize({ datasets }) {
  const { t } = useApp();
  const [mode, setMode] = useState("single");
  const [registry, setRegistry] = useState([]);

  useEffect(() => {
    api.lab.modelsList().then((r) => setRegistry(r.models)).catch(() => {});
  }, []);

  if (registry.length === 0) return <EmptyState>{t("lab_optimize_none")}</EmptyState>;

  return (
    <div className="flex flex-col gap-4" data-testid="lab-optimize">
      <nav className="seg self-start" role="tablist">
        <button type="button" aria-pressed={mode === "single"} onClick={() => setMode("single")}>{t("lab_optimize_single")}</button>
        <button type="button" aria-pressed={mode === "multi"} onClick={() => setMode("multi")}>{t("lab_optimize_multi")}</button>
      </nav>
      {mode === "single" ? <SingleOptimize registry={registry} /> : <MultiOptimize registry={registry} />}
    </div>
  );
}

function SingleOptimize({ registry, datasets }) {
  const { t, lang, notify } = useApp();
  const [modelId, setModelId] = useState("");
  const [direction, setDirection] = useState("maximize");
  const [bounds, setBounds] = useState({});
  const [batchSize, setBatchSize] = useState(5);
  const [constraintsText, setConstraintsText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const model = registry.find((m) => String(m.id) === String(modelId));
  const features = model?.features || [];

  const setBound = (f, key, value) => setBounds((b) => ({ ...b, [f]: { ...b[f], [key]: value } }));

  const run = async () => {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const boundsBody = {};
      const fixed = {};
      const integerFeatures = [];
      const categorical = {};
      for (const f of features) {
        const b = bounds[f] || {};
        if (b.categorical) {
          categorical[f] = b.categorical.split(",").map((s) => s.trim()).filter(Boolean);
          continue;
        }
        if (b.fix !== undefined && b.fix !== "") {
          fixed[f] = Number.isNaN(Number(b.fix)) ? b.fix : Number(b.fix);
          continue;
        }
        if (b.min !== undefined && b.min !== "" && b.max !== undefined && b.max !== "") {
          boundsBody[f] = [Number(b.min), Number(b.max)];
        }
        if (b.integer) integerFeatures.push(f);
      }
      const constraints = parseConstraints(constraintsText);
      const r = await api.lab.optimize(Number(modelId), {
        direction, bounds: Object.keys(boundsBody).length ? boundsBody : undefined,
        fixed: Object.keys(fixed).length ? fixed : undefined,
        integer_features: integerFeatures.length ? integerFeatures : undefined,
        categorical_features: Object.keys(categorical).length ? categorical : undefined,
        constraints: constraints.length ? constraints : undefined,
        batch_size: Number(batchSize),
      });
      setResult(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveDataset = async () => {
    if (!result) return;
    try {
      const r = await api.lab.optimize(Number(modelId), {
        direction, batch_size: Number(batchSize), write_to: "new_dataset",
      });
      notify(`${t("lab_optimize_saved")}: ${r.optimize_dataset}`);
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <SectionCard testId="lab-optimize-single">
      <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
        <div>
          <label className="label">{t("lab_optimize_model")}</label>
          <select className="field" value={modelId} onChange={(e) => setModelId(e.target.value)} data-testid="lab-optimize-model">
            <option value="">{t("lab_models_pick_model")}</option>
            {registry.map((m) => (
              <option key={m.id} value={m.id}>#{m.id} — {m.backend}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">{t("lab_optimize_direction")}</label>
          <select className="field" value={direction} onChange={(e) => setDirection(e.target.value)}>
            <option value="maximize">{t("lab_optimize_maximize")}</option>
            <option value="minimize">{t("lab_optimize_minimize")}</option>
          </select>
        </div>
        <div>
          <label className="label">{t("lab_optimize_batch_size")}</label>
          <input className="field" type="number" min="1" max="50" value={batchSize} onChange={(e) => setBatchSize(e.target.value)} />
        </div>
      </div>

      {features.length > 0 && (
        <div>
          <div className="help mb-1">{t("lab_optimize_bounds_title")}</div>
          <div className="overflow-auto">
            <table className="w-full border-collapse text-[12.5px]">
              <thead>
                <tr>
                  {["feature", t("lab_optimize_bound_min"), t("lab_optimize_bound_max"), t("lab_optimize_bound_fix"), t("lab_optimize_bound_integer"), t("lab_optimize_bound_categorical")].map((h) => (
                    <th key={h} className="border-b p-1 text-left" style={{ borderColor: "var(--line)" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {features.map((f) => (
                  <tr key={f}>
                    <td className="border-b p-1 font-medium" style={{ borderColor: "var(--line)" }}>{f}</td>
                    <td className="border-b p-1" style={{ borderColor: "var(--line)" }}><input className="field" style={{ width: 90 }} value={bounds[f]?.min ?? ""} onChange={(e) => setBound(f, "min", e.target.value)} /></td>
                    <td className="border-b p-1" style={{ borderColor: "var(--line)" }}><input className="field" style={{ width: 90 }} value={bounds[f]?.max ?? ""} onChange={(e) => setBound(f, "max", e.target.value)} /></td>
                    <td className="border-b p-1" style={{ borderColor: "var(--line)" }}><input className="field" style={{ width: 90 }} value={bounds[f]?.fix ?? ""} onChange={(e) => setBound(f, "fix", e.target.value)} /></td>
                    <td className="border-b p-1" style={{ borderColor: "var(--line)" }}><input type="checkbox" checked={!!bounds[f]?.integer} onChange={(e) => setBound(f, "integer", e.target.checked)} /></td>
                    <td className="border-b p-1" style={{ borderColor: "var(--line)" }}><input className="field" style={{ width: 130 }} value={bounds[f]?.categorical ?? ""} onChange={(e) => setBound(f, "categorical", e.target.value)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div>
        <label className="label">{t("lab_optimize_constraints_title")}</label>
        <textarea className="field" rows={2} placeholder="price <= 90" value={constraintsText} onChange={(e) => setConstraintsText(e.target.value)} />
        <div className="help">{t("lab_optimize_constraints_hint")}</div>
      </div>

      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}
      <div>
        <button type="button" className="btn btn-primary" onClick={run} disabled={busy || !modelId} data-testid="lab-optimize-run">
          {busy ? t("common_loading") : t("lab_optimize_run")}
        </button>
      </div>

      {result && (
        <div className="flex flex-col gap-2" data-testid="lab-optimize-result">
          <div className="help">{t("lab_optimize_suggested_title")}</div>
          <SimpleTable
            columns={[
              { key: "inputs", label: "inputs" },
              { key: "predicted_value", label: "predicted" },
              { key: "uncertainty", label: "± uncertainty" },
            ]}
            keyField="predicted_value"
            rows={result.suggested_points.map((p, i) => ({ ...p, __k: i }))}
            renderCell={(row, key) => {
              if (key === "inputs") return Object.entries(row.inputs).map(([k, v]) => `${k}=${typeof v === "number" ? formatNumber(v, lang, { maximumFractionDigits: 3 }) : v}`).join(", ");
              if (key === "predicted_value" || key === "uncertainty") return formatNumber(row[key], lang, { maximumFractionDigits: 3 });
              return row[key];
            }}
          />
          <button type="button" className="btn btn-sm self-start" onClick={saveDataset} data-testid="lab-optimize-save">{t("lab_optimize_save_dataset")}</button>
        </div>
      )}
    </SectionCard>
  );
}

function MultiOptimize({ registry }) {
  const { t, lang, notify } = useApp();
  const [modelIds, setModelIds] = useState([]);
  const [directions, setDirections] = useState({});
  const [nCandidates, setNCandidates] = useState(1000);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const toggleModel = (id) => {
    setModelIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : ids.length < 3 ? [...ids, id] : ids));
  };

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.lab.pareto({
        model_ids: modelIds.map(Number),
        directions: modelIds.map((id) => directions[id] || "maximize"),
        n_candidates: Number(nCandidates),
      });
      setResult(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveDataset = async () => {
    try {
      const r = await api.lab.pareto({
        model_ids: modelIds.map(Number), directions: modelIds.map((id) => directions[id] || "maximize"),
        n_candidates: Number(nCandidates), write_to: "new_dataset",
      });
      notify(`${t("lab_optimize_saved")}: ${r.pareto_dataset}`);
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <SectionCard testId="lab-optimize-multi">
      <div>
        <label className="label">{t("lab_optimize_models")}</label>
        <div className="flex flex-wrap gap-2">
          {registry.map((m) => (
            <label key={m.id} className="chip" style={{ cursor: "pointer" }}>
              <input type="checkbox" checked={modelIds.includes(m.id)} onChange={() => toggleModel(m.id)} style={{ marginRight: 4 }} />
              #{m.id} {m.backend}
            </label>
          ))}
        </div>
      </div>
      {modelIds.length > 0 && (
        <div>
          <label className="label">{t("lab_optimize_directions")}</label>
          <div className="flex flex-wrap gap-3">
            {modelIds.map((id) => (
              <div key={id} className="flex items-center gap-1">
                <span className="help">#{id}</span>
                <select className="field" style={{ width: "auto" }} value={directions[id] || "maximize"} onChange={(e) => setDirections((d) => ({ ...d, [id]: e.target.value }))}>
                  <option value="maximize">{t("lab_optimize_maximize")}</option>
                  <option value="minimize">{t("lab_optimize_minimize")}</option>
                </select>
              </div>
            ))}
          </div>
        </div>
      )}
      <div style={{ maxWidth: 200 }}>
        <label className="label">n candidates</label>
        <input className="field" type="number" value={nCandidates} onChange={(e) => setNCandidates(e.target.value)} />
      </div>
      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}
      <div>
        <button type="button" className="btn btn-primary" onClick={run} disabled={busy || modelIds.length < 2} data-testid="lab-pareto-run">
          {busy ? t("common_loading") : t("lab_optimize_run")}
        </button>
      </div>
      {result && (
        <div className="flex flex-col gap-2" data-testid="lab-pareto-result">
          <div className="help">{t("lab_optimize_pareto_title")}: {result.n_front} / {result.n_candidates_evaluated}</div>
          <VegaChart spec={paretoScatterSpec(result.front, [], result.objectives[0]?.target, result.objectives[1]?.target)} />
          <SimpleTable
            columns={[{ key: "inputs", label: "inputs" }, { key: "objectives", label: "objectives" }]}
            keyField="__k"
            rows={result.front.map((p, i) => ({ ...p, __k: i }))}
            renderCell={(row, key) => {
              if (key === "inputs") return Object.entries(row.inputs).map(([k, v]) => `${k}=${typeof v === "number" ? formatNumber(v, lang, { maximumFractionDigits: 3 }) : v}`).join(", ");
              if (key === "objectives") return row.objectives.map((v) => formatNumber(v, lang, { maximumFractionDigits: 3 })).join(" / ");
              return row[key];
            }}
          />
          <button type="button" className="btn btn-sm self-start" onClick={saveDataset} data-testid="lab-pareto-save">{t("lab_optimize_save_dataset")}</button>
        </div>
      )}
    </SectionCard>
  );
}
