import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Chip } from "../components/ui.jsx";
import { CalibratePanel, InspectPanel, ToolpathPanel } from "../components/FabQuoteTools.jsx";
import { formatNumber } from "../format.js";

const PROCESSES = ["fdm", "cnc_laser", "concrete"];
const SOURCES = ["user", "assumed", "measured", "rate card", "calibrated"];
const CATS = ["geometric_material", "process_material", "setup", "labour", "machine", "waste", "margin"];

function save(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export default function FabQuotePage() {
  const { t, lang, act } = useApp();
  const fmt = (v, d = 3) => (typeof v === "number" ? formatNumber(v, lang, { maximumFractionDigits: d }) : "—");
  const [saved, setSaved] = useState([]);
  const [quote, setQuote] = useState(null);
  const [name, setName] = useState("");
  const [form, setForm] = useState({ name: "", process: "fdm", volume_only: "coarse", vulcan_id: "", vulcan_url: "" });
  const [edits, setEdits] = useState({});
  const [diff, setDiff] = useState(null);
  const [scn, setScn] = useState({ name: "", text: "" });
  const [cmp, setCmp] = useState(null);
  const [sens, setSens] = useState(null);
  const [sensIn, setSensIn] = useState({ pct: 10, params: "" });
  const [acts, setActs] = useState(null);
  const [actIn, setActIn] = useState({ item: "", value: "", unit: "", ledger_ref: "" });
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const refreshSaved = () => api.fabquoteQuotes().then((r) => setSaved(r.quotes)).catch(() => {});
  useEffect(() => { refreshSaved(); }, []);

  const guard = (key, fn) => async () => {
    setBusy(key); setError("");
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(""); }
  };
  const load = async (n) => {
    if (!n) return;
    const q = await api.fabquoteGet(n);
    setName(n); setQuote(q); setEdits({}); setDiff(null); setCmp(null); setSens(null);
    setActs(await api.fabquoteActuals(n).catch(() => null));
  };
  const reload = async () => { await load(name); await refreshSaved(); };

  const doCreate = guard("create", async () => {
    const body = { name: form.name, process: form.process, volume_only: form.volume_only };
    if (form.vulcan_id) body.vulcan = { model_id: form.vulcan_id, base_url: form.vulcan_url || null };
    const q = await act(() => api.fabquoteCreate(body), t("fqp_created"));
    await refreshSaved(); await load(q.name);
  });
  const doDelete = guard("delete", async () => { await api.fabquoteDelete(name); setQuote(null); setName(""); await refreshSaved(); });
  const doApply = guard("apply", async () => {
    const parameters = {};
    for (const [k, v] of Object.entries(edits)) {
      const out = {};
      if (v.value !== undefined) out.value = v.value === "" ? null : Number(v.value);
      if (v.source) out.source = v.source;
      parameters[k] = out;
    }
    const r = await act(() => api.fabquoteSetParams(name, parameters), t("fqp_applied"));
    setDiff(r.diff);
    await load(name); setDiff(r.diff);
  });
  const parseOverrides = () => Object.fromEntries(scn.text.split("\n").map((l) => l.split("=")).filter((p) => p.length === 2 && p[0].trim()).map(([k, v]) => [k.trim(), Number(v)]));
  const doScenario = guard("scn", async () => { await api.fabquoteScenarioSave(name, scn.name, parseOverrides()); await reload(); setCmp(await api.fabquoteCompare(name)); });
  const doCompare = guard("cmp", async () => { setCmp(await api.fabquoteCompare(name)); });
  const doSens = guard("sens", async () => {
    const params = sensIn.params.split(/[,;\s]+/).filter(Boolean);
    setSens(await api.fabquoteSensitivity(name, { pct: Number(sensIn.pct), params: params.length ? params : null }));
  });
  const doActual = guard("act", async () => {
    await api.fabquoteActual(name, { item: actIn.item, value: Number(actIn.value), unit: actIn.unit || null, ledger_ref: actIn.ledger_ref || null });
    setActIn({ ...actIn, value: "", ledger_ref: "" }); setActs(await api.fabquoteActuals(name)); await refreshSaved();
  });
  const doExport = (format) => guard("export", async () => { save(await api.fabquoteExport(name, format), `${name}.${format === "markdown" ? "md" : format}`); })();

  const res = quote?.result;
  const rows = useMemo(() => (res ? [...res.derived, ...res.items] : []), [res]);
  const status = (r) => (r.status === "ok" ? <Chip tone={r.quality === "coarse" ? "warn" : "ok"}>{r.quality === "coarse" ? t("fqp_coarse") : t("fqp_ok")}</Chip> : <Chip tone="danger">{r.status === "error" ? t("fqp_error") : t("fqp_blocked")}</Chip>);
  const dirty = Object.keys(edits).length > 0;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">{t("fqp_title")}</h1>
        <p className="help mt-1">{t("fqp_intro")}</p>
      </div>
      {error && <div className="panel font-semibold" role="alert" data-testid="fq-error">{error}</div>}

      <div className="panel flex flex-col gap-3" data-testid="fq-create">
        <div className="flex flex-wrap items-end gap-3">
          <div><label className="label" htmlFor="fq-saved">{t("fqp_saved")}</label>
            <select id="fq-saved" className="field" value={name} onChange={(e) => load(e.target.value).catch((x) => setError(x.message))}>
              <option value="">—</option>{saved.map((q) => <option key={q.name}>{q.name}</option>)}</select></div>
          <div><label className="label" htmlFor="fq-name">{t("fqp_name")}</label>
            <input id="fq-name" className="field" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></div>
          <div><label className="label" htmlFor="fq-proc">{t("fqp_process")}</label>
            <select id="fq-proc" className="field" value={form.process} onChange={(e) => setForm({ ...form, process: e.target.value })}>
              {PROCESSES.map((p) => <option key={p} value={p}>{t("fqp_proc_" + p)}</option>)}</select></div>
          {form.process === "fdm" && <div><label className="label" htmlFor="fq-vo">{t("fqp_volume_only")}</label>
            <select id="fq-vo" className="field" value={form.volume_only} onChange={(e) => setForm({ ...form, volume_only: e.target.value })}>
              <option value="coarse">{t("fqp_vo_coarse")}</option><option value="block">{t("fqp_vo_block")}</option></select></div>}
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <div><label className="label" htmlFor="fq-vid">{t("fqp_vulcan_id")}</label>
            <input id="fq-vid" className="field" style={{ width: 140 }} value={form.vulcan_id} onChange={(e) => setForm({ ...form, vulcan_id: e.target.value })} /></div>
          <div><label className="label" htmlFor="fq-vurl">{t("fqp_vulcan_url")}</label>
            <input id="fq-vurl" className="field" style={{ width: 240 }} placeholder="http://127.0.0.1:5186" value={form.vulcan_url} onChange={(e) => setForm({ ...form, vulcan_url: e.target.value })} /></div>
          <button className="btn btn-primary" onClick={doCreate} disabled={!form.name || !!busy} data-testid="fq-create-btn">{busy === "create" ? t("fqp_busy") : t("fqp_create")}</button>
          {quote && <button className="btn" onClick={doDelete} disabled={!!busy}>{t("fqp_delete")}</button>}
        </div>
        <p className="help">{t("fqp_vulcan_help")}</p>
      </div>

      {!quote && <p className="help">{t("fqp_none")}</p>}
      {quote && res && (
        <>
          <div className="panel flex flex-wrap items-center gap-4" data-testid="fq-summary">
            <div><div className="help">{t("fqp_total")}</div>
              <div className="num text-2xl font-semibold" data-testid="fq-total">{fmt(res.total.value, 2)} {res.currency}</div></div>
            {res.total.partial && <Chip tone="danger">{t("fqp_partial")}</Chip>}
            <Chip tone={res.quality === "coarse" ? "warn" : "ok"}>{res.quality === "coarse" ? t("fqp_coarse") : t("fqp_exact")}</Chip>
            <div className="flex flex-wrap gap-3 text-[13px]">
              {CATS.map((c) => <span key={c}>{t("fqp_cat_" + c)}: <span className="num">{fmt(res.categories[c], 2)}</span></span>)}
            </div>
            <div className="flex gap-2 ml-auto">
              <span className="help self-center">{t("fqp_export")}</span>
              {["json", "csv", "markdown"].map((f) => <button key={f} className="btn btn-sm" onClick={() => doExport(f)} disabled={!!busy}>{f}</button>)}
            </div>
          </div>
          {quote.provenance?.vulcan && (
            <p className="help" data-testid="fq-prov">{t("fqp_provenance")}: id {quote.provenance.vulcan.model_id} · {quote.provenance.vulcan.fetched_at} · sha256 {quote.provenance.vulcan.response_sha256.slice(0, 12)}…</p>
          )}
          {quote.warnings.length > 0 && (
            <div className="panel" data-testid="fq-warnings"><h2 className="font-semibold">{t("fqp_warnings")}</h2>
              <ul className="list-disc pl-5 text-[13px]">{quote.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></div>
          )}

          <div className="panel flex flex-col gap-3 overflow-x-auto" data-testid="fq-params">
            <div className="flex items-center gap-3"><h2 className="font-semibold">{t("fqp_params")}</h2>
              <button className="btn btn-sm btn-primary" onClick={doApply} disabled={!dirty || !!busy} data-testid="fq-apply">{t("fqp_save_params")}</button></div>
            <table className="w-full text-[13px]">
              <thead><tr><th className="px-2 text-left font-medium">{t("fqp_param")}</th><th className="px-2 text-right font-medium">{t("fqp_value")}</th><th className="px-2 text-left font-medium">{t("fqp_unit")}</th><th className="px-2 text-left font-medium">{t("fqp_source")}</th><th className="px-2 text-left font-medium">{t("fqp_used_by")}</th></tr></thead>
              <tbody>{res.parameters.map((p) => {
                const e = edits[p.name] || {};
                return (
                  <tr key={p.name} data-testid={"fq-param-" + p.name}>
                    <td className="px-2"><code>{p.name}</code></td>
                    <td className="px-2 text-right"><input className="field num" style={{ width: 110 }} type="number" step="any" aria-label={p.name}
                      value={e.value !== undefined ? e.value : (p.value ?? "")} placeholder={p.missing ? t("fqp_missing") : ""}
                      onChange={(ev) => setEdits({ ...edits, [p.name]: { ...e, value: ev.target.value, source: e.source || (p.source === "assumed" || p.missing ? "user" : p.source) } })} /></td>
                    <td className="px-2">{p.unit}</td>
                    <td className="px-2"><select className="field" aria-label={p.name + " " + t("fqp_source")} value={e.source || p.source}
                      onChange={(ev) => setEdits({ ...edits, [p.name]: { ...e, source: ev.target.value } })}>{SOURCES.map((s) => <option key={s} value={s}>{t("fqp_src_" + s)}</option>)}</select></td>
                    <td className="px-2 help">{p.used_by.join(", ")}</td>
                  </tr>);
              })}</tbody>
            </table>
          </div>

          {diff && (
            <div className="panel flex flex-col gap-2 overflow-x-auto" data-testid="fq-diff">
              <h2 className="font-semibold">{t("fqp_changes")}</h2>
              <DiffTable diff={diff} t={t} fmt={fmt} />
            </div>
          )}

          <div className="panel flex flex-col gap-3 overflow-x-auto" data-testid="fq-items">
            <h2 className="font-semibold">{t("fqp_items")}</h2>
            <table className="w-full text-[13px]">
              <thead><tr><th className="px-2 text-left font-medium">{t("fqp_category")}</th><th className="px-2 text-left font-medium">{t("fqp_item")}</th><th className="px-2 text-right font-medium">{t("fqp_value")}</th><th className="px-2 text-left font-medium">{t("fqp_unit")}</th><th className="px-2 text-left font-medium">{t("fqp_status")}</th><th className="px-2 text-left font-medium">{t("fqp_formula")}</th><th className="px-2 text-left font-medium">{t("fqp_depends")}</th></tr></thead>
              <tbody>{rows.map((r) => (
                <tr key={r.id} data-testid={"fq-item-" + r.id}>
                  <td className="px-2">{t("fqp_cat_" + (r.category || "derived"))}</td>
                  <td className="px-2"><strong>{r.label}</strong> <code className="help">{r.id}</code></td>
                  <td className="px-2 text-right num">{fmt(r.value, r.kind === "item" ? 2 : 4)}</td>
                  <td className="px-2">{r.unit}</td>
                  <td className="px-2">{status(r)}{r.reason && <div className="help">{r.reason}</div>}{r.warnings.map((w, i) => <div key={i} className="help">{w}</div>)}</td>
                  <td className="px-2"><code>{r.formula}</code></td>
                  <td className="px-2 help">{r.deps.join(", ")}</td>
                </tr>))}</tbody>
            </table>
          </div>

          <div className="panel flex flex-col gap-3 overflow-x-auto" data-testid="fq-scenarios">
            <h2 className="font-semibold">{t("fqp_scenarios")}</h2>
            <div className="flex flex-wrap items-end gap-3">
              <div><label className="label" htmlFor="fq-sn">{t("fqp_scn_name")}</label><input id="fq-sn" className="field" value={scn.name} onChange={(e) => setScn({ ...scn, name: e.target.value })} /></div>
              <div><label className="label" htmlFor="fq-so">{t("fqp_scn_overrides")}</label>
                <textarea id="fq-so" className="field" rows={3} style={{ width: 300 }} placeholder="machine_rate_eur_h = 2" value={scn.text} onChange={(e) => setScn({ ...scn, text: e.target.value })} /></div>
              <button className="btn btn-sm" onClick={doScenario} disabled={!scn.name || !scn.text || !!busy} data-testid="fq-scn-save">{t("fqp_scn_save")}</button>
              <button className="btn btn-sm" onClick={doCompare} disabled={!quote.scenarios || !Object.keys(quote.scenarios).length || !!busy}>{t("fqp_scn_compare")}</button>
            </div>
            {cmp && (
              <>
                <table className="w-full text-[13px]" data-testid="fq-cmp">
                  <thead><tr><th className="px-2 text-left font-medium">{t("fqp_scenarios")}</th><th className="px-2 text-right font-medium">{t("fqp_total")}</th><th className="px-2 text-right font-medium">{t("fqp_delta")}</th></tr></thead>
                  <tbody>
                    <tr><td className="px-2">{t("fqp_scn_base")}</td><td className="px-2 text-right num">{fmt(cmp.base.total.value, 2)}</td><td /></tr>
                    {cmp.scenarios.map((s) => <tr key={s.scenario}><td className="px-2">{s.scenario}</td><td className="px-2 text-right num">{fmt(s.total.value, 2)}</td>
                      <td className="px-2 text-right num">{fmt(s.diff.total.delta, 2)} ({fmt(s.diff.total.delta_pct, 1)} %)</td></tr>)}
                  </tbody>
                </table>
                {cmp.scenarios.map((s) => <div key={s.scenario}><h3 className="font-medium text-[13px] mt-2">{s.scenario}</h3><DiffTable diff={s.diff} t={t} fmt={fmt} /></div>)}
              </>
            )}
          </div>

          <div className="panel flex flex-col gap-3 overflow-x-auto" data-testid="fq-sens">
            <h2 className="font-semibold">{t("fqp_sens")}</h2>
            <div className="flex flex-wrap items-end gap-3">
              <div><label className="label" htmlFor="fq-sp">{t("fqp_sens_pct")}</label><input id="fq-sp" className="field num" type="number" style={{ width: 80 }} value={sensIn.pct} onChange={(e) => setSensIn({ ...sensIn, pct: e.target.value })} /></div>
              <div><label className="label" htmlFor="fq-sl">{t("fqp_sens_params")}</label><input id="fq-sl" className="field" style={{ width: 300 }} value={sensIn.params} onChange={(e) => setSensIn({ ...sensIn, params: e.target.value })} /></div>
              <button className="btn btn-sm" onClick={doSens} disabled={!!busy} data-testid="fq-sens-run">{t("fqp_sens_run")}</button>
            </div>
            {sens && (
              <>
                <p className="help">{sens.method}</p>
                <table className="w-full text-[13px]">
                  <thead><tr><th className="px-2 text-left font-medium">{t("fqp_param")}</th><th className="px-2 text-right font-medium">{t("fqp_value")}</th><th className="px-2 text-right font-medium">{t("fqp_sens_low")}</th><th className="px-2 text-right font-medium">{t("fqp_sens_high")}</th><th className="px-2 text-right font-medium">{t("fqp_sens_swing")}</th></tr></thead>
                  <tbody>{sens.ranking.map((r) => (
                    <tr key={r.parameter}><td className="px-2"><code>{r.parameter}</code></td><td className="px-2 text-right num">{fmt(r.base_value)} {r.unit}</td>
                      <td className="px-2 text-right num">{fmt(r.low.total, 2)}</td><td className="px-2 text-right num">{fmt(r.high.total, 2)}</td>
                      <td className="px-2 text-right num">{fmt(r.swing, 2)}</td></tr>))}</tbody>
                </table>
              </>
            )}
          </div>

          <div className="panel flex flex-col gap-3 overflow-x-auto" data-testid="fq-actuals">
            <h2 className="font-semibold">{t("fqp_actuals")}</h2>
            <p className="help">{t("fqp_actuals_help")}</p>
            <div className="flex flex-wrap items-end gap-3">
              <div><label className="label" htmlFor="fq-ai">{t("fqp_act_item")}</label>
                <select id="fq-ai" className="field" value={actIn.item} onChange={(e) => setActIn({ ...actIn, item: e.target.value })}>
                  <option value="">—</option>{rows.map((r) => <option key={r.id} value={r.id}>{r.label} ({r.unit})</option>)}</select></div>
              <div><label className="label" htmlFor="fq-av">{t("fqp_act_value")}</label><input id="fq-av" className="field num" type="number" step="any" style={{ width: 110 }} value={actIn.value} onChange={(e) => setActIn({ ...actIn, value: e.target.value })} /></div>
              <div><label className="label" htmlFor="fq-au">{t("fqp_act_unit")}</label><input id="fq-au" className="field" style={{ width: 90 }} value={actIn.unit} onChange={(e) => setActIn({ ...actIn, unit: e.target.value })} /></div>
              <div><label className="label" htmlFor="fq-al">{t("fqp_act_ledger")}</label><input id="fq-al" className="field" style={{ width: 160 }} value={actIn.ledger_ref} onChange={(e) => setActIn({ ...actIn, ledger_ref: e.target.value })} /></div>
              <button className="btn btn-sm" onClick={doActual} disabled={!actIn.item || actIn.value === "" || !!busy} data-testid="fq-act-record">{t("fqp_act_record")}</button>
            </div>
            {acts && (
              <table className="w-full text-[13px]">
                <thead><tr><th className="px-2 text-left font-medium">{t("fqp_item")}</th><th className="px-2 text-right font-medium">{t("fqp_quoted")}</th><th className="px-2 text-right font-medium">{t("fqp_actual")}</th><th className="px-2 text-right font-medium">{t("fqp_delta")}</th><th className="px-2 text-right font-medium">{t("fqp_ratio")}</th></tr></thead>
                <tbody>{acts.rows.filter((r) => r.actual !== null).map((r) => (
                  <tr key={r.item}><td className="px-2"><code>{r.item}</code> {r.unit}{r.ledger_refs?.length ? <span className="help"> · {r.ledger_refs.join(", ")}</span> : null}</td>
                    <td className="px-2 text-right num">{fmt(r.quoted, 3)}</td><td className="px-2 text-right num">{fmt(r.actual, 3)}</td>
                    <td className="px-2 text-right num">{fmt(r.delta, 3)}</td><td className="px-2 text-right num">{fmt(r.ratio_actual_to_quote, 2)}</td></tr>))}</tbody>
              </table>
            )}
          </div>

          <CalibratePanel name={name} quote={quote} reload={reload} setError={setError} t={t} fmt={fmt} />
          <ToolpathPanel name={name} process={quote.process} reload={reload} setError={setError} t={t} fmt={fmt} />
          {quote.process === "fdm" && <InspectPanel name={name} quote={quote} reload={reload} setError={setError} t={t} />}
        </>
      )}
    </div>
  );
}

function DiffTable({ diff, t, fmt }) {
  return (
    <>
      <table className="w-full text-[13px]">
        <thead><tr><th className="px-2 text-left font-medium">{t("fqp_item")}</th><th className="px-2 text-right font-medium">{t("fqp_before")}</th><th className="px-2 text-right font-medium">{t("fqp_after")}</th><th className="px-2 text-left font-medium">{t("fqp_formula")}</th><th className="px-2 text-left font-medium">{t("fqp_why")}</th></tr></thead>
        <tbody>{diff.changes.map((c) => (
          <tr key={c.id}><td className="px-2"><code>{c.id}</code></td><td className="px-2 text-right num">{fmt(c.before, 3)}</td><td className="px-2 text-right num">{fmt(c.after, 3)}</td>
            <td className="px-2"><code>{c.formula}</code></td>
            <td className="px-2 help">{c.because.map((b) => `${b.parameter}: ${b.before ?? "—"} → ${b.after ?? "—"} (${b.source_after})`).join("; ")}</td></tr>))}</tbody>
      </table>
      <p className="help">{t("fqp_unchanged")}: {diff.unchanged.join(", ") || "—"}</p>
    </>
  );
}
