import React, { useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Chip } from "../components/ui.jsx";

const EXAMPLE = {
  fdm: [
    { label: "job 1", parameters: { volume_cm3: 60, surface_cm2: 140, part_height_mm: 40 }, actuals: { print_time_h: 2.3, machine_time: 5.6, mat_geo: 1.2, setup_prep: 18 } },
    { label: "job 2", parameters: { volume_cm3: 150, surface_cm2: 260, part_height_mm: 70 }, actuals: { print_time_h: 5.4, machine_time: 13.1, mat_geo: 2.8, setup_prep: 17 } },
    { label: "job 3", parameters: { volume_cm3: 30, surface_cm2: 90, part_height_mm: 25 }, actuals: { print_time_h: 1.1, machine_time: 2.7, mat_geo: 0.6, setup_prep: 19 } },
    { label: "job 4", parameters: { volume_cm3: 220, surface_cm2: 330, part_height_mm: 95 }, actuals: { print_time_h: 8.2, machine_time: 19.8, mat_geo: 4.1, setup_prep: 18 } },
  ],
  cnc_laser: [
    { label: "job 1", parameters: { cut_length_mm: 6000, blank_area_cm2: 400 }, actuals: { cut_time_h: 0.09, machine_time: 5.0 } },
    { label: "job 2", parameters: { cut_length_mm: 14000, blank_area_cm2: 900 }, actuals: { cut_time_h: 0.2, machine_time: 10.3 } },
    { label: "job 3", parameters: { cut_length_mm: 3000, blank_area_cm2: 150 }, actuals: { cut_time_h: 0.05, machine_time: 2.4 } },
  ],
  concrete: [
    { label: "job 1", parameters: { volume_cm3: 800000, toolpath_length_m: 900, part_height_mm: 600, layer_height_mm: 15 }, actuals: { print_time_h: 6.3, machine_time: 410 } },
    { label: "job 2", parameters: { volume_cm3: 1500000, toolpath_length_m: 1800, part_height_mm: 900, layer_height_mm: 15 }, actuals: { print_time_h: 11.8, machine_time: 760 } },
    { label: "job 3", parameters: { volume_cm3: 300000, toolpath_length_m: 400, part_height_mm: 300, layer_height_mm: 15 }, actuals: { print_time_h: 2.9, machine_time: 190 } },
  ],
};

const tone = { error: "danger", warning: "warn", info: "ok" };

export function ToolpathPanel({ name, process, reload, setError, t, fmt }) {
  const { act } = useApp();
  const [text, setText] = useState("");
  const [label, setLabel] = useState("");
  const [kind, setKind] = useState("auto");
  const [unit, setUnit] = useState("mm");
  const [pre, setPre] = useState(null);
  const [busy, setBusy] = useState("");
  const file = useRef(null);

  const onFile = async (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setLabel(f.name); setPre(null);
    setText(await f.text());
  };
  const run = (key, fn) => async () => {
    setBusy(key); setError("");
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(""); }
  };
  const body = { content: text, kind, unit, label };
  const preview = run("pre", async () => setPre(await api.fabquoteToolpathPreview(body)));
  const apply = run("apply", async () => {
    const r = await act(() => api.fabquoteToolpath(name, body), t("fqp_tp_applied"));
    setPre(r); await reload();
  });
  const rd = pre?.reading;
  return (
    <div className="panel flex flex-col gap-3 overflow-x-auto" data-testid="fq-toolpath">
      <h2 className="font-semibold">{t("fqp_tp_title")}</h2>
      <p className="help">{t("fqp_tp_help")}</p>
      <div className="flex flex-wrap items-end gap-3">
        <div><label className="label" htmlFor="fq-tp-file">{t("fqp_tp_file")}</label>
          <input id="fq-tp-file" ref={file} className="field" type="file" accept=".gcode,.gco,.nc,.txt,.csv,.json" onChange={onFile} data-testid="fq-tp-file" /></div>
        <div><label className="label" htmlFor="fq-tp-kind">{t("fqp_tp_kind")}</label>
          <select id="fq-tp-kind" className="field" value={kind} onChange={(e) => setKind(e.target.value)}>
            {["auto", "gcode", "trajectory"].map((k) => <option key={k} value={k}>{t("fqp_tp_kind_" + k)}</option>)}</select></div>
        <div><label className="label" htmlFor="fq-tp-unit">{t("fqp_tp_unit")}</label>
          <select id="fq-tp-unit" className="field" value={unit} onChange={(e) => setUnit(e.target.value)}>
            {["mm", "cm", "m", "in"].map((u) => <option key={u}>{u}</option>)}</select></div>
      </div>
      <div><label className="label" htmlFor="fq-tp-text">{t("fqp_tp_text")}</label>
        <textarea id="fq-tp-text" className="field num" rows={5} style={{ width: "100%", maxWidth: 640 }} value={text}
          onChange={(e) => { setText(e.target.value); setPre(null); }} data-testid="fq-tp-text" /></div>
      <div className="flex gap-2">
        <button className="btn btn-sm" onClick={preview} disabled={!text || !!busy} data-testid="fq-tp-preview">{t("fqp_tp_preview")}</button>
        <button className="btn btn-sm btn-primary" onClick={apply} disabled={!text || !!busy} data-testid="fq-tp-apply">{t("fqp_tp_apply")}</button>
      </div>
      {rd && (
        <div className="flex flex-col gap-2" data-testid="fq-tp-reading">
          <h3 className="font-medium text-[13px]">{t("fqp_tp_reading")}{rd.slicer ? ` · ${rd.slicer}` : ""} · <span className="help">sha256 {rd.sha256.slice(0, 12)}…</span></h3>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[13px]">
            {rd.time_h != null && <span>{t("fqp_rd_time")}: <span className="num">{fmt(rd.time_h, 3)}</span></span>}
            {rd.format === "gcode" && <span>{t("fqp_rd_filament")}: <span className="num">{fmt(rd.filament_cm3, 3)}</span></span>}
            {rd.skin_cm3 != null && <span>{t("fqp_rd_skin")}: <span className="num">{fmt(rd.skin_cm3, 4)}</span></span>}
            {rd.support_cm3 != null && <span>{t("fqp_rd_support")}: <span className="num">{fmt(rd.support_cm3, 4)}</span></span>}
            {rd.layers != null && <span>{t("fqp_rd_layers")}: <span className="num">{rd.layers}</span></span>}
            {rd.part_height_mm != null && <span>{t("fqp_rd_height")}: <span className="num">{fmt(rd.part_height_mm, 2)}</span></span>}
            <span>{t("fqp_rd_path")}: <span className="num">{fmt(rd.path_length_m, 3)}</span></span>
          </div>
          {rd.features_cm3 && (
            <p className="help">{t("fqp_rd_features")}: {Object.entries(rd.features_cm3).map(([k, v]) => `${k} ${fmt(v, 4)}`).join(" · ")}</p>
          )}
          {rd.warnings?.length > 0 && <ul className="list-disc pl-5 text-[13px]">{rd.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
          {pre.parameters && (
            <p className="help" data-testid="fq-tp-fills">{t("fqp_tp_fills")}: {Object.entries(pre.parameters).map(([k, v]) => `${k} = ${fmt(v.value, 4)}`).join(" · ")}</p>
          )}
        </div>
      )}
    </div>
  );
}

export function InspectPanel({ name, quote, reload, setError, t }) {
  const { act } = useApp();
  const [form, setForm] = useState({ model_id: "", target: "fdm-print" });
  const [busy, setBusy] = useState(false);
  const insp = quote.inspection;
  const run = async () => {
    setBusy(true); setError("");
    try {
      await act(() => api.fabquoteInspect(name, { model_id: form.model_id, target: form.target || "fdm-print" }), t("fqp_in_done"));
      await reload();
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="panel flex flex-col gap-3 overflow-x-auto" data-testid="fq-inspect">
      <h2 className="font-semibold">{t("fqp_in_title")}</h2>
      <p className="help">{t("fqp_in_help")}</p>
      <div className="flex flex-wrap items-end gap-3">
        <div><label className="label" htmlFor="fq-in-id">{t("fqp_in_model")}</label>
          <input id="fq-in-id" className="field num" style={{ width: 120 }} value={form.model_id} onChange={(e) => setForm({ ...form, model_id: e.target.value })} /></div>
        <div><label className="label" htmlFor="fq-in-target">{t("fqp_in_target")}</label>
          <input id="fq-in-target" className="field" style={{ width: 180 }} value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })} /></div>
        <button className="btn btn-sm btn-primary" onClick={run} disabled={!form.model_id || busy} data-testid="fq-in-run">{t("fqp_in_run")}</button>
      </div>
      {insp && (
        <div className="flex flex-col gap-2" data-testid="fq-in-result">
          <p className="text-[13px]">{t("fqp_in_verdict")}: <strong>{insp.verdict}</strong> · {t("fqp_in_model")} {insp.model_id} · {insp.target} · <span className="help">{insp.at}</span></p>
          {insp.warnings.length > 0 && (
            <table className="w-full text-[13px]">
              <thead><tr><th className="px-2 text-left font-medium">{t("fqp_in_sev")}</th><th className="px-2 text-left font-medium">{t("fqp_in_code")}</th><th className="px-2 text-left font-medium">{t("fqp_in_msg")}</th><th className="px-2 text-left font-medium">{t("fqp_in_affects")}</th><th className="px-2 text-left font-medium">{t("fqp_in_fix")}</th></tr></thead>
              <tbody>{insp.warnings.map((w, i) => (
                <tr key={i} data-testid={"fq-in-w-" + i}>
                  <td className="px-2"><Chip tone={tone[w.severity]}>{t("fqp_sev_" + w.severity)}</Chip></td>
                  <td className="px-2"><code>{w.code}</code></td>
                  <td className="px-2">{w.message}{w.blocker && <div className="help">{w.blocker}</div>}</td>
                  <td className="px-2 help">{(w.affects || []).join(", ") || "—"}</td>
                  <td className="px-2">{w.suggestion || "—"}</td>
                </tr>))}</tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}

export function CalibratePanel({ name, quote, reload, setError, t, fmt }) {
  const { act } = useApp();
  const process = quote.process;
  const [jobs, setJobs] = useState("");
  const [fit, setFit] = useState("");
  const [level, setLevel] = useState(95);
  const [out, setOut] = useState(null);
  const [busy, setBusy] = useState("");

  const body = (apply) => {
    let parsed;
    try { parsed = JSON.parse(jobs); } catch { throw new Error(t("fqp_ca_bad_json")); }
    const names = fit.split(/[,;\s]+/).filter(Boolean);
    return { jobs: parsed, fit: names.length ? names : null, apply, level: Number(level) / 100 };
  };
  const run = (key, apply) => async () => {
    setBusy(key); setError("");
    try {
      const r = apply ? await act(() => api.fabquoteCalibrate(name, body(true)), t("fqp_ca_applied")) : await api.fabquoteCalibrate(name, body(false));
      setOut(r);
      if (apply) await reload();
    } catch (e) { setError(e.message); } finally { setBusy(""); }
  };
  const e = out?.error;
  return (
    <div className="panel flex flex-col gap-3 overflow-x-auto" data-testid="fq-calib">
      <h2 className="font-semibold">{t("fqp_ca_title")}</h2>
      <p className="help">{t("fqp_ca_help")}</p>
      <div><label className="label" htmlFor="fq-ca-jobs">{t("fqp_ca_jobs")}</label>
        <textarea id="fq-ca-jobs" className="field num" rows={8} style={{ width: "100%", maxWidth: 760 }} value={jobs}
          onChange={(ev) => setJobs(ev.target.value)} data-testid="fq-ca-jobs" /></div>
      <div className="flex flex-wrap items-end gap-3">
        <button className="btn btn-sm" onClick={() => setJobs(JSON.stringify(EXAMPLE[process] || EXAMPLE.fdm, null, 1))} data-testid="fq-ca-example">{t("fqp_ca_example")}</button>
        <div><label className="label" htmlFor="fq-ca-fit">{t("fqp_ca_fit")}</label>
          <input id="fq-ca-fit" className="field" style={{ width: 300 }} value={fit} onChange={(ev) => setFit(ev.target.value)} /></div>
        <div><label className="label" htmlFor="fq-ca-level">{t("fqp_ca_level")}</label>
          <input id="fq-ca-level" className="field num" type="number" style={{ width: 80 }} min="50" max="99.9" value={level} onChange={(ev) => setLevel(ev.target.value)} /></div>
        <button className="btn btn-sm" onClick={run("calc", false)} disabled={!jobs || !!busy} data-testid="fq-ca-run">{busy === "calc" ? t("fqp_busy") : t("fqp_ca_run")}</button>
        <button className="btn btn-sm btn-primary" onClick={run("apply", true)} disabled={!jobs || !!busy} data-testid="fq-ca-apply">{t("fqp_ca_apply")}</button>
      </div>
      {out && (
        <div className="flex flex-col gap-3" data-testid="fq-ca-result">
          <div className="flex flex-wrap items-center gap-4 text-[13px]">
            <span>{t("fqp_ca_mape")}: <span className="num">{fmt(e.before.mape_pct, 1)} %</span> → <strong className="num" data-testid="fq-ca-after">{fmt(e.after.mape_pct, 1)} %</strong></span>
            <Chip tone="ok">{t("fqp_ca_improve")}: {fmt(e.improvement_pct, 0)} %</Chip>
            {out.cross_validation && <span>{t("fqp_ca_cv")}: <span className="num">{fmt(out.cross_validation.mape_pct, 1)} %</span></span>}
            <span className="help">{out.jobs} · {out.observations} · {out.dof} gl</span>
            {out.applied && <Chip tone="ok">{t("fqp_ca_applied")}</Chip>}
          </div>
          <table className="w-full text-[13px]">
            <thead><tr><th className="px-2 text-left font-medium">{t("fqp_param")}</th><th className="px-2 text-right font-medium">{t("fqp_ca_before")}</th><th className="px-2 text-right font-medium">{t("fqp_ca_after")}</th><th className="px-2 text-right font-medium">{t("fqp_ca_se")}</th><th className="px-2 text-right font-medium">{t("fqp_ca_ci")}</th><th className="px-2 text-right font-medium">± %</th></tr></thead>
            <tbody>{out.parameters.map((p) => (
              <tr key={p.parameter} data-testid={"fq-ca-p-" + p.parameter}>
                <td className="px-2"><code>{p.parameter}</code> <span className="help">{p.unit}</span></td>
                <td className="px-2 text-right num">{fmt(p.before, 4)}</td>
                <td className="px-2 text-right num">{p.identifiable ? <strong>{fmt(p.after, 4)}</strong> : <span className="help">{t("fqp_ca_notid")}</span>}</td>
                <td className="px-2 text-right num">{fmt(p.std_error, 4)}</td>
                <td className="px-2 text-right num">{p.ci_low != null ? `${fmt(p.ci_low, 4)} – ${fmt(p.ci_high, 4)}` : "—"}</td>
                <td className="px-2 text-right num">{fmt(p.rel_uncertainty_pct, 1)}</td>
              </tr>))}</tbody>
          </table>
          <table className="w-full text-[13px]">
            <thead><tr><th className="px-2 text-left font-medium">{t("fqp_ca_job")}</th><th className="px-2 text-right font-medium">{t("fqp_ca_before")} %</th><th className="px-2 text-right font-medium">{t("fqp_ca_after")} %</th></tr></thead>
            <tbody>{e.per_job.map((j) => <tr key={j.job}><td className="px-2">{j.job}</td><td className="px-2 text-right num">{fmt(j.before_pct, 1)}</td><td className="px-2 text-right num">{fmt(j.after_pct, 1)}</td></tr>)}</tbody>
          </table>
          <p className="help">{Object.entries(e.per_target_before_pct).map(([k, v]) => `${k}: ${fmt(v, 1)} % → ${fmt(e.per_target_after_pct[k], 1)} %`).join(" · ")}</p>
          {out.warnings.length > 0 && <ul className="list-disc pl-5 text-[13px]">{out.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
          <p className="help">{out.method}</p>
          <p className="help">{out.caveat}</p>
        </div>
      )}
      {quote.calibrations?.length > 0 && (
        <p className="help" data-testid="fq-ca-history">{t("fqp_ca_history")}: {quote.calibrations.map((c) => `${c.at} (${c.jobs}: ${fmt(c.error.before_mape_pct, 1)} % → ${fmt(c.error.after_mape_pct, 1)} %)`).join("; ")}</p>
      )}
    </div>
  );
}
