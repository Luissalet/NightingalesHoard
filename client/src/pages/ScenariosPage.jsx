import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Chip } from "../components/ui.jsx";
import VegaChart from "../components/VegaChart.jsx";
import { formatNumber } from "../format.js";

const MAIN = ["throughput", "lead_time_mean", "lead_time_p50", "lead_time_p90", "wip", "lost_jobs", "stockouts", "total_cost", "cost_per_hour", "orders", "avg_stock"];

function Num({ v, lang }) {
  return <span className="num">{typeof v === "number" ? formatNumber(v, lang, { maximumFractionDigits: 3 }) : "—"}</span>;
}
function Ci({ m, lang }) {
  if (!m) return null;
  return (
    <>
      <td className="num text-right"><Num v={m.mean} lang={lang} /></td>
      <td className="num text-right help">{m.ci_low === null || m.ci_low === undefined ? "—" : <>[<Num v={m.ci_low} lang={lang} />, <Num v={m.ci_high} lang={lang} />]</>}</td>
    </>
  );
}

export default function ScenariosPage() {
  const { t, lang, datasets, act } = useApp();
  const names = useMemo(() => datasets.map((d) => d.name), [datasets]);
  const [model, setModel] = useState(null);
  const [text, setText] = useState("");
  const [jsonError, setJsonError] = useState("");
  const [saved, setSaved] = useState([]);
  const [modelName, setModelName] = useState("");
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState(null);
  const [cmp, setCmp] = useState(null);
  const [sweep, setSweep] = useState(null);
  const [variants, setVariants] = useState([{ name: "Variant 1", path: "", value: "" }]);
  const [sweepParam, setSweepParam] = useState("arrivals.rate");
  const [sweepValues, setSweepValues] = useState("2, 3, 4, 5");
  const [sweepMetric, setSweepMetric] = useState("lead_time_mean");
  const [saveAs, setSaveAs] = useState("");
  const [cal, setCal] = useState({ dataset: "", column: "", kind: "service", unit: "minutes" });
  const [calRes, setCalRes] = useState(null);
  const [error, setError] = useState("");

  const refreshSaved = () => api.scenarioModels().then((r) => setSaved(r.models)).catch(() => {});
  const setM = (m) => { setModel(m); setText(JSON.stringify(m, null, 2)); setJsonError(""); };
  useEffect(() => { api.scenarioTemplate().then((r) => setM(r.model)); refreshSaved(); }, []);
  useEffect(() => { if (!cal.dataset && names[0]) setCal((c) => ({ ...c, dataset: names[0] })); }, [names]);

  const guard = (key, fn) => async () => {
    setBusy(key); setError("");
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(""); }
  };
  const common = () => ({ model, save_as: saveAs || null });

  const applyJson = () => {
    try { setM(JSON.parse(text)); } catch (e) { setJsonError(e.message); }
  };
  const patch = (fn) => { const m = JSON.parse(JSON.stringify(model)); fn(m); setM(m); };
  const setStation = (i, key, value) => patch((m) => { m.stations[i][key] = value; });
  const setService = (i, dist, num) => patch((m) => {
    m.stations[i].service = dist === "fixed" ? { dist, value: num } : { dist: "exponential", mean: num };
  });

  const doRun = guard("run", async () => { setResult(await act(() => api.scenarioRun(common()), saveAs ? t("sc_saved") : null)); });
  const doCompare = guard("compare", async () => {
    const vs = variants.filter((v) => v.path).map((v) => {
      let value = v.value;
      try { value = JSON.parse(v.value); } catch { /* keep text */ }
      return { name: v.name, overrides: { [v.path]: value } };
    });
    setCmp(await act(() => api.scenarioCompare({ ...common(), variants: vs })));
  });
  const doSweep = guard("sweep", async () => {
    const values = sweepValues.split(/[,;\s]+/).filter(Boolean).map(Number);
    setSweep(await act(() => api.scenarioSweep({ ...common(), param: sweepParam, values })));
  });
  const doCalibrate = guard("cal", async () => { setCalRes(await api.scenarioCalibrate(cal)); });
  const doSave = guard("save", async () => {
    await api.scenarioSave(modelName, model); await refreshSaved(); setError("");
  });
  const doLoad = async (name) => {
    if (!name) return;
    try { setM((await api.scenarioGet(name)).model); setModelName(name); } catch (e) { setError(e.message); }
  };
  const doDelete = guard("del", async () => { await api.scenarioDelete(modelName); setModelName(""); await refreshSaved(); });

  const useSpec = (spec) => {
    patch((m) => { m.stations[0].service = spec; });
  };

  const sweepSpec = useMemo(() => {
    if (!sweep) return null;
    const rows = sweep.points.map((p) => ({ value: Number(p.value), ...p.metrics[sweepMetric] })).filter((r) => r.mean !== null);
    return {
      $schema: "https://vega.github.io/schema/vega-lite/v5.json",
      width: "container", height: 240, data: { values: rows },
      layer: [
        { mark: { type: "errorband", opacity: 0.2 }, encoding: { x: { field: "value", type: "quantitative", title: sweep.param }, y: { field: "ci_low", type: "quantitative", title: sweepMetric }, y2: { field: "ci_high" } } },
        { mark: { type: "line", point: true }, encoding: { x: { field: "value", type: "quantitative" }, y: { field: "mean", type: "quantitative" } } },
      ],
    };
  }, [sweep, sweepMetric]);

  if (!model) return <p className="help">{t("common_loading")}</p>;
  const arr = model.arrivals || {};

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">{t("sc_title")}</h1>
        <p className="help mt-1">{t("sc_intro")}</p>
      </div>
      {error && <div className="panel font-semibold" role="alert" data-testid="sc-error">{error}</div>}

      <div className="grid gap-6" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
        <div className="panel flex flex-col gap-3" data-testid="sc-model">
          <h2 className="font-semibold">{t("sc_model")}</h2>
          <div className="flex flex-wrap items-end gap-2">
            <div><label className="label" htmlFor="sc-saved">{t("sc_saved_models")}</label>
              <select id="sc-saved" className="field" value="" onChange={(e) => doLoad(e.target.value)}>
                <option value="">—</option>{saved.map((m) => <option key={m.name}>{m.name}</option>)}</select></div>
            <div><label className="label" htmlFor="sc-name">{t("sc_name")}</label><input id="sc-name" className="field" value={modelName} onChange={(e) => setModelName(e.target.value)} /></div>
            <button className="btn btn-sm" onClick={doSave} disabled={!modelName || !!busy}>{t("sc_save")}</button>
            <button className="btn btn-sm" onClick={doDelete} disabled={!modelName || !saved.some((m) => m.name === modelName) || !!busy}>{t("sc_delete")}</button>
          </div>
          <div className="flex flex-wrap gap-3">
            {[["horizon_hours", "sc_horizon"], ["warmup_hours", "sc_warmup"], ["replications", "sc_reps"], ["seed", "sc_seed"]].map(([k, l]) => (
              <label key={k} className="text-[13px]">{t(l)}<input className="field num" style={{ width: 90 }} type="number" value={model[k] ?? ""} onChange={(e) => patch((m) => { m[k] = Number(e.target.value); })} /></label>
            ))}
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-[13px]">{t("sc_arrivals")}
              <select className="field" value={arr.type} onChange={(e) => patch((m) => { m.arrivals = e.target.value === "poisson" ? { type: "poisson", rate: 4 } : e.target.value === "schedule" ? { type: "schedule", rates: Array(24).fill(4) } : { type: "empirical", values: [0.25] }; })}>
                <option value="poisson">{t("sc_arr_poisson")}</option><option value="schedule">{t("sc_arr_schedule")}</option><option value="empirical">{t("sc_arr_empirical")}</option></select></label>
            {arr.type === "poisson" && <label className="text-[13px]">{t("sc_rate")}<input className="field num" style={{ width: 90 }} type="number" min="0" step="any" value={arr.rate} onChange={(e) => patch((m) => { m.arrivals.rate = Number(e.target.value); })} /></label>}
            {arr.type !== "poisson" && <span className="help">{t("sc_arr_json")}</span>}
          </div>
          <table className="w-full text-[13px]" data-testid="sc-stations">
            <thead><tr><th className="px-2 font-medium text-left">{t("sc_station")}</th><th className="px-2 font-medium text-right">{t("sc_servers")}</th><th className="px-2 font-medium text-left">{t("sc_service")}</th><th className="px-2 font-medium text-right">{t("sc_mean_h")}</th><th className="px-2 font-medium text-right">{t("sc_buffer")}</th><th className="px-2 font-medium text-left">{t("sc_if_full")}</th><th /></tr></thead>
            <tbody>{model.stations.map((s, i) => {
              const d = s.service?.dist;
              const simple = d === "fixed" || d === "exponential";
              return (
                <tr key={i}>
                  <td><input className="field" aria-label={t("sc_station")} value={s.name} onChange={(e) => setStation(i, "name", e.target.value)} /></td>
                  <td><input className="field num" style={{ width: 64 }} type="number" min="1" value={s.servers} onChange={(e) => setStation(i, "servers", Math.max(1, Math.round(Number(e.target.value))))} /></td>
                  <td>{simple ? <select className="field" value={d} onChange={(e) => setService(i, e.target.value, s.service.mean ?? s.service.value)}><option value="exponential">{t("sc_d_exponential")}</option><option value="fixed">{t("sc_d_fixed")}</option></select> : <span className="help">{d}</span>}</td>
                  <td>{simple ? <input className="field num" style={{ width: 80 }} type="number" min="0" step="any" value={s.service.mean ?? s.service.value} onChange={(e) => setService(i, d, Number(e.target.value))} /> : <span className="help">JSON</span>}</td>
                  <td><input className="field num" style={{ width: 64 }} type="number" min="0" placeholder="∞" value={s.buffer ?? ""} onChange={(e) => setStation(i, "buffer", e.target.value === "" ? null : Math.max(0, Math.round(Number(e.target.value))))} /></td>
                  <td><select className="field" value={s.on_full || "lose"} onChange={(e) => setStation(i, "on_full", e.target.value)}><option value="lose">{t("sc_lose")}</option><option value="block">{t("sc_block")}</option></select></td>
                  <td><button className="btn btn-sm" aria-label={t("sc_remove")} disabled={model.stations.length < 2} onClick={() => patch((m) => { m.stations.splice(i, 1); })}>✕</button></td>
                </tr>
              );
            })}</tbody>
          </table>
          <button className="btn btn-sm self-start" onClick={() => patch((m) => { m.stations.push({ name: `Station ${m.stations.length + 1}`, servers: 1, service: { dist: "exponential", mean: 0.1 } }); })}>{t("sc_add_station")}</button>
          <label className="label" htmlFor="sc-json">{t("sc_json")}</label>
          <textarea id="sc-json" className="field mono" rows={12} value={text} onChange={(e) => setText(e.target.value)} data-testid="sc-json" />
          <div className="flex items-center gap-2"><button className="btn btn-sm" onClick={applyJson}>{t("sc_apply_json")}</button>{jsonError && <span className="help">{jsonError}</span>}</div>
        </div>

        <div className="flex flex-col gap-6">
          <div className="panel flex flex-col gap-3" data-testid="sc-calibrate">
            <h2 className="font-semibold">{t("sc_calibrate")}</h2>
            <div className="flex flex-wrap items-end gap-2">
              <label className="text-[13px]">{t("sc_dataset")}<select className="field" value={cal.dataset} onChange={(e) => setCal({ ...cal, dataset: e.target.value })}>{names.map((n) => <option key={n}>{n}</option>)}</select></label>
              <label className="text-[13px]">{t("sc_column")}<input className="field" value={cal.column} onChange={(e) => setCal({ ...cal, column: e.target.value })} /></label>
              <label className="text-[13px]">{t("sc_kind")}<select className="field" value={cal.kind} onChange={(e) => setCal({ ...cal, kind: e.target.value })}><option value="service">{t("sc_k_service")}</option><option value="interarrival">{t("sc_k_interarrival")}</option><option value="timestamps">{t("sc_k_timestamps")}</option></select></label>
              {cal.kind !== "timestamps" && <label className="text-[13px]">{t("sc_unit")}<select className="field" value={cal.unit} onChange={(e) => setCal({ ...cal, unit: e.target.value })}>{["seconds", "minutes", "hours", "days"].map((u) => <option key={u} value={u}>{t("sc_u_" + u)}</option>)}</select></label>}
              <button className="btn" onClick={doCalibrate} disabled={!cal.dataset || !cal.column || !!busy}>{busy === "cal" ? t("sc_working") : t("sc_fit")}</button>
            </div>
            {calRes && (
              <div className="flex flex-col gap-2" data-testid="sc-cal-result">
                <p>{t("sc_sample")}: <b className="num">{calRes.sample_size}</b> · {t("sc_mean_h")}: <Num v={calRes.summary.mean} lang={lang} /> · CV: <Num v={calRes.summary.cv} lang={lang} /> · p90: <Num v={calRes.summary.p90} lang={lang} /></p>
                {calRes.warnings.map((w, i) => <p key={i} className="font-semibold">{w}</p>)}
                <p className="help">{t("sc_suggested")}: {calRes.suggestion.dist} — {calRes.suggestion.reason}</p>
                {calRes.kind === "service" && <div className="flex flex-wrap gap-2">{Object.entries(calRes.specs).map(([k, spec]) => <button key={k} className="btn btn-sm" onClick={() => useSpec(spec)}>{t("sc_use_for_first")}: {k}</button>)}</div>}
                {calRes.kind === "interarrival" && calRes.arrivals && <div className="flex flex-wrap gap-2">{Object.entries(calRes.arrivals).filter(([, a]) => a).map(([k, a]) => <button key={k} className="btn btn-sm" onClick={() => patch((m) => { m.arrivals = a; })}>{t("sc_use_arrivals")}: {k}</button>)}</div>}
              </div>
            )}
          </div>

          <div className="panel flex flex-col gap-3" data-testid="sc-run">
            <h2 className="font-semibold">{t("sc_run")}</h2>
            <div><label className="label" htmlFor="sc-save-as">{t("sc_save_as")}</label><input id="sc-save-as" className="field" value={saveAs} onChange={(e) => setSaveAs(e.target.value)} /></div>
            <button className="btn btn-primary self-start" onClick={doRun} disabled={!!busy}>{busy === "run" ? t("sc_working") : t("sc_run_btn")}</button>
          </div>
        </div>
      </div>

      {result && (
        <div className="flex flex-col gap-4" data-testid="sc-result">
          <p className="help">{t("sc_result_note")} {result.replications} {t("sc_reps_lc")}, {t("sc_seed")} {result.seed}, {result.horizon_hours} h.</p>
          {result.calibration.map((c) => <p key={c.path} className="help">{t("sc_calibrated")}: {c.dataset}.{c.column} (n={c.sample_size})</p>)}
          {result.warnings.map((w, i) => <p key={i} className="font-semibold">{w}</p>)}
          <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
            <div className="panel overflow-x-auto">
              <table className="w-full text-[13px]" data-testid="sc-metrics">
                <thead><tr><th className="text-left">{t("sc_metric")}</th><th className="text-right">{t("sc_mean")}</th><th className="text-right">{t("sc_ci")}</th></tr></thead>
                <tbody>{MAIN.filter((k) => result.metrics[k]).map((k) => <tr key={k}><td>{t("sc_m_" + k)}</td><Ci m={result.metrics[k]} lang={lang} /></tr>)}</tbody>
              </table>
            </div>
            <div className="panel overflow-x-auto">
              <table className="w-full text-[13px]" data-testid="sc-station-metrics">
                <thead><tr><th className="text-left">{t("sc_station")}</th><th className="text-right">{t("sc_wait")}</th><th className="text-right">{t("sc_util")}</th><th className="text-right">{t("sc_queue")}</th><th className="text-right">{t("sc_lost")}</th></tr></thead>
                <tbody>{result.stations.map((s) => (
                  <tr key={s.name}><td>{s.name} ×{s.servers}</td><td className="text-right"><Num v={s.wait_hours.mean} lang={lang} /></td><td className="text-right"><Num v={s.utilisation.mean} lang={lang} /></td><td className="text-right"><Num v={s.avg_queue.mean} lang={lang} /></td><td className="text-right"><Num v={s.lost.mean} lang={lang} /></td></tr>
                ))}</tbody>
              </table>
            </div>
          </div>
          <div className="panel flex flex-col gap-1" data-testid="sc-checks">
            <h2 className="font-semibold">{t("sc_checks")}</h2>
            {result.checks.littles_law && <p className="text-[13px]"><Chip tone={result.checks.littles_law.ok ? "ok" : "warn"}>{result.checks.littles_law.ok ? t("sc_ok") : t("sc_check")}</Chip> {t("sc_little")}: WIP <Num v={result.checks.littles_law.wip} lang={lang} /> ≈ <Num v={result.checks.littles_law.throughput_x_lead_time} lang={lang} /> ({Math.round(result.checks.littles_law.relative_difference * 1000) / 10}%)</p>}
            {result.checks.analytic && <p className="text-[13px]"><Chip tone={result.checks.analytic.within_ci ? "ok" : "warn"}>{result.checks.analytic.model}</Chip> {t("sc_erlang")}: <Num v={result.checks.analytic.wq_hours} lang={lang} /> h · {t("sc_simulated")}: <Num v={result.checks.analytic.simulated_wq_hours} lang={lang} /> h · ρ <Num v={result.checks.analytic.rho} lang={lang} /></p>}
            {!result.checks.analytic && <p className="help">{t("sc_no_analytic")}</p>}
          </div>
          {result.saved_dataset && <p>{t("sc_saved")}: <a className="mono" href={`#/datasets/${encodeURIComponent(result.saved_dataset)}`}>{result.saved_dataset}</a></p>}
        </div>
      )}

      <div className="panel flex flex-col gap-3" data-testid="sc-compare">
        <h2 className="font-semibold">{t("sc_compare")}</h2>
        <p className="help">{t("sc_compare_help")}</p>
        {variants.map((v, i) => (
          <div key={i} className="flex flex-wrap items-center gap-2">
            <input className="field" aria-label={t("sc_name")} style={{ width: 140 }} value={v.name} onChange={(e) => setVariants(variants.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
            <input className="field mono" aria-label={t("sc_path")} placeholder="stations.Service.servers" style={{ width: 240 }} value={v.path} onChange={(e) => setVariants(variants.map((x, j) => j === i ? { ...x, path: e.target.value } : x))} />
            <span className="help">=</span>
            <input className="field mono" aria-label={t("sc_value")} style={{ width: 100 }} value={v.value} onChange={(e) => setVariants(variants.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} />
            <button className="btn btn-sm" onClick={() => setVariants(variants.filter((_, j) => j !== i))}>✕</button>
          </div>
        ))}
        <div className="flex gap-2">
          <button className="btn btn-sm" onClick={() => setVariants([...variants, { name: `Variant ${variants.length + 1}`, path: "", value: "" }])}>{t("sc_add_variant")}</button>
          <button className="btn btn-primary" onClick={doCompare} disabled={!!busy || !variants.some((v) => v.path)}>{busy === "compare" ? t("sc_working") : t("sc_compare_btn")}</button>
        </div>
        {cmp && (
          <div className="overflow-x-auto" data-testid="sc-compare-result">
            {cmp.variants.map((v) => (
              <div key={v.name} className="mb-4">
                <h3 className="font-medium">{v.name} <span className="help mono">{JSON.stringify(v.overrides)}</span></h3>
                <table className="w-full text-[13px]">
                  <thead><tr><th className="text-left">{t("sc_metric")}</th><th className="text-right">{t("sc_base")}</th><th className="text-right">{t("sc_variant")}</th><th className="text-right">{t("sc_delta")}</th><th className="text-right">{t("sc_ci")}</th><th className="text-left">{t("sc_verdict")}</th></tr></thead>
                  <tbody>{v.rows.filter((r) => MAIN.includes(r.metric) || r.metric.startsWith("wait:") || r.metric.startsWith("util:")).map((r) => (
                    <tr key={r.metric}>
                      <td>{MAIN.includes(r.metric) ? t("sc_m_" + r.metric) : r.metric}</td>
                      <td className="text-right"><Num v={r.base} lang={lang} /></td><td className="text-right"><Num v={r.variant} lang={lang} /></td>
                      <td className="text-right"><Num v={r.delta} lang={lang} /></td>
                      <td className="text-right help">{r.delta_ci_low === null ? "—" : <>[<Num v={r.delta_ci_low} lang={lang} />, <Num v={r.delta_ci_high} lang={lang} />]</>}</td>
                      <td>{r.distinguishable ? <Chip tone={r.better === "variant" ? "ok" : r.better === "base" ? "warn" : undefined}>{t("sc_distinct")}{r.better ? ` · ${t("sc_better_" + r.better)}` : ""}</Chip> : <span className="help">{t("sc_not_distinct")}</span>}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ))}
            <p className="help">{cmp.method}</p>
          </div>
        )}
      </div>

      <div className="panel flex flex-col gap-3" data-testid="sc-sweep">
        <h2 className="font-semibold">{t("sc_sweep")}</h2>
        <div className="flex flex-wrap items-end gap-2">
          <label className="text-[13px]">{t("sc_path")}<input className="field mono" style={{ width: 240 }} value={sweepParam} onChange={(e) => setSweepParam(e.target.value)} /></label>
          <label className="text-[13px]">{t("sc_values")}<input className="field mono" style={{ width: 200 }} value={sweepValues} onChange={(e) => setSweepValues(e.target.value)} /></label>
          <button className="btn btn-primary" onClick={doSweep} disabled={!!busy}>{busy === "sweep" ? t("sc_working") : t("sc_sweep_btn")}</button>
          {sweep && <label className="text-[13px]">{t("sc_metric")}<select className="field" value={sweepMetric} onChange={(e) => setSweepMetric(e.target.value)}>{MAIN.filter((k) => sweep.points[0].metrics[k]).map((k) => <option key={k} value={k}>{t("sc_m_" + k)}</option>)}</select></label>}
        </div>
        {sweepSpec && <div data-testid="sc-sweep-chart"><VegaChart spec={sweepSpec} height={240} /></div>}
      </div>
    </div>
  );
}
