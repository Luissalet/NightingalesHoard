import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Chip } from "../components/ui.jsx";
import VegaChart from "../components/VegaChart.jsx";
import { formatNumber } from "../format.js";

const SCHEMA = "https://vega.github.io/schema/vega-lite/v5.json";
const split = (s) => String(s || "").split(/[,;\n]+/).map((x) => x.trim()).filter(Boolean);
const jsonOr = (text, fallback) => { try { return JSON.parse(text); } catch { return fallback; } };

function Num({ v, lang, digits = 3 }) {
  return <span className="num">{typeof v === "number" ? formatNumber(v, lang, { maximumFractionDigits: digits }) : "—"}</span>;
}
function Range({ lo, hi, lang }) {
  if (lo === null || lo === undefined) return <span className="help">—</span>;
  return <span className="num help">[<Num v={lo} lang={lang} />, <Num v={hi} lang={lang} />]</span>;
}
function Warnings({ items }) {
  if (!items || !items.length) return null;
  return <div className="flex flex-col gap-1" data-testid="ex-warnings">{items.map((w, i) => <p key={i} className="font-semibold text-[13px]">⚠ {w}</p>)}</div>;
}
function Field({ label, children, id }) {
  return <div className="flex flex-col"><label className="label" htmlFor={id}>{label}</label>{children}</div>;
}

function useColumns(dataset) {
  const [cols, setCols] = useState([]);
  useEffect(() => {
    if (!dataset) { setCols([]); return; }
    let live = true;
    api.profile(dataset).then((r) => { if (live) setCols(r.columns.map((c) => c.name)); }).catch(() => { if (live) setCols([]); });
    return () => { live = false; };
  }, [dataset]);
  return cols;
}

function DatasetPick({ id, value, onChange, names, t }) {
  return (
    <Field label={t("sc_dataset")} id={id}>
      <select id={id} className="field" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>{names.map((n) => <option key={n}>{n}</option>)}
      </select>
    </Field>
  );
}

function useRunner() {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const run = async (key, fn) => {
    setBusy(key); setError("");
    try { return await fn(); } catch (e) { setError(e.message); return null; } finally { setBusy(""); }
  };
  return { busy, error, run, setError };
}

/* ------------------------------------------------------------------ twin */
function TwinTab() {
  const { t, lang, datasets, act } = useApp();
  const names = useMemo(() => datasets.map((d) => d.name), [datasets]);
  const [f, setF] = useState({ dataset: "", time: "", outputs: "", inputs: "", model: "arx", valPct: 25, maxLag: "", name: "" });
  const cols = useColumns(f.dataset);
  const [report, setReport] = useState(null);
  const [saved, setSaved] = useState([]);
  const [twinName, setTwinName] = useState("");
  const [twin, setTwin] = useState(null);
  const [profile, setProfile] = useState({});
  const [steps, setSteps] = useState(50);
  const [allow, setAllow] = useState(false);
  const [sim, setSim] = useState(null);
  const { busy, error, run, setError } = useRunner();
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }));
  const refresh = () => api.twinList().then((r) => setSaved(r.twins)).catch(() => {});
  useEffect(() => { refresh(); }, []);
  useEffect(() => { if (!f.dataset && names[0]) set("dataset", names[0]); }, [names]);

  const doFit = () => run("fit", async () => {
    const body = { dataset: f.dataset, time: f.time || null, outputs: split(f.outputs), inputs: split(f.inputs), model: f.model,
      validation_fraction: Number(f.valPct) / 100, max_lag: f.maxLag ? Number(f.maxLag) : null, name: f.name || null };
    const r = await act(() => api.twinFit(body), f.name ? t("ex_saved") : null);
    setReport(r); if (f.name) { setTwinName(f.name); refresh(); }
  });
  const loadTwin = async (name) => {
    setTwinName(name); setSim(null); setError("");
    if (!name) { setTwin(null); return; }
    try { const r = await api.twinGet(name); setTwin(r.twin); setProfile({}); } catch (e) { setError(e.message); }
  };
  useEffect(() => { if (twinName && !twin) loadTwin(twinName); }, [twinName]);
  const profileOf = (inp) => profile[inp] || { mode: "hold", a: "", b: "", at: 5, list: "" };
  const setProf = (inp, patch) => setProfile((p) => ({ ...p, [inp]: { ...profileOf(inp), ...patch } }));
  const buildProfile = () => {
    const out = {};
    for (const inp of twin.inputs) {
      const p = profileOf(inp);
      if (p.mode === "hold" && p.a !== "") out[inp] = Number(p.a);
      if (p.mode === "step" && p.b !== "") out[inp] = { step: { at: Number(p.at), ...(p.a !== "" ? { from: Number(p.a) } : {}), to: Number(p.b) } };
      if (p.mode === "ramp" && p.b !== "") out[inp] = { ramp: { ...(p.a !== "" ? { from: Number(p.a) } : {}), to: Number(p.b) } };
      if (p.mode === "list" && p.list) out[inp] = split(p.list).map(Number);
    }
    return out;
  };
  const doSim = () => run("sim", async () => { setSim(await act(() => api.twinSimulate({ name: twinName, profile: buildProfile(), steps: Number(steps), allow_extrapolation: allow }))); });

  const valSpec = useMemo(() => {
    if (!report) return null;
    const out = report.outputs[0];
    const v = report.validation_series;
    const o = v[out];
    const rows = v.time.map((_, i) => ({ step: i + 1, observed: o.observed[i], simulated: o.simulated[i], lo: o.lo ? o.lo[i] : null, hi: o.hi ? o.hi[i] : null }));
    const layers = [];
    if (o.lo) layers.push({ mark: { type: "area", opacity: 0.2 }, encoding: { x: { field: "step", type: "quantitative", title: t("ex_step") }, y: { field: "lo", type: "quantitative", title: out }, y2: { field: "hi" } } });
    layers.push({ mark: { type: "line", color: "#999" }, encoding: { x: { field: "step", type: "quantitative" }, y: { field: "observed", type: "quantitative" } } });
    layers.push({ mark: { type: "line", strokeDash: [4, 3] }, encoding: { x: { field: "step", type: "quantitative" }, y: { field: "simulated", type: "quantitative" } } });
    return { $schema: SCHEMA, width: "container", height: 220, data: { values: rows }, layer: layers };
  }, [report, lang]);

  const simSpec = useMemo(() => {
    if (!sim) return null;
    const out = Object.keys(sim.outputs)[0];
    const o = sim.outputs[out];
    const b = sim.baseline?.outputs[out];
    const rows = sim.time.map((_, i) => ({ step: i + 1, nominal: o.nominal[i], lo: o.lo[i], hi: o.hi[i], base: b ? b.nominal[i] : null }));
    const layers = [
      { mark: { type: "area", opacity: 0.2 }, encoding: { x: { field: "step", type: "quantitative", title: t("ex_step") }, y: { field: "lo", type: "quantitative", title: out }, y2: { field: "hi" } } },
      { mark: { type: "line" }, encoding: { x: { field: "step", type: "quantitative" }, y: { field: "nominal", type: "quantitative" } } },
    ];
    if (b) layers.push({ mark: { type: "line", color: "#999", strokeDash: [4, 3] }, encoding: { x: { field: "step", type: "quantitative" }, y: { field: "base", type: "quantitative" } } });
    return { $schema: SCHEMA, width: "container", height: 220, data: { values: rows }, layer: layers };
  }, [sim, lang]);

  const rep = report;
  return (
    <div className="flex flex-col gap-6">
      {error && <div className="panel font-semibold" role="alert" data-testid="ex-error">{error}</div>}
      <div className="panel flex flex-col gap-3" data-testid="ex-twin-fit">
        <h2 className="font-semibold">{t("ex_tw_fit")}</h2>
        <p className="help">{t("ex_tw_help")}</p>
        <datalist id="ex-cols">{cols.map((c) => <option key={c} value={c} />)}</datalist>
        <div className="flex flex-wrap items-end gap-3">
          <DatasetPick id="ex-tw-ds" value={f.dataset} onChange={(v) => set("dataset", v)} names={names} t={t} />
          <Field label={t("ex_time_col")} id="ex-tw-time"><input id="ex-tw-time" list="ex-cols" className="field" value={f.time} onChange={(e) => set("time", e.target.value)} /></Field>
          <Field label={t("ex_outputs")} id="ex-tw-out"><input id="ex-tw-out" list="ex-cols" className="field" value={f.outputs} onChange={(e) => set("outputs", e.target.value)} /></Field>
          <Field label={t("ex_inputs")} id="ex-tw-in"><input id="ex-tw-in" list="ex-cols" className="field" value={f.inputs} onChange={(e) => set("inputs", e.target.value)} /></Field>
          <Field label={t("ex_model")} id="ex-tw-model"><select id="ex-tw-model" className="field" value={f.model} onChange={(e) => set("model", e.target.value)}><option value="arx">ARX</option><option value="fopdt">FOPDT</option><option value="auto">{t("ex_auto")}</option></select></Field>
          <Field label={t("ex_val_pct")} id="ex-tw-val"><input id="ex-tw-val" className="field num" style={{ width: 80 }} type="number" min="10" max="50" value={f.valPct} onChange={(e) => set("valPct", e.target.value)} /></Field>
          <Field label={t("ex_max_lag")} id="ex-tw-lag"><input id="ex-tw-lag" className="field num" style={{ width: 80 }} type="number" min="1" max="12" value={f.maxLag} onChange={(e) => set("maxLag", e.target.value)} /></Field>
          <Field label={t("sc_name")} id="ex-tw-name"><input id="ex-tw-name" className="field" value={f.name} onChange={(e) => set("name", e.target.value)} /></Field>
          <button className="btn btn-primary" onClick={doFit} disabled={!f.dataset || !f.outputs || !f.inputs || !!busy}>{busy === "fit" ? t("sc_working") : t("ex_fit_btn")}</button>
        </div>
      </div>

      {rep && (
        <div className="flex flex-col gap-4" data-testid="ex-twin-report">
          <Warnings items={rep.warnings} />
          <p className="help">{rep.model.toUpperCase()} · {t("ex_rows")}: {rep.sampling.rows_used}/{rep.sampling.rows_total} · {t("ex_fit_rows")} {rep.split.fit_rows} · {t("ex_val_rows")} {rep.split.validation_rows} · {rep.nrmse_definition}</p>
          <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
            <div className="panel overflow-x-auto">
              <h3 className="font-medium mb-1">{t("ex_validation")}</h3>
              <table className="w-full text-[13px]" data-testid="ex-val-table">
                <thead><tr><th className="text-left">{t("ex_output")}</th><th className="text-right">NRMSE ({t("ex_free_run")})</th><th className="text-right">R²</th>{rep.validation.one_step && <th className="text-right">NRMSE ({t("ex_one_step")})</th>}<th className="text-right">{t("ex_coverage")}</th></tr></thead>
                <tbody>{Object.entries(rep.validation.free_run).map(([o, m]) => (
                  <tr key={o}><td>{o}</td><td className="text-right"><Num v={m.nrmse} lang={lang} /></td><td className="text-right"><Num v={m.r2} lang={lang} /></td>
                    {rep.validation.one_step && <td className="text-right"><Num v={rep.validation.one_step[o]?.nrmse} lang={lang} /></td>}
                    <td className="text-right">{rep.interval_coverage?.[o] !== undefined ? `${Math.round(rep.interval_coverage[o] * 100)}%` : "—"}</td></tr>
                ))}</tbody>
              </table>
              <p className="mt-2 text-[13px]"><Chip tone={rep.residual_check.ok ? "ok" : "warn"}>{rep.residual_check.ok ? t("sc_ok") : t("sc_check")}</Chip> {t("ex_resid_check")}</p>
              {rep.structure.lag_order && <p className="help">{t("ex_lag")}: {rep.structure.lag_order} · {rep.stable ? t("ex_stable") : t("ex_unstable")}</p>}
            </div>
            <div className="panel overflow-x-auto">
              <h3 className="font-medium mb-1">{t("ex_gains")}</h3>
              <table className="w-full text-[13px]" data-testid="ex-gains">
                <thead><tr><th className="text-left">{t("ex_input")} → {t("ex_output")}</th><th className="text-right">{t("ex_gain")}</th><th className="text-right">{t("sc_ci")}</th></tr></thead>
                <tbody>{rep.gains.map((g) => <tr key={g.input + g.output}><td>{g.input} → {g.output}</td><td className="text-right"><Num v={g.gain} lang={lang} /></td><td className="text-right"><Range lo={g.ci_low} hi={g.ci_high} lang={lang} /></td></tr>)}</tbody>
              </table>
              <details className="mt-2"><summary className="help cursor-pointer">{t("ex_params")}</summary>
                <table className="w-full text-[13px]"><tbody>{rep.parameters.map((p) => <tr key={p.output + p.term}><td>{p.output} · {p.term}</td><td className="text-right"><Num v={p.estimate} lang={lang} /></td><td className="text-right"><Range lo={p.ci_low} hi={p.ci_high} lang={lang} /></td></tr>)}</tbody></table>
              </details>
            </div>
          </div>
          {valSpec && <div className="panel" data-testid="ex-val-chart"><h3 className="font-medium mb-1">{t("ex_val_chart")} — {rep.outputs[0]}</h3><VegaChart spec={valSpec} height={220} /><p className="help">{t("ex_val_legend")}</p></div>}
          <p className="help">{rep.caveat}</p>
        </div>
      )}

      <div className="panel flex flex-col gap-3" data-testid="ex-twin-sim">
        <h2 className="font-semibold">{t("ex_whatif")}</h2>
        <div className="flex flex-wrap items-end gap-3">
          <Field label={t("ex_twin")} id="ex-tw-pick"><select id="ex-tw-pick" className="field" value={twinName} onChange={(e) => loadTwin(e.target.value)}><option value="">—</option>{saved.map((s) => <option key={s.name}>{s.name}</option>)}</select></Field>
          {twinName && <button className="btn btn-sm" onClick={async () => { await run("del", () => api.twinDelete(twinName)); setTwinName(""); setTwin(null); refresh(); }}>{t("sc_delete")}</button>}
          <Field label={t("ex_steps")} id="ex-tw-steps"><input id="ex-tw-steps" className="field num" style={{ width: 80 }} type="number" min="1" value={steps} onChange={(e) => setSteps(e.target.value)} /></Field>
        </div>
        {twin && twin.inputs.map((inp) => {
          const p = profileOf(inp);
          const range = twin.training.input_range[inp];
          return (
            <div key={inp} className="flex flex-wrap items-end gap-2">
              <span className="font-medium" style={{ minWidth: 90 }}>{inp}</span>
              <select className="field" aria-label={t("ex_profile")} value={p.mode} onChange={(e) => setProf(inp, { mode: e.target.value })}>
                <option value="hold">{t("ex_p_hold")}</option><option value="step">{t("ex_p_step")}</option><option value="ramp">{t("ex_p_ramp")}</option><option value="list">{t("ex_p_list")}</option></select>
              {p.mode !== "list" && <input className="field num" style={{ width: 90 }} type="number" step="any" placeholder={p.mode === "hold" ? t("ex_value") : t("ex_from")} value={p.a} onChange={(e) => setProf(inp, { a: e.target.value })} />}
              {(p.mode === "step" || p.mode === "ramp") && <input className="field num" style={{ width: 90 }} type="number" step="any" placeholder={t("ex_to")} value={p.b} onChange={(e) => setProf(inp, { b: e.target.value })} />}
              {p.mode === "step" && <input className="field num" style={{ width: 70 }} type="number" min="0" placeholder={t("ex_at")} value={p.at} onChange={(e) => setProf(inp, { at: e.target.value })} />}
              {p.mode === "list" && <input className="field mono" style={{ width: 240 }} placeholder="1, 1.5, 2" value={p.list} onChange={(e) => setProf(inp, { list: e.target.value })} />}
              <span className="help">{t("ex_trained_on")} <Range lo={range[0]} hi={range[1]} lang={lang} /></span>
            </div>
          );
        })}
        {twin && (
          <div className="flex items-center gap-3">
            <label className="text-[13px] flex items-center gap-1"><input type="checkbox" checked={allow} onChange={(e) => setAllow(e.target.checked)} />{t("ex_allow_extra")}</label>
            <button className="btn btn-primary" onClick={doSim} disabled={!!busy}>{busy === "sim" ? t("sc_working") : t("ex_sim_btn")}</button>
          </div>
        )}
        {sim && (
          <div className="flex flex-col gap-2" data-testid="ex-sim-result">
            <Warnings items={sim.warnings} />
            {sim.extrapolation.outside_training_range && <p className="font-semibold" data-testid="ex-extrapolated">{t("ex_extrapolated")}</p>}
            {Object.entries(sim.delta_vs_baseline || {}).map(([o, d]) => <p key={o} className="text-[13px]">{o}: {t("ex_delta_final")} <Num v={d.final_nominal} lang={lang} /></p>)}
            {simSpec && <VegaChart spec={simSpec} height={220} />}
            <p className="help">{sim.interval_note}</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ design and next run */
const DEFAULT_FACTORS = [{ name: "A", low: 0, high: 10 }, { name: "B", low: 0, high: 10 }, { name: "C", low: 0, high: 10 }, { name: "D", low: 0, high: 10 }];
const DEFAULT_HYP = JSON.stringify({
  hypotheses: ["pump", "valve", "sensor"],
  tests: [
    { name: "swap sensor", cost: 1, outcomes: { pump: "unchanged", valve: "unchanged", sensor: "fixed" } },
    { name: "pressure check", cost: 2, outcomes: { pump: "low", valve: "high", sensor: "normal" } },
    { name: "strip valve", cost: 8, outcomes: { pump: "ok", valve: "worn", sensor: "ok" } },
  ],
}, null, 2);

function DesignTab() {
  const { t, lang, datasets, act } = useApp();
  const names = useMemo(() => datasets.map((d) => d.name), [datasets]);
  const [factors, setFactors] = useState(DEFAULT_FACTORS);
  const [d, setD] = useState({ kind: "fractional", minRes: 4, centers: "", seed: 0, runs: 20, variant: "face", forbidden: "", saveAs: "" });
  const [plan, setPlan] = useState(null);
  const [a, setA] = useState({ dataset: "", response: "", model: "auto", objective: "explore", n: 1 });
  const cols = useColumns(a.dataset);
  const [analysis, setAnalysis] = useState(null);
  const [next, setNext] = useState(null);
  const [hyp, setHyp] = useState(DEFAULT_HYP);
  const [choice, setChoice] = useState(null);
  const { busy, error, run } = useRunner();
  useEffect(() => { if (!a.dataset && names[0]) setA((x) => ({ ...x, dataset: names[0] })); }, [names]);
  const setF = (i, patch) => setFactors(factors.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  const facBody = () => factors.map((f) => ({ name: f.name, low: Number(f.low), high: Number(f.high) }));
  const analysisFactors = () => (a.dataset && factors.every((f) => cols.includes(f.name)) ? facBody() : factors.filter((f) => cols.includes(f.name)).map((f) => ({ name: f.name, low: Number(f.low), high: Number(f.high) })));

  const doDesign = () => run("design", async () => {
    setPlan(await act(() => api.expDesign({ factors: facBody(), kind: d.kind, min_resolution: Number(d.minRes), center_points: d.centers === "" ? null : Number(d.centers),
      seed: Number(d.seed), runs: Number(d.runs), variant: d.variant, forbidden: d.forbidden.split("\n").map((x) => x.trim()).filter(Boolean), save_as: d.saveAs || null }), d.saveAs ? t("sc_saved") : null));
  });
  const doAnalyze = () => run("analyze", async () => { setAnalysis(await act(() => api.expAnalyze({ dataset: a.dataset, factors: analysisFactors(), response: a.response, model: a.model }))); });
  const doNext = () => run("next", async () => { setNext(await act(() => api.expNext({ dataset: a.dataset, factors: analysisFactors(), response: a.response, objective: a.objective, n: Number(a.n),
    forbidden: d.forbidden.split("\n").map((x) => x.trim()).filter(Boolean), model: a.model }))); });
  const doChoose = () => run("choose", async () => {
    const body = jsonOr(hyp, null);
    if (!body) throw new Error(t("ex_bad_json"));
    setChoice(await act(() => api.expChoose(body)));
  });

  const effectSpec = useMemo(() => {
    if (!analysis) return null;
    const rows = analysis.terms.filter((x) => x.kind !== "intercept").map((x) => {
      const k = x.effect !== null && x.effect !== undefined ? 2 : 1;
      return { term: x.term, effect: x.effect ?? x.coefficient, lo: x.ci_low === null || x.ci_low === undefined ? null : k * x.ci_low, hi: x.ci_high === null || x.ci_high === undefined ? null : k * x.ci_high, sig: x.significant ? t("ex_sig") : t("ex_notsig") };
    });
    const layers = [{ mark: "bar", encoding: { y: { field: "term", type: "nominal", sort: "-x", title: null }, x: { field: "effect", type: "quantitative", title: t("ex_effect") }, color: { field: "sig", type: "nominal", title: null } } }];
    if (rows.some((r) => r.lo !== null)) layers.push({ mark: { type: "rule" }, encoding: { y: { field: "term", type: "nominal", sort: "-x" }, x: { field: "lo", type: "quantitative" }, x2: { field: "hi" } } });
    return { $schema: SCHEMA, width: "container", height: Math.max(120, rows.length * 22), data: { values: rows }, layer: layers };
  }, [analysis, lang]);

  const surfSpec = useMemo(() => {
    const s = analysis?.surface;
    if (!s) return null;
    const rows = [];
    s.x_values.forEach((xv, i) => s.y_values.forEach((yv, j) => rows.push({ x: Number(xv.toFixed(3)), y: Number(yv.toFixed(3)), z: s.mean[i][j] })));
    return { $schema: SCHEMA, width: "container", height: 260, data: { values: rows },
      mark: "rect", encoding: { x: { field: "x", type: "ordinal", title: s.x }, y: { field: "y", type: "ordinal", title: s.y, sort: "descending" }, color: { field: "z", type: "quantitative", title: analysis.response } } };
  }, [analysis, lang]);

  const aliasRows = plan?.info?.alias_structure?.filter((x) => x.aliased_with.length).slice(0, 12) || [];
  return (
    <div className="flex flex-col gap-6">
      {error && <div className="panel font-semibold" role="alert" data-testid="ex-error">{error}</div>}
      <div className="grid gap-6" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
        <div className="panel flex flex-col gap-3" data-testid="ex-design">
          <h2 className="font-semibold">{t("ex_design")}</h2>
          {factors.map((f, i) => (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <input className="field" aria-label={t("sc_name")} style={{ width: 120 }} value={f.name} onChange={(e) => setF(i, { name: e.target.value })} />
              <input className="field num" aria-label={t("ex_low")} style={{ width: 80 }} type="number" step="any" value={f.low} onChange={(e) => setF(i, { low: e.target.value })} />
              <input className="field num" aria-label={t("ex_high")} style={{ width: 80 }} type="number" step="any" value={f.high} onChange={(e) => setF(i, { high: e.target.value })} />
              <button className="btn btn-sm" aria-label={t("sc_remove")} disabled={factors.length < 2} onClick={() => setFactors(factors.filter((_, j) => j !== i))}>✕</button>
            </div>
          ))}
          <button className="btn btn-sm self-start" onClick={() => setFactors([...factors, { name: `F${factors.length + 1}`, low: 0, high: 1 }])}>{t("ex_add_factor")}</button>
          <div className="flex flex-wrap items-end gap-3">
            <Field label={t("ex_kind")} id="ex-kind"><select id="ex-kind" className="field" value={d.kind} onChange={(e) => setD({ ...d, kind: e.target.value })}>
              <option value="fractional">{t("ex_k_fractional")}</option><option value="full">{t("ex_k_full")}</option><option value="ccd">{t("ex_k_ccd")}</option><option value="lhs">{t("ex_k_lhs")}</option></select></Field>
            {d.kind === "fractional" && <Field label={t("ex_min_res")} id="ex-minres"><select id="ex-minres" className="field" value={d.minRes} onChange={(e) => setD({ ...d, minRes: e.target.value })}><option value="3">III</option><option value="4">IV</option><option value="5">V</option></select></Field>}
            {d.kind === "ccd" && <Field label={t("ex_variant")} id="ex-variant"><select id="ex-variant" className="field" value={d.variant} onChange={(e) => setD({ ...d, variant: e.target.value })}><option value="face">{t("ex_v_face")}</option><option value="inscribed">{t("ex_v_inscribed")}</option><option value="circumscribed">{t("ex_v_circ")}</option></select></Field>}
            {d.kind === "lhs" && <Field label={t("ex_runs")} id="ex-runs"><input id="ex-runs" className="field num" style={{ width: 80 }} type="number" min="3" value={d.runs} onChange={(e) => setD({ ...d, runs: e.target.value })} /></Field>}
            <Field label={t("ex_centers")} id="ex-centers"><input id="ex-centers" className="field num" style={{ width: 70 }} type="number" min="0" value={d.centers} onChange={(e) => setD({ ...d, centers: e.target.value })} /></Field>
            <Field label={t("sc_seed")} id="ex-seed"><input id="ex-seed" className="field num" style={{ width: 70 }} type="number" value={d.seed} onChange={(e) => setD({ ...d, seed: e.target.value })} /></Field>
          </div>
          <Field label={t("ex_forbidden")} id="ex-forb"><textarea id="ex-forb" className="field mono" rows={2} placeholder="A + B > 15" value={d.forbidden} onChange={(e) => setD({ ...d, forbidden: e.target.value })} /></Field>
          <div className="flex flex-wrap items-end gap-3">
            <Field label={t("sc_save_as")} id="ex-saveas"><input id="ex-saveas" className="field" value={d.saveAs} onChange={(e) => setD({ ...d, saveAs: e.target.value })} /></Field>
            <button className="btn btn-primary" onClick={doDesign} disabled={!!busy}>{busy === "design" ? t("sc_working") : t("ex_design_btn")}</button>
          </div>
        </div>

        <div className="panel flex flex-col gap-3" data-testid="ex-results">
          <h2 className="font-semibold">{t("ex_results")}</h2>
          <p className="help">{t("ex_results_help")}</p>
          <div className="flex flex-wrap items-end gap-3">
            <DatasetPick id="ex-an-ds" value={a.dataset} onChange={(v) => setA({ ...a, dataset: v })} names={names} t={t} />
            <Field label={t("ex_response")} id="ex-response"><input id="ex-response" list="ex-an-cols" className="field" value={a.response} onChange={(e) => setA({ ...a, response: e.target.value })} /></Field>
            <datalist id="ex-an-cols">{cols.map((c) => <option key={c} value={c} />)}</datalist>
            <Field label={t("ex_model")} id="ex-an-model"><select id="ex-an-model" className="field" value={a.model} onChange={(e) => setA({ ...a, model: e.target.value })}><option value="auto">{t("ex_auto")}</option><option value="main">{t("ex_m_main")}</option><option value="interactions">{t("ex_m_inter")}</option><option value="quadratic">{t("ex_m_quad")}</option></select></Field>
            <button className="btn btn-primary" onClick={doAnalyze} disabled={!a.dataset || !a.response || !!busy}>{busy === "analyze" ? t("sc_working") : t("ex_analyze_btn")}</button>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <Field label={t("ex_objective")} id="ex-obj"><select id="ex-obj" className="field" value={a.objective} onChange={(e) => setA({ ...a, objective: e.target.value })}><option value="explore">{t("ex_o_explore")}</option><option value="maximize">{t("ex_o_max")}</option><option value="minimize">{t("ex_o_min")}</option></select></Field>
            <Field label={t("ex_how_many")} id="ex-n"><input id="ex-n" className="field num" style={{ width: 70 }} type="number" min="1" max="20" value={a.n} onChange={(e) => setA({ ...a, n: e.target.value })} /></Field>
            <button className="btn btn-primary" onClick={doNext} disabled={!a.dataset || !a.response || !!busy}>{busy === "next" ? t("sc_working") : t("ex_next_btn")}</button>
          </div>
        </div>
      </div>

      {plan && (
        <div className="panel flex flex-col gap-2" data-testid="ex-plan">
          <h2 className="font-semibold">{t("ex_plan")} — {plan.n_runs} {t("ex_runs_lc")}{plan.info.resolution ? ` · ${t("ex_resolution")} ${plan.info.resolution}` : ""}</h2>
          <Warnings items={plan.warnings} />
          {plan.info.interpretation && <p className="help">{plan.info.interpretation}</p>}
          {plan.info.generators?.length > 0 && <p className="help mono">{plan.info.generators.join(", ")} · {(plan.info.defining_relation || []).join(", ")}</p>}
          {aliasRows.length > 0 && <p className="help">{t("ex_aliases")}: {aliasRows.map((r) => `${r.effect} = ${r.aliased_with.join(" + ")}`).join(" · ")}</p>}
          <p className="help">{t("ex_orthogonal")}: <Num v={plan.properties.max_abs_correlation_between_factors} lang={lang} /> · {Object.entries(plan.properties.estimable).map(([k, v]) => <span key={k}><Chip tone={v ? "ok" : "warn"}>{t("ex_est_" + k)}</Chip> </span>)}</p>
          <div className="overflow-x-auto" style={{ maxHeight: 320 }}>
            <table className="w-full text-[13px]"><thead><tr><th>#</th><th>{t("ex_type")}</th>{plan.factors.map((f) => <th key={f.name} className="text-right">{f.name}</th>)}</tr></thead>
              <tbody>{plan.runs.slice(0, 80).map((r) => <tr key={r.run}><td>{r.run}</td><td>{r.type}</td>{plan.factors.map((f) => <td key={f.name} className="text-right"><Num v={r[f.name]} lang={lang} /></td>)}</tr>)}</tbody></table>
          </div>
          {plan.saved_dataset && <p>{t("sc_saved")}: <a className="mono" href={`#/datasets/${encodeURIComponent(plan.saved_dataset)}`}>{plan.saved_dataset}</a></p>}
          <p className="help">{plan.note}</p>
        </div>
      )}

      {analysis && (
        <div className="flex flex-col gap-4" data-testid="ex-analysis">
          <h2 className="font-semibold">{t("ex_analysis")} — {analysis.response} · R² <Num v={analysis.fit.r2} lang={lang} /> · {t("ex_resid_df")} {analysis.fit.residual_df}</h2>
          <Warnings items={analysis.warnings} />
          <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))" }}>
            <div className="panel">{effectSpec && <VegaChart spec={effectSpec} height={Math.max(120, analysis.terms.length * 22)} />}<p className="help">{analysis.coding}</p></div>
            <div className="panel overflow-x-auto"><table className="w-full text-[13px]" data-testid="ex-terms">
              <thead><tr><th className="text-left">{t("ex_term")}</th><th className="text-right">{t("ex_effect")}</th><th className="text-right">{t("sc_ci")}</th><th className="text-right">p</th></tr></thead>
              <tbody>{analysis.terms.map((x) => <tr key={x.term}><td>{x.term}</td><td className="text-right"><Num v={x.effect ?? x.coefficient} lang={lang} /></td>
                <td className="text-right"><Range lo={x.ci_low === undefined || x.ci_low === null ? null : (x.effect !== null && x.effect !== undefined ? 2 : 1) * x.ci_low} hi={x.ci_high === undefined || x.ci_high === null ? null : (x.effect !== null && x.effect !== undefined ? 2 : 1) * x.ci_high} lang={lang} /></td>
                <td className="text-right"><Num v={x.p_value} lang={lang} /></td></tr>)}</tbody></table></div>
          </div>
          {surfSpec && <div className="panel" data-testid="ex-surface"><h3 className="font-medium mb-1">{t("ex_surface")}</h3><VegaChart spec={surfSpec} height={260} /><p className="help">{t("ex_surface_note")}</p></div>}
          {analysis.stationary_point && <p className="text-[13px]">{t("ex_stationary")}: {analysis.stationary_point.type}, {Object.entries(analysis.stationary_point.settings).map(([k, v]) => `${k}=${formatNumber(v, lang, { maximumFractionDigits: 3 })}`).join(", ")} → <Num v={analysis.stationary_point.predicted} lang={lang} /> ± <Num v={analysis.stationary_point.ci_half} lang={lang} />{analysis.stationary_point.inside_region ? "" : ` (${t("ex_outside_region")})`}</p>}
          {analysis.notes.map((n, i) => <p key={i} className="help">{n}</p>)}
        </div>
      )}

      {next && (
        <div className="panel flex flex-col gap-2" data-testid="ex-next">
          <h2 className="font-semibold">{t("ex_next")}</h2>
          <Warnings items={next.warnings} />
          {next.recommendations.map((r) => (
            <div key={r.rank} className="border-t pt-2">
              <p className="font-medium">#{r.rank}: {Object.entries(r.settings).map(([k, v]) => `${k} = ${formatNumber(v, lang, { maximumFractionDigits: 4 })}`).join(", ")}</p>
              <p className="text-[13px]">{t("ex_pred")}: <Num v={r.predicted_mean} lang={lang} /> ± <Num v={r.predicted_sd} lang={lang} />{r.expected_improvement !== undefined ? <> · EI <Num v={r.expected_improvement} lang={lang} /></> : null}</p>
              <p className="text-[13px]"><b>{t("ex_why")}:</b> {r.why}</p>
            </div>
          ))}
          <p className="help">{next.model.caveat}</p>
        </div>
      )}

      <div className="panel flex flex-col gap-3" data-testid="ex-choose">
        <h2 className="font-semibold">{t("ex_choose")}</h2>
        <p className="help">{t("ex_choose_help")}</p>
        <textarea className="field mono" rows={12} aria-label={t("ex_choose")} value={hyp} onChange={(e) => setHyp(e.target.value)} />
        <button className="btn btn-primary self-start" onClick={doChoose} disabled={!!busy}>{busy === "choose" ? t("sc_working") : t("ex_choose_btn")}</button>
        {choice && (
          <div className="flex flex-col gap-2" data-testid="ex-choice">
            <p><b>{choice.recommendation.test}</b> — {choice.recommendation.why}</p>
            {choice.cheapest_fully_separating && <p className="help">{t("ex_cheapest_sep")}: {choice.cheapest_fully_separating.test} ({choice.cheapest_fully_separating.cost})</p>}
            <table className="w-full text-[13px]"><thead><tr><th className="text-left">{t("ex_test")}</th><th className="text-right">{t("ex_cost")}</th><th className="text-right">{t("ex_bits")}</th><th className="text-right">{t("ex_bits_per_cost")}</th><th>{t("ex_separates")}</th></tr></thead>
              <tbody>{choice.ranking.map((r) => <tr key={r.test}><td>{r.test}</td><td className="text-right"><Num v={r.cost} lang={lang} /></td><td className="text-right"><Num v={r.information_gain_bits} lang={lang} /></td><td className="text-right"><Num v={r.gain_per_cost} lang={lang} /></td><td className="text-center">{r.fully_separates ? "✓" : ""}</td></tr>)}</tbody></table>
            <p className="help">{choice.assumptions}</p>
          </div>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ effect */
function EffectTab() {
  const { t, lang, datasets, act } = useApp();
  const names = useMemo(() => datasets.map((d) => d.name), [datasets]);
  const [f, setF] = useState({ method: "did", dataset: "", outcome: "", time: "", intervention: "", group: "", treated: "", unit: "", confounders: "", treatment: "", placeboOutcome: "", season: "" });
  const cols = useColumns(f.dataset);
  const [res, setRes] = useState(null);
  const { busy, error, run } = useRunner();
  useEffect(() => { if (!f.dataset && names[0]) setF((x) => ({ ...x, dataset: names[0] })); }, [names]);
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }));
  const doRun = () => run("eff", async () => {
    setRes(await act(() => api.effectEstimate({ method: f.method, dataset: f.dataset, outcome: f.outcome, time: f.time || null, intervention: f.intervention === "" ? null : (Number.isNaN(Number(f.intervention)) ? f.intervention : Number(f.intervention)),
      group: f.group || null, treated_value: f.treated === "" ? null : f.treated, unit: f.unit || null, confounders: split(f.confounders), treatment: f.treatment || null,
      placebo_outcome: f.placeboOutcome || null, season_period: f.season ? Number(f.season) : null })));
  });
  const inp = (key, label, id, extra = {}) => <Field label={label} id={id}><input id={id} list="ex-ef-cols" className="field" value={f[key]} onChange={(e) => set(key, e.target.value)} {...extra} /></Field>;

  const spec = useMemo(() => {
    if (!res) return null;
    if (res.method === "did") {
      const cut = res.intervention_time;
      return { $schema: SCHEMA, width: "container", height: 240, data: { values: res.chart },
        layer: [{ mark: { type: "line", point: true }, encoding: { x: { field: "time", type: "quantitative", title: f.time }, y: { field: "mean", type: "quantitative", title: res.outcome }, color: { field: "group", type: "nominal" } } },
          { data: { values: [{ x: cut }] }, mark: { type: "rule", strokeDash: [4, 3] }, encoding: { x: { field: "x", type: "quantitative" } } }] };
    }
    if (res.method === "its") {
      const c = res.chart;
      const rows = c.time.map((tm, i) => ({ time: tm, observed: c.observed[i], fitted: c.fitted[i], cf: c.counterfactual[i], lo: c.counterfactual_lo[i], hi: c.counterfactual_hi[i] }));
      return { $schema: SCHEMA, width: "container", height: 240, data: { values: rows },
        layer: [{ mark: { type: "area", opacity: 0.15 }, encoding: { x: { field: "time", type: "quantitative", title: f.time }, y: { field: "lo", type: "quantitative", title: res.outcome }, y2: { field: "hi" } } },
          { mark: { type: "point", color: "#888" }, encoding: { x: { field: "time", type: "quantitative" }, y: { field: "observed", type: "quantitative" } } },
          { mark: { type: "line" }, encoding: { x: { field: "time", type: "quantitative" }, y: { field: "fitted", type: "quantitative" } } },
          { mark: { type: "line", strokeDash: [4, 3] }, encoding: { x: { field: "time", type: "quantitative" }, y: { field: "cf", type: "quantitative" } } },
          { data: { values: [{ x: res.intervention_time }] }, mark: { type: "rule", strokeDash: [2, 2] }, encoding: { x: { field: "x", type: "quantitative" } } }] };
    }
    return null;
  }, [res, lang]);

  const e = res?.effect;
  const tone = { passed: "ok", failed: "warn", not_testable: undefined };
  return (
    <div className="flex flex-col gap-6">
      {error && <div className="panel font-semibold" role="alert" data-testid="ex-error">{error}</div>}
      <div className="panel flex flex-col gap-3" data-testid="ex-effect-form">
        <h2 className="font-semibold">{t("ex_effect_title")}</h2>
        <p className="help">{t("ex_effect_help")}</p>
        <datalist id="ex-ef-cols">{cols.map((c) => <option key={c} value={c} />)}</datalist>
        <div className="flex flex-wrap items-end gap-3">
          <Field label={t("ex_ef_method")} id="ex-ef-method"><select id="ex-ef-method" className="field" value={f.method} onChange={(e2) => set("method", e2.target.value)}><option value="did">{t("ex_ef_did")}</option><option value="its">{t("ex_ef_its")}</option><option value="adjust">{t("ex_ef_adjust")}</option></select></Field>
          <DatasetPick id="ex-ef-ds" value={f.dataset} onChange={(v) => set("dataset", v)} names={names} t={t} />
          {inp("outcome", t("ex_outcome"), "ex-ef-outcome")}
          {f.method !== "adjust" && inp("time", t("ex_time_col"), "ex-ef-time")}
          {f.method !== "adjust" && <Field label={t("ex_intervention")} id="ex-ef-int"><input id="ex-ef-int" className="field" placeholder="2026-03-01" value={f.intervention} onChange={(e2) => set("intervention", e2.target.value)} /></Field>}
          {f.method === "did" && inp("group", t("ex_group"), "ex-ef-group")}
          {(f.method === "did" || f.method === "adjust") && <Field label={t("ex_treated_value")} id="ex-ef-tv"><input id="ex-ef-tv" className="field" style={{ width: 110 }} value={f.treated} onChange={(e2) => set("treated", e2.target.value)} /></Field>}
          {f.method === "did" && inp("unit", t("ex_unit"), "ex-ef-unit")}
          {f.method === "adjust" && inp("treatment", t("ex_treatment"), "ex-ef-treat")}
          {f.method === "adjust" && inp("placeboOutcome", t("ex_placebo_outcome"), "ex-ef-plo")}
          {f.method !== "its" && <Field label={t("ex_confounders")} id="ex-ef-conf"><input id="ex-ef-conf" list="ex-ef-cols" className="field" value={f.confounders} onChange={(e2) => set("confounders", e2.target.value)} /></Field>}
          {f.method === "its" && <Field label={t("ex_season")} id="ex-ef-season"><input id="ex-ef-season" className="field num" style={{ width: 80 }} type="number" min="2" value={f.season} onChange={(e2) => set("season", e2.target.value)} /></Field>}
          <button className="btn btn-primary" onClick={doRun} disabled={!f.dataset || !f.outcome || !!busy}>{busy === "eff" ? t("sc_working") : t("ex_estimate_btn")}</button>
        </div>
      </div>

      {res && (
        <div className="flex flex-col gap-4" data-testid="ex-effect-result">
          <div className="panel flex flex-col gap-1">
            <h2 className="font-semibold">{t("ex_effect_est")}: <Num v={e.estimate} lang={lang} digits={4} /> <span className="help"><Range lo={e.ci_low} hi={e.ci_high} lang={lang} /> {Math.round(e.level * 100)}%</span></h2>
            {e.definition && <p className="help">{e.definition}</p>}
            {e.naive_estimate !== undefined && <p className="text-[13px]">{t("ex_naive")}: <Num v={e.naive_estimate} lang={lang} /> → {t("ex_adjusted")}: <Num v={e.estimate} lang={lang} /></p>}
            {res.slope_change && <p className="text-[13px]">{t("ex_slope_change")}: <Num v={res.slope_change.estimate} lang={lang} digits={4} /> <Range lo={res.slope_change.ci_low} hi={res.slope_change.ci_high} lang={lang} /></p>}
            <p className="text-[13px]" data-testid="ex-causal"><Chip tone={res.causal_claim.assumptions_failed.length ? "warn" : undefined}>{res.causal_claim.assumptions_failed.length ? t("ex_assump_failed") : t("ex_not_causal")}</Chip> {res.causal_claim.text}</p>
          </div>
          <Warnings items={res.warnings} />
          <div className="panel flex flex-col gap-1" data-testid="ex-assumptions">
            <h3 className="font-medium">{t("ex_assumptions")}</h3>
            {res.assumptions.map((a) => (
              <p key={a.name} className="text-[13px]"><Chip tone={tone[a.status]}>{t("ex_st_" + a.status)}</Chip> <b>{a.name}</b>: {a.statement} <span className="help">{a.detail}</span></p>
            ))}
          </div>
          {spec && <div className="panel" data-testid="ex-effect-chart"><VegaChart spec={spec} height={240} /></div>}
          {res.stability && res.stability.length > 0 && (
            <div className="panel overflow-x-auto"><h3 className="font-medium mb-1">{t("ex_stability")}</h3>
              <table className="w-full text-[13px]"><thead><tr><th className="text-left">{t("ex_adjusted_for")}</th><th className="text-right">{t("ex_effect")}</th><th className="text-right">{t("sc_ci")}</th></tr></thead>
                <tbody>{res.stability.map((s, i) => <tr key={i}><td>{s.adjusted_for.join(", ") || "—"}</td><td className="text-right"><Num v={s.estimate} lang={lang} /></td><td className="text-right"><Range lo={s.ci_low} hi={s.ci_high} lang={lang} /></td></tr>)}</tbody></table>
              {res.sensitivity && <p className="help mt-2">{res.sensitivity.reading}</p>}
            </div>
          )}
          {res.balance && res.balance.length > 0 && (
            <div className="panel overflow-x-auto"><h3 className="font-medium mb-1">{t("ex_balance")}</h3>
              <table className="w-full text-[13px]"><thead><tr><th className="text-left">{t("ex_confounder")}</th><th className="text-right">{t("ex_std_diff")}</th></tr></thead>
                <tbody>{res.balance.map((b) => <tr key={b.confounder}><td>{b.confounder}</td><td className="text-right"><Num v={b.standardized_difference} lang={lang} /> {b.imbalanced ? "⚠" : ""}</td></tr>)}</tbody></table></div>
          )}
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ page */
const TABS = [["twin", "ex_tab_twin", TwinTab], ["design", "ex_tab_design", DesignTab], ["effect", "ex_tab_effect", EffectTab]];

export default function ExperimentsPage() {
  const { t } = useApp();
  const [tab, setTab] = useState("twin");
  const Current = TABS.find((x) => x[0] === tab)[2];
  return (
    <div className="flex flex-col gap-4" data-testid="experiments-page">
      <div>
        <h1 className="text-lg font-semibold">{t("ex_title")}</h1>
        <p className="help mt-1">{t("ex_intro")}</p>
      </div>
      <nav className="seg self-start flex-wrap" role="tablist" aria-label={t("ex_title")} data-testid="ex-tabs">
        {TABS.map(([id, key]) => <button key={id} type="button" aria-pressed={tab === id} onClick={() => setTab(id)} data-testid={`ex-tab-${id}`}>{t(key)}</button>)}
      </nav>
      <Current />
    </div>
  );
}
