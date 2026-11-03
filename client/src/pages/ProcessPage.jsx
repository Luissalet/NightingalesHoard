import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Chip } from "../components/ui.jsx";
import { formatNumber } from "../format.js";
import { CalibrationPanel, ProfilePanel, downloadJson } from "../components/EventLogPanels.jsx";

const SLOTS = ["case", "activity", "timestamp", "start", "lifecycle", "resource", "cost"];
const REQUIRED = new Set(["case", "activity", "timestamp"]);
const TABS = ["map", "profile", "variants", "bottlenecks", "conformance", "rehearsal", "calibration"];
const list = (txt) => txt.split(",").map((x) => x.trim()).filter(Boolean);
const CHANGES = ["add_resource", "scale_duration", "remove_step", "set_routing"];

function N({ v, lang, digits = 2 }) {
  return <span className="num">{typeof v === "number" ? formatNumber(v, lang, { maximumFractionDigits: digits }) : "—"}</span>;
}
const pct = (v, lang) => (typeof v === "number" ? `${formatNumber(v * 100, lang, { maximumFractionDigits: 1 })}%` : "—");

/** Layered layout: activities are ranked by their mean position in the traces; edges that go backwards are loops. */
function layout(res) {
  const acts = res.activities.map((a) => a.activity);
  const pos = {}; const wt = {};
  res.variants.forEach((v) => v.activities.forEach((a, i) => { pos[a] = (pos[a] || 0) + i * v.count; wt[a] = (wt[a] || 0) + v.count; }));
  acts.forEach((a) => { pos[a] = wt[a] ? pos[a] / wt[a] : 0; });
  const order = [...acts].sort((a, b) => pos[a] - pos[b]);
  const rank = Object.fromEntries(order.map((a, i) => [a, i]));
  const kept = res.graph.edges.filter((e) => e.kept);
  const level = Object.fromEntries(acts.map((a) => [a, 0]));
  for (let pass = 0; pass < acts.length; pass += 1) {
    let changed = false;
    kept.forEach((e) => { if (e.from !== e.to && rank[e.from] < rank[e.to] && level[e.to] < level[e.from] + 1) { level[e.to] = level[e.from] + 1; changed = true; } });
    if (!changed) break;
  }
  const cols = {};
  order.forEach((a) => { (cols[level[a]] = cols[level[a]] || []).push(a); });
  const W = 150; const H = 64; const GX = 70; const GY = 34;
  const maxLevel = Math.max(0, ...Object.keys(cols).map(Number));
  const maxRows = Math.max(1, ...Object.values(cols).map((c) => c.length));
  const nodes = {};
  Object.entries(cols).forEach(([lv, list]) => {
    list.forEach((a, i) => { nodes[a] = { x: 60 + Number(lv) * (W + GX) + 70, y: 60 + (i + (maxRows - list.length) / 2) * (H + GY) + 30, w: W, h: H }; });
  });
  const width = 60 + (maxLevel + 1) * (W + GX) + 140;
  const height = 60 + maxRows * (H + GY) + 60;
  return { nodes, width, height, rank, kept };
}

function ProcessMap({ res, mode, lang, t }) {
  const L = useMemo(() => layout(res), [res]);
  const maxC = Math.max(1, ...L.kept.map((e) => e.count));
  const act = Object.fromEntries(res.activities.map((a) => [a.activity, a]));
  const startY = 30; const endY = L.height - 20;
  const edgePath = (e) => {
    const a = L.nodes[e.from]; const b = L.nodes[e.to];
    if (e.from === e.to) return { d: `M ${a.x - 20} ${a.y - a.h / 2} c -10 -34 50 -34 40 0`, lx: a.x, ly: a.y - a.h / 2 - 26 };
    const back = L.rank[e.from] > L.rank[e.to];
    if (!back) {
      const x1 = a.x + a.w / 2; const y1 = a.y; const x2 = b.x - b.w / 2; const y2 = b.y;
      const same = Math.abs(x2 - x1) < 5;
      if (same) return { d: `M ${a.x} ${a.y + a.h / 2} L ${b.x} ${b.y - b.h / 2}`, lx: a.x + 6, ly: (a.y + b.y) / 2 };
      const mx = (x1 + x2) / 2;
      return { d: `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`, lx: mx, ly: (y1 + y2) / 2 - 6 };
    }
    const off = 46 + Math.abs(L.rank[e.from] - L.rank[e.to]) * 4;
    const x1 = a.x; const y1 = a.y + a.h / 2; const x2 = b.x; const y2 = b.y + b.h / 2;
    const ym = Math.max(y1, y2) + off;
    return { d: `M ${x1} ${y1} C ${x1} ${ym}, ${x2} ${ym}, ${x2} ${y2}`, lx: (x1 + x2) / 2, ly: ym - 4 };
  };
  const label = (e) => (mode === "freq" ? String(e.count) : (typeof e.wait_median_hours === "number" ? `${formatNumber(e.wait_median_hours, lang, { maximumFractionDigits: 2 })} h` : "—"));
  const first = res.start_activities.filter((s) => L.nodes[s.activity]);
  const last = res.end_activities.filter((s) => L.nodes[s.activity]);
  return (
    <div className="panel overflow-x-auto" data-testid="pr-map">
      <svg width={L.width} height={L.height} role="img" aria-label={t("pr_map")} style={{ maxWidth: "none" }}>
        <defs>
          <marker id="pr-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="var(--muted)" /></marker>
        </defs>
        <circle cx={40} cy={startY} r={9} fill="var(--accent)" /><text x={56} y={startY + 4} fontSize="11" fill="var(--muted)">{t("pr_start")}</text>
        <rect x={32} y={endY - 8} width={16} height={16} rx={3} fill="var(--muted)" /><text x={56} y={endY + 4} fontSize="11" fill="var(--muted)">{t("pr_end")}</text>
        {first.map((s) => { const n = L.nodes[s.activity]; return <path key={"s" + s.activity} d={`M 40 ${startY + 9} C 40 ${n.y - n.h / 2 - 20}, ${n.x} ${startY + 20}, ${n.x} ${n.y - n.h / 2}`} fill="none" stroke="var(--muted)" strokeWidth={1} markerEnd="url(#pr-arrow)" opacity={0.55} />; })}
        {last.map((s) => { const n = L.nodes[s.activity]; return <path key={"e" + s.activity} d={`M ${n.x} ${n.y + n.h / 2} C ${n.x} ${endY - 30}, 40 ${endY - 40}, 40 ${endY - 8}`} fill="none" stroke="var(--muted)" strokeWidth={1} markerEnd="url(#pr-arrow)" opacity={0.55} />; })}
        {L.kept.map((e) => {
          const p = edgePath(e);
          return (
            <g key={e.from + ">" + e.to} data-testid={`pr-edge-${e.from}-${e.to}`}>
              <path d={p.d} fill="none" stroke="var(--muted)" strokeWidth={1 + 5 * (e.count / maxC)} opacity={0.6} markerEnd="url(#pr-arrow)" />
              <text x={p.lx} y={p.ly} fontSize="11" textAnchor="middle" fill="var(--ink)" stroke="var(--white)" strokeWidth={3} paintOrder="stroke">{label(e)}</text>
            </g>
          );
        })}
        {Object.entries(L.nodes).map(([name, n]) => {
          const a = act[name];
          const hot = a.wait_share >= 0.3;
          return (
            <g key={name} data-testid={`pr-node-${name}`}>
              <rect x={n.x - n.w / 2} y={n.y - n.h / 2} width={n.w} height={n.h} rx={8} fill={hot ? "var(--warn-bg)" : "var(--soft)"} stroke={hot ? "var(--warn-ink)" : "var(--line)"} strokeWidth={hot ? 2 : 1} />
              <text x={n.x} y={n.y - 8} fontSize="13" fontWeight="600" textAnchor="middle" fill="var(--ink)">{name.length > 20 ? name.slice(0, 19) + "…" : name}</text>
              <text x={n.x} y={n.y + 8} fontSize="11" textAnchor="middle" fill="var(--muted)">{mode === "freq" ? `${a.count} ${t("pr_events")}` : `${t("pr_wait")} ${formatNumber(a.wait_median_hours ?? 0, lang, { maximumFractionDigits: 2 })} h`}</text>
              <text x={n.x} y={n.y + 22} fontSize="10" textAnchor="middle" fill="var(--muted)">{a.resources ? `${t("pr_resources_lc")}: ${a.resources}` : ""}{a.repeat_cases ? ` · ↻ ${a.repeat_cases}` : ""}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export default function ProcessPage() {
  const { t, lang, datasets, act } = useApp();
  const names = useMemo(() => datasets.map((d) => d.name), [datasets]);
  const [dataset, setDataset] = useState("");
  const [cols, setCols] = useState([]);
  const [mapping, setMapping] = useState({});
  const [warn, setWarn] = useState([]);
  const [threshold, setThreshold] = useState(0.5);
  const [minCount, setMinCount] = useState(1);
  const [prefix, setPrefix] = useState("");
  const [res, setRes] = useState(null);
  const [tab, setTab] = useState("map");
  const [mode, setMode] = useState("freq");
  const [refText, setRefText] = useState('{\n  "steps": []\n}');
  const [conf, setConf] = useState(null);
  const [confSave, setConfSave] = useState("");
  const [variants, setVariants] = useState([{ name: "Variant 1", changes: [{ type: "add_resource", activity: "", count: 1, factor: 0.8, from: "", to: "", share: 0.1 }] }]);
  const [reps, setReps] = useState(20);
  const [rehSave, setRehSave] = useState("");
  const [modelName, setModelName] = useState("");
  const [reh, setReh] = useState(null);
  const [tz, setTz] = useState("UTC");
  const [ends, setEnds] = useState("");
  const [expected, setExpected] = useState("");
  const [prof, setProf] = useState(null);
  const [profErr, setProfErr] = useState("");
  const [holdout, setHoldout] = useState(0.3);
  const [seed, setSeed] = useState(1);
  const [calModel, setCalModel] = useState("");
  const [cal, setCal] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => { if (!dataset && names[0]) setDataset(names[0]); }, [names]);
  useEffect(() => {
    if (!dataset) return;
    setRes(null); setConf(null); setReh(null); setError(""); setProf(null); setCal(null); setProfErr("");
    api.processSuggest(dataset).then((s) => { setCols(s.columns.map((c) => c.name)); setMapping(s.mapping); setWarn(s.warnings); }).catch((e) => setError(e.message));
  }, [dataset]);

  const guard = (key, fn) => async () => {
    setBusy(key); setError("");
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(""); }
  };
  const cleanMap = () => Object.fromEntries(Object.entries(mapping).filter(([, v]) => v));
  const mapOk = REQUIRED.size === [...REQUIRED].filter((k) => mapping[k]).length;
  const acts = res ? res.activities.map((a) => a.activity) : [];
  const logOptions = () => ({ timezone: tz || "UTC", end_activities: list(ends), expected: list(expected) });
  const logBody = () => ({ dataset, mapping: cleanMap(), log_options: logOptions() });
  const vsBody = () => variants.filter((v) => v.changes.length).map((v) => ({ name: v.name, changes: v.changes.map(changeBody) }));

  const doDiscover = guard("discover", async () => {
    const r = await act(() => api.processDiscover({ dataset, mapping: cleanMap(), threshold: Number(threshold), min_count: Number(minCount), save_as: prefix || null }), prefix ? t("pr_saved") : null);
    setRes(r); setConf(null); setReh(null); setCal(null); setTab("map");
    const first = r.activities.map((a) => a.activity);
    setVariants((vs) => vs.map((v) => ({ ...v, changes: v.changes.map((c) => ({ ...c, activity: c.activity || first[0] || "", from: c.from || first[0] || "", to: c.to || first[1] || "" })) })));
    setProf(null); setProfErr("");
    api.processProfile({ ...logBody(), threshold: Number(threshold), min_count: Number(minCount) }).then(setProf).catch((e) => setProfErr(e.message));
    if (r.activities.length && refText.includes('"steps": []')) {
      setRefText(JSON.stringify({ steps: r.variants[0].activities.filter((a, i, arr) => arr.indexOf(a) === i) }, null, 2));
    }
  });
  const doConform = guard("conform", async () => {
    let reference;
    try { reference = JSON.parse(refText); } catch (e) { throw new Error(`${t("pr_ref_json")}: ${e.message}`); }
    setConf(await act(() => api.processConform({ dataset, mapping: cleanMap(), reference, save_as: confSave || null }), confSave ? t("pr_saved") : null));
  });
  const changeBody = (c) => {
    if (c.type === "add_resource") return { type: c.type, activity: c.activity, count: Number(c.count) };
    if (c.type === "scale_duration") return { type: c.type, activity: c.activity, factor: Number(c.factor) };
    if (c.type === "remove_step") return { type: c.type, activity: c.activity };
    return { type: c.type, from: c.from, to: c.to, share: Number(c.share) };
  };
  const doRehearse = guard("rehearse", async () => {
    const vs = variants.filter((v) => v.changes.length).map((v) => ({ name: v.name, changes: v.changes.map(changeBody) }));
    setReh(await act(() => api.processRehearse({ dataset, mapping: cleanMap(), variants: vs, replications: Number(reps), save_as: rehSave || null, model_name: modelName || null }), rehSave ? t("pr_saved") : null));
  });
  const doCalibrate = guard("calibrate", async () => {
    setCal(await act(() => api.processCalibrate({ ...logBody(), holdout: Number(holdout), replications: Number(reps), seed: Number(seed), variants: vsBody(), model_name: calModel || null }), calModel ? t("pr_model_saved") : null));
  });
  const doExport = (withCal) => guard("export", async () => {
    const r = await api.processExport({ ...logBody(), with_calibration: withCal, holdout: Number(holdout), replications: Number(reps), seed: Number(seed), variants: withCal ? vsBody() : [] });
    downloadJson(r.recipe, r.filename);
  })();
  const setChange = (vi, ci, patch) => setVariants(variants.map((v, i) => (i !== vi ? v : { ...v, changes: v.changes.map((c, j) => (j !== ci ? c : { ...c, ...patch })) })));
  const blankChange = () => ({ type: "add_resource", activity: acts[0] || "", count: 1, factor: 0.8, from: acts[0] || "", to: acts[1] || "", share: 0.1 });

  const variantEditor = (
    <>
      {variants.map((v, vi) => (
        <div key={vi} className="flex flex-col gap-2 rounded-md border p-3" style={{ borderColor: "var(--line)" }}>
          <div className="flex items-center gap-2">
            <input className="field" aria-label={t("pr_variant_name")} value={v.name} onChange={(e) => setVariants(variants.map((x, i) => (i === vi ? { ...x, name: e.target.value } : x)))} />
            <button className="btn btn-sm" aria-label={t("pr_remove")} onClick={() => setVariants(variants.filter((_, i) => i !== vi))}>✕</button>
          </div>
          {v.changes.map((c, ci) => (
            <div key={ci} className="flex flex-wrap items-center gap-2">
              <select className="field" style={{ width: "auto" }} aria-label={t("pr_change")} value={c.type} onChange={(e) => setChange(vi, ci, { type: e.target.value })}>{CHANGES.map((k) => <option key={k} value={k}>{t("pr_ch_" + k)}</option>)}</select>
              {c.type !== "set_routing" && <select className="field" style={{ width: "auto" }} aria-label={t("pr_activity")} value={c.activity} onChange={(e) => setChange(vi, ci, { activity: e.target.value })}>{acts.map((a) => <option key={a}>{a}</option>)}</select>}
              {c.type === "add_resource" && <label className="text-[13px]">{t("pr_how_many")}<input className="field num" style={{ width: 70 }} type="number" min="1" value={c.count} onChange={(e) => setChange(vi, ci, { count: e.target.value })} /></label>}
              {c.type === "scale_duration" && <label className="text-[13px]">{t("pr_factor")}<input className="field num" style={{ width: 80 }} type="number" min="0.05" step="0.05" value={c.factor} onChange={(e) => setChange(vi, ci, { factor: e.target.value })} /></label>}
              {c.type === "set_routing" && <>
                <select className="field" style={{ width: "auto" }} aria-label={t("pr_from")} value={c.from} onChange={(e) => setChange(vi, ci, { from: e.target.value })}>{acts.map((a) => <option key={a}>{a}</option>)}</select>
                <span className="help">→</span>
                <select className="field" style={{ width: "auto" }} aria-label={t("pr_to")} value={c.to} onChange={(e) => setChange(vi, ci, { to: e.target.value })}>{acts.map((a) => <option key={a}>{a}</option>)}</select>
                <label className="text-[13px]">{t("pr_new_share")}<input className="field num" style={{ width: 80 }} type="number" min="0" max="1" step="0.05" value={c.share} onChange={(e) => setChange(vi, ci, { share: e.target.value })} /></label>
              </>}
              <button className="btn btn-sm" aria-label={t("pr_remove")} onClick={() => setVariants(variants.map((x, i) => (i !== vi ? x : { ...x, changes: x.changes.filter((_, j) => j !== ci) })))}>✕</button>
            </div>
          ))}
          <button className="btn btn-sm self-start" onClick={() => setVariants(variants.map((x, i) => (i === vi ? { ...x, changes: [...x.changes, blankChange()] } : x)))}>{t("pr_add_change")}</button>
        </div>
      ))}
    </>
  );

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">{t("pr_title")}</h1>
        <p className="help mt-1">{t("pr_intro")}</p>
      </div>
      {error && <div className="panel font-semibold" role="alert" data-testid="pr-error">{error}</div>}

      <div className="panel flex flex-col gap-3" data-testid="pr-source">
        <h2 className="font-semibold">{t("pr_log")}</h2>
        <div className="flex flex-wrap items-end gap-3">
          <div><label className="label" htmlFor="pr-ds">{t("pr_dataset")}</label>
            <select id="pr-ds" className="field" value={dataset} onChange={(e) => setDataset(e.target.value)}>{names.map((n) => <option key={n}>{n}</option>)}</select></div>
          {SLOTS.map((k) => (
            <div key={k}><label className="label" htmlFor={"pr-m-" + k}>{t("pr_col_" + k)}{REQUIRED.has(k) ? " *" : ""}</label>
              <select id={"pr-m-" + k} className="field" value={mapping[k] || ""} onChange={(e) => setMapping({ ...mapping, [k]: e.target.value })}>
                <option value="">—</option>{cols.map((c) => <option key={c}>{c}</option>)}</select></div>
          ))}
        </div>
        <p className="help">{t("pr_mapping_help")}</p>
        <div className="flex flex-wrap items-end gap-3" data-testid="pr-log-options">
          <div><label className="label" htmlFor="pr-tz">{t("el_timezone")}</label><input id="pr-tz" className="field mono" style={{ width: 150 }} value={tz} onChange={(e) => setTz(e.target.value)} /></div>
          <div><label className="label" htmlFor="pr-ends">{t("el_end_activities")}</label><input id="pr-ends" className="field" style={{ width: 220 }} value={ends} placeholder={t("el_list_ph")} onChange={(e) => setEnds(e.target.value)} /></div>
          <div className="grow"><label className="label" htmlFor="pr-expected">{t("el_expected")}</label><input id="pr-expected" className="field" value={expected} placeholder={t("el_list_ph")} onChange={(e) => setExpected(e.target.value)} /></div>
        </div>
        <p className="help">{t("el_options_help")}</p>
        {warn.map((w, i) => <p key={i} className="font-semibold">{w}</p>)}
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-[13px]">{t("pr_threshold")}<input className="field num" style={{ width: 80 }} type="number" min="0" max="1" step="0.05" value={threshold} onChange={(e) => setThreshold(e.target.value)} /></label>
          <label className="text-[13px]">{t("pr_min_count")}<input className="field num" style={{ width: 80 }} type="number" min="1" value={minCount} onChange={(e) => setMinCount(e.target.value)} /></label>
          <label className="text-[13px]">{t("pr_save_prefix")}<input className="field" value={prefix} onChange={(e) => setPrefix(e.target.value)} /></label>
          <button className="btn btn-primary" onClick={doDiscover} disabled={!dataset || !mapOk || !!busy} data-testid="pr-discover">{busy === "discover" ? t("pr_working") : t("pr_discover")}</button>
        </div>
      </div>

      {res && (
        <>
          <div className="panel flex flex-col gap-2" data-testid="pr-summary">
            <p className="text-[13px]">
              <b className="num">{res.cases}</b> {t("pr_cases")} · <b className="num">{res.events}</b> {t("pr_events")} · <b className="num">{res.activities_count}</b> {t("pr_activities")} · <b className="num">{res.variants_count}</b> {t("pr_variants_lc")}
              {" · "}{t("pr_tt")}: <N v={res.throughput.median} lang={lang} /> h ({t("pr_median")}), <N v={res.throughput.mean} lang={lang} /> h ({t("pr_mean")}), <N v={res.throughput.p90} lang={lang} /> h (p90)
            </p>
            {res.bottleneck_verdict && <p className="text-[13px]"><Chip tone="warn">{t("pr_bottleneck")}</Chip> {res.bottleneck_verdict}</p>}
            {res.warnings.map((w, i) => <p key={i} className="help">{w}</p>)}
            {res.saved_datasets && <p className="text-[13px]">{t("pr_saved")}: {Object.values(res.saved_datasets).map((n) => <a key={n} className="mono mr-2" href={`#/datasets/${encodeURIComponent(n)}`}>{n}</a>)}</p>}
          </div>
          <div className="flex flex-wrap gap-2" role="tablist">
            {TABS.map((k) => <button key={k} role="tab" aria-selected={tab === k} className={"btn btn-sm " + (tab === k ? "btn-primary" : "")} onClick={() => setTab(k)} data-testid={"pr-tab-" + k}>{t("pr_tab_" + k)}</button>)}
          </div>

          {tab === "map" && (
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-3">
                <div className="flex gap-1" role="group" aria-label={t("pr_show")}>
                  <button className={"btn btn-sm " + (mode === "freq" ? "btn-primary" : "")} onClick={() => setMode("freq")} data-testid="pr-mode-freq">{t("pr_freq")}</button>
                  <button className={"btn btn-sm " + (mode === "dur" ? "btn-primary" : "")} onClick={() => setMode("dur")} data-testid="pr-mode-dur">{t("pr_dur")}</button>
                </div>
                <span className="help">{t("pr_map_help")} {res.graph.kept_edges}/{res.graph.edges.length} {t("pr_edges_kept")}</span>
              </div>
              <ProcessMap res={res} mode={mode} lang={lang} t={t} />
              <div className="panel overflow-x-auto"><table className="pr-t w-full text-[13px]" data-testid="pr-edges">
                <thead><tr><th className="text-left">{t("pr_from")}</th><th className="text-left">{t("pr_to")}</th><th className="text-right">{t("pr_count")}</th><th className="text-right">{t("pr_dep")}</th><th className="text-right">{t("pr_wait_median")}</th><th className="text-right">{t("pr_wait_p90")}</th><th className="text-left">{t("pr_kept")}</th></tr></thead>
                <tbody>{res.graph.edges.map((e) => (
                  <tr key={e.from + ">" + e.to} className={e.kept ? "" : "help"}><td>{e.from}</td><td>{e.to}</td><td className="text-right"><N v={e.count} lang={lang} digits={0} /></td><td className="text-right"><N v={e.dependency} lang={lang} /></td><td className="text-right"><N v={e.wait_median_hours} lang={lang} /></td><td className="text-right"><N v={e.wait_p90_hours} lang={lang} /></td><td>{e.kept ? "✓" : "—"}</td></tr>
                ))}</tbody></table></div>
              <div className="panel" data-testid="pr-rework">
                <h2 className="font-semibold mb-1">{t("pr_rework")}</h2>
                {res.rework.loops.length === 0 ? <p className="help">{t("pr_no_rework")}</p> : (
                  <ul className="text-[13px] list-disc pl-5">{res.rework.loops.map((l) => <li key={l.type + l.activities.join()}>{l.activities.join(" → ")} · {l.cases} {t("pr_cases")} ({pct(l.share_of_cases, lang)}) · {l.occurrences}×</li>)}</ul>
                )}
              </div>
            </div>
          )}

          {tab === "profile" && (
            prof ? <ProfilePanel prof={prof} lang={lang} t={t} busy={busy} onExport={() => doExport(false)} />
              : <div className="panel" data-testid="pr-profile-pending">{profErr ? <p className="font-semibold" role="alert">{profErr}</p> : <p className="help">{t("pr_working")}</p>}</div>
          )}

          {tab === "calibration" && (
            <div className="flex flex-col gap-4">
              <div className="panel flex flex-col gap-3" data-testid="pr-calibrate">
                <h2 className="font-semibold">{t("el_calibrate")}</h2>
                <p className="help">{t("el_calibrate_help")}</p>
                {variantEditor}
                <div className="flex flex-wrap items-end gap-3">
                  <button className="btn btn-sm" onClick={() => setVariants([...variants, { name: `Variant ${variants.length + 1}`, changes: [blankChange()] }])}>{t("pr_add_variant")}</button>
                  <label className="text-[13px]">{t("el_holdout")}<input className="field num" style={{ width: 80 }} type="number" min="0.05" max="0.8" step="0.05" value={holdout} onChange={(e) => setHoldout(e.target.value)} data-testid="pr-holdout" /></label>
                  <label className="text-[13px]">{t("pr_reps")}<input className="field num" style={{ width: 80 }} type="number" min="1" max="100" value={reps} onChange={(e) => setReps(e.target.value)} /></label>
                  <label className="text-[13px]">{t("el_seed")}<input className="field num" style={{ width: 80 }} type="number" value={seed} onChange={(e) => setSeed(e.target.value)} /></label>
                  <label className="text-[13px]">{t("pr_model_name")}<input className="field" value={calModel} onChange={(e) => setCalModel(e.target.value)} /></label>
                  <button className="btn btn-primary" onClick={doCalibrate} disabled={!!busy} data-testid="pr-run-calibrate">{busy === "calibrate" ? t("pr_working") : t("el_run_calibrate")}</button>
                  {cal && <button className="btn btn-sm" onClick={() => doExport(true)} disabled={!!busy} data-testid="pr-export-cal">{t("el_export_cal")}</button>}
                </div>
              </div>
              {cal && <CalibrationPanel cal={cal} lang={lang} t={t} />}
            </div>
          )}

          {tab === "variants" && (
            <div className="panel overflow-x-auto" data-testid="pr-variants">
              <table className="pr-t w-full text-[13px]">
                <thead><tr><th className="text-right">#</th><th className="text-left">{t("pr_trace")}</th><th className="text-right">{t("pr_count")}</th><th className="text-right">{t("pr_share")}</th><th className="text-right">{t("pr_cum")}</th><th className="text-right">{t("pr_tt_mean")}</th></tr></thead>
                <tbody>{res.variants.map((v) => (
                  <tr key={v.id}><td className="text-right">{v.id}</td><td>{v.activities.join(" → ")}</td><td className="text-right"><N v={v.count} lang={lang} digits={0} /></td><td className="text-right">{pct(v.share, lang)}</td><td className="text-right">{pct(v.cumulative_share, lang)}</td><td className="text-right"><N v={v.throughput_mean_hours} lang={lang} /></td></tr>
                ))}</tbody>
              </table>
              <p className="help mt-2">{res.variants_shown}/{res.variants_count} {t("pr_variants_lc")}</p>
            </div>
          )}

          {tab === "bottlenecks" && (
            <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))" }}>
              <div className="panel overflow-x-auto" data-testid="pr-bottlenecks">
                <h2 className="font-semibold mb-1">{t("pr_bottlenecks")}</h2>
                <p className="help mb-2">{res.wait_includes_work ? t("pr_wait_incl_work") : t("pr_wait_help")}</p>
                <table className="pr-t w-full text-[13px]">
                  <thead><tr><th className="text-left">{t("pr_activity")}</th><th className="text-right">{t("pr_wait_share")}</th><th className="text-right">{t("pr_wait_median")}</th><th className="text-right">{t("pr_wait_p90")}</th><th className="text-right">{t("pr_util")}</th></tr></thead>
                  <tbody>{res.bottlenecks.map((b) => <tr key={b.activity}><td>{b.activity}</td><td className="text-right">{pct(b.wait_share, lang)}</td><td className="text-right"><N v={b.wait_median_hours} lang={lang} /></td><td className="text-right"><N v={b.wait_p90_hours} lang={lang} /></td><td className="text-right">{b.utilisation === null ? "—" : pct(b.utilisation, lang)}</td></tr>)}</tbody>
                </table>
              </div>
              <div className="panel overflow-x-auto" data-testid="pr-resources">
                <h2 className="font-semibold mb-1">{t("pr_workload")}</h2>
                {!res.resources ? <p className="help">{t("pr_no_resources")}</p> : (
                  <table className="pr-t w-full text-[13px]">
                    <thead><tr><th className="text-left">{t("pr_resource")}</th><th className="text-right">{t("pr_events")}</th><th className="text-right">{t("pr_share")}</th><th className="text-right">{t("pr_busy")}</th><th className="text-left">{t("pr_activities")}</th></tr></thead>
                    <tbody>{res.resources.map((r) => <tr key={r.resource}><td>{r.resource}</td><td className="text-right"><N v={r.events} lang={lang} digits={0} /></td><td className="text-right">{pct(r.share_of_events, lang)}</td><td className="text-right"><N v={r.busy_hours} lang={lang} /></td><td className="help">{r.activities.join(", ")}</td></tr>)}</tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          {tab === "conformance" && (
            <div className="flex flex-col gap-4">
              <div className="panel flex flex-col gap-2" data-testid="pr-reference">
                <h2 className="font-semibold">{t("pr_reference")}</h2>
                <p className="help">{t("pr_reference_help")}</p>
                <textarea className="field mono" rows={8} aria-label={t("pr_reference")} value={refText} onChange={(e) => setRefText(e.target.value)} />
                <div className="flex flex-wrap items-end gap-3">
                  <label className="text-[13px]">{t("pr_save_as")}<input className="field" value={confSave} onChange={(e) => setConfSave(e.target.value)} /></label>
                  <button className="btn btn-primary" onClick={doConform} disabled={!!busy} data-testid="pr-conform">{busy === "conform" ? t("pr_working") : t("pr_check")}</button>
                </div>
              </div>
              {conf && (
                <div className="panel flex flex-col gap-3 overflow-x-auto" data-testid="pr-conf-result">
                  <p className="text-[13px]"><b className="num">{conf.fitting_cases}</b>/{conf.cases} {t("pr_fit")} ({pct(conf.fitting_share, lang)})</p>
                  {conf.warnings.map((w, i) => <p key={i} className="font-semibold">{w}</p>)}
                  <table className="pr-t w-full text-[13px]">
                    <thead><tr><th className="text-left">{t("pr_deviation")}</th><th className="text-left">{t("pr_detail")}</th><th className="text-right">{t("pr_cases")}</th><th className="text-right">{t("pr_share")}</th><th className="text-left">{t("pr_examples")}</th></tr></thead>
                    <tbody>{conf.deviations.map((d) => <tr key={d.type + d.detail}><td>{t("pr_dev_" + d.type.replace(/ /g, "_"))}</td><td>{d.detail}</td><td className="text-right"><N v={d.cases} lang={lang} digits={0} /></td><td className="text-right">{pct(d.share_of_cases, lang)}</td><td className="mono help">{d.examples.join(", ")}</td></tr>)}</tbody>
                  </table>
                  {conf.saved_dataset && <p>{t("pr_saved")}: <a className="mono" href={`#/datasets/${encodeURIComponent(conf.saved_dataset)}`}>{conf.saved_dataset}</a></p>}
                  <p className="help">{conf.method}</p>
                </div>
              )}
            </div>
          )}

          {tab === "rehearsal" && (
            <div className="flex flex-col gap-4">
              <div className="panel flex flex-col gap-3" data-testid="pr-rehearse">
                <h2 className="font-semibold">{t("pr_rehearse")}</h2>
                <p className="help">{t("pr_rehearse_help")}</p>
                {variantEditor}
                <div className="flex flex-wrap items-end gap-3">
                  <button className="btn btn-sm" onClick={() => setVariants([...variants, { name: `Variant ${variants.length + 1}`, changes: [blankChange()] }])}>{t("pr_add_variant")}</button>
                  <label className="text-[13px]">{t("pr_reps")}<input className="field num" style={{ width: 80 }} type="number" min="1" max="100" value={reps} onChange={(e) => setReps(e.target.value)} /></label>
                  <label className="text-[13px]">{t("pr_save_as")}<input className="field" value={rehSave} onChange={(e) => setRehSave(e.target.value)} /></label>
                  <label className="text-[13px]">{t("pr_model_name")}<input className="field" value={modelName} onChange={(e) => setModelName(e.target.value)} /></label>
                  <button className="btn btn-primary" onClick={doRehearse} disabled={!!busy} data-testid="pr-run-rehearse">{busy === "rehearse" ? t("pr_working") : t("pr_run_rehearse")}</button>
                </div>
              </div>
              {reh && (
                <div className="flex flex-col gap-4" data-testid="pr-reh-result">
                  <div className="panel flex flex-col gap-2" data-testid="pr-validation">
                    <h2 className="font-semibold"><Chip tone={reh.validation.representative ? "ok" : "warn"}>{reh.validation.representative ? t("pr_representative") : t("pr_not_representative")}</Chip> {t("pr_validation")}</h2>
                    <p className="text-[13px]">{reh.validation.note}</p>
                    <table className="pr-t w-full text-[13px]">
                      <thead><tr><th className="text-left">{t("pr_metric")}</th><th className="text-right">{t("pr_observed")}</th><th className="text-right">{t("pr_simulated")}</th><th className="text-right">{t("pr_diff")}</th><th className="text-left">{t("pr_within")}</th></tr></thead>
                      <tbody>{reh.validation.rows.map((r) => <tr key={r.metric}><td>{t("pr_v_" + r.metric)}</td><td className="text-right"><N v={r.observed} lang={lang} /></td><td className="text-right"><N v={r.simulated} lang={lang} /></td><td className="text-right">{pct(r.relative_difference, lang)}</td><td>{r.ok ? "✓" : "✗"} ≤ {pct(r.tolerance, lang)}</td></tr>)}</tbody>
                    </table>
                    {reh.warnings.map((w, i) => <p key={i} className="help">{w}</p>)}
                  </div>
                  <div className="panel overflow-x-auto" data-testid="pr-model">
                    <h2 className="font-semibold mb-1">{t("pr_model")}</h2>
                    <p className="help mb-2">{t("pr_arrivals")}: {reh.model.arrivals} · {reh.model.cases_used} {t("pr_cases")}</p>
                    <table className="pr-t w-full text-[13px]"><thead><tr><th className="text-left">{t("pr_activity")}</th><th className="text-right">{t("pr_servers")}</th><th className="text-right">{t("pr_dur_mean")}</th><th className="text-left">{t("pr_basis")}</th></tr></thead>
                      <tbody>{reh.model.activities.map((a) => <tr key={a.activity}><td>{a.activity}</td><td className="text-right">{a.servers ?? "∞"}</td><td className="text-right"><N v={a.duration_mean_hours} lang={lang} /></td><td className="help">{a.capacity_basis}</td></tr>)}</tbody></table>
                    {reh.scenario_model && <p className="mt-2 text-[13px]">{t("pr_model_saved")}: <a className="mono" href="#/scenarios">{reh.scenario_model}</a></p>}
                  </div>
                  {reh.variants.map((v) => (
                    <div key={v.name} className="panel overflow-x-auto" data-testid="pr-variant-result">
                      <h2 className="font-semibold">{v.name} <span className="help">{v.changes.join("; ")}</span></h2>
                      <p className="text-[13px] mb-2"><Chip tone={v.headline.distinguishable ? (v.headline.better === "variant" ? "ok" : "warn") : undefined}>{v.headline.distinguishable ? t("pr_distinct") : t("pr_not_distinct")}</Chip> {v.summary}</p>
                      <table className="pr-t w-full text-[13px]">
                        <thead><tr><th className="text-left">{t("pr_metric")}</th><th className="text-right">{t("pr_base")}</th><th className="text-right">{t("pr_variant")}</th><th className="text-right">{t("pr_delta")}</th><th className="text-right">{t("pr_ci")}</th><th className="text-left">{t("pr_verdict")}</th></tr></thead>
                        <tbody>{v.rows.map((r) => (
                          <tr key={r.metric}><td>{t("pr_m_" + r.metric) !== "pr_m_" + r.metric ? t("pr_m_" + r.metric) : r.metric}</td><td className="text-right"><N v={r.base} lang={lang} /></td><td className="text-right"><N v={r.variant} lang={lang} /></td><td className="text-right"><N v={r.delta} lang={lang} /></td>
                            <td className="text-right help">{r.delta_ci_low === null ? "—" : <>[<N v={r.delta_ci_low} lang={lang} />, <N v={r.delta_ci_high} lang={lang} />]</>}</td>
                            <td>{r.distinguishable ? <Chip tone={r.better === "variant" ? "ok" : r.better === "base" ? "warn" : undefined}>{t("pr_distinct")}{r.better ? ` · ${t("pr_better_" + r.better)}` : ""}</Chip> : <span className="help">{t("pr_not_distinct")}</span>}</td></tr>
                        ))}</tbody>
                      </table>
                    </div>
                  ))}
                  {reh.saved_dataset && <p>{t("pr_saved")}: <a className="mono" href={`#/datasets/${encodeURIComponent(reh.saved_dataset)}`}>{reh.saved_dataset}</a></p>}
                  <p className="help">{reh.method}</p>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
