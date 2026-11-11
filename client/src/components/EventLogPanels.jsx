import React, { useState } from "react";
import { Chip } from "./ui.jsx";
import { formatNumber } from "../format.js";

/** Event-log profile and holdout calibration panels of the Process page. Numbers come from the server as they are;
 * nothing is recomputed here. */

function N({ v, lang, digits = 2 }) {
  return <span className="num">{typeof v === "number" ? formatNumber(v, lang, { maximumFractionDigits: digits }) : "—"}</span>;
}
const pct = (v, lang) => (typeof v === "number" ? `${formatNumber(v * 100, lang, { maximumFractionDigits: 1 })}%` : "—");

export function downloadJson(obj, filename) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Stat({ s, lang }) {
  if (!s || !s.n) return <span className="help">—</span>;
  return <span className="num">n={s.n} · <N v={s.p50} lang={lang} /> / <N v={s.p90} lang={lang} /></span>;
}

function Check({ label, block, render, testid }) {
  const total = block ? block.total : 0;
  return (
    <details className="text-[13px]" data-testid={testid}>
      <summary className="cursor-pointer"><Chip tone={total ? "warn" : "ok"}>{total}</Chip> {label}</summary>
      {total > 0 && <ul className="mono help mt-1 list-disc pl-5">{block.items.map((it, i) => <li key={i}>{render(it)}</li>)}</ul>}
      {total > (block?.items?.length || 0) && <p className="help">… +{total - block.items.length}</p>}
    </details>
  );
}

export function ProfilePanel({ prof, lang, t, onExport, busy }) {
  const L = prof.log;
  const ex = L.excluded;
  const row = (r) => `${t("el_row")} ${r.row}${r.case ? ` · ${r.case}` : ""}${r.activity ? ` · ${r.activity}` : ""}${r.value !== undefined ? ` · ${JSON.stringify(r.value)}` : ""}${r.reason ? ` (${r.reason})` : ""}`;
  return (
    <div className="flex flex-col gap-4" data-testid="pr-profile">
      <div className="panel flex flex-col gap-2">
        <p className="text-[13px]">
          <b className="num">{prof.cases.closed}</b> {t("el_closed")} · <b className="num">{prof.cases.open}</b> {t("el_open")} · <b className="num">{L.rows_read}</b> {t("el_rows")} · <b className="num">{L.instances}</b> {t("el_instances")} · {t("el_mode")}: <span className="mono">{L.mode}</span>
        </p>
        {prof.explanation.map((x, i) => <p key={i} className="text-[13px]"><Chip tone="warn">{t("pr_bottleneck")}</Chip> {x}</p>)}
        {prof.warnings.map((w, i) => <p key={i} className="help">{w}</p>)}
        {prof.missing_data.length > 0 && (
          <div data-testid="pr-missing-data"><h3 className="font-semibold text-[13px]">{t("el_missing_data")}</h3>
            <ul className="list-disc pl-5 text-[13px]">{prof.missing_data.map((m, i) => <li key={i}>{m}</li>)}</ul></div>
        )}
        <div className="flex flex-wrap gap-2">
          <button className="btn btn-sm" onClick={onExport} disabled={!!busy} data-testid="pr-export">{busy === "export" ? t("pr_working") : t("el_export")}</button>
          <span className="help">{t("el_export_help")}</span>
        </div>
      </div>

      <SeasonalityBlock s={prof.arrival_seasonality} lang={lang} t={t} />

      <div className="panel flex flex-col gap-1" data-testid="pr-checks">
        <h2 className="font-semibold mb-1">{t("el_checks")}</h2>
        <p className="help mb-1">{L.ordering}</p>
        <Check testid="pr-check-duplicates" label={t("el_c_duplicates")} block={L.duplicates} render={(r) => `${t("el_row")} ${r.row} = ${t("el_row")} ${r.duplicate_of} · ${r.case} · ${r.activity} · ${r.at}`} />
        <Check testid="pr-check-ties" label={t("el_c_ties")} block={L.ties} render={(r) => `${r.case} · ${r.at} · ${r.activities.join(" = ")} (${t("el_rows_ref")} ${r.rows.join(", ")})`} />
        <Check testid="pr-check-invalid" label={t("el_c_invalid")} block={ex.invalid_timestamp} render={row} />
        <Check label={t("el_c_invalid_start")} block={ex.invalid_start} render={row} />
        <Check label={t("el_c_missing_ids")} block={ex.missing_case_or_activity} render={row} />
        <Check label={t("el_c_lifecycle")} block={ex.unknown_lifecycle} render={row} />
        <Check label={t("el_c_no_start")} block={L.complete_without_start} render={(r) => `${t("el_row")} ${r.row} · ${r.case} · ${r.activity} · ${r.at}`} />
        <Check label={t("el_c_no_complete")} block={L.start_without_complete} render={(r) => `${t("el_row")} ${r.row} · ${r.case} · ${r.activity} · ${r.started}`} />
        <Check label={t("el_c_start_after_end")} block={L.start_after_end} render={(r) => `${t("el_row")} ${r.row} · ${r.case} · ${r.activity}`} />
        <Check testid="pr-check-open" label={t("el_c_open")} block={L.open_cases} render={(r) => `${r.case} · ${r.last_activity} · ${r.last_at}${r.in_progress ? " · " + t("el_in_progress") : ""}`} />
        <Check testid="pr-check-zones" label={t("el_c_zones")} block={L.timezone.zone_changes} render={(r) => `${r.case}: ${r.offsets.join(" → ")}`} />
        <Check label={t("el_c_ambiguous")} block={L.timezone.ambiguous_local_times} render={(r) => `${t("el_row")} ${r.row} · ${r.case} · ${r.value}`} />
        <p className="help mt-1">{t("el_zone")}: <span className="mono">{L.timezone.declared}</span> → UTC · {Object.entries(L.timezone.offsets).map(([k, v]) => `${k}: ${v}`).join(", ")}. {L.timezone.note}</p>
        {L.pairing && (
          <details className="text-[13px]" data-testid="pr-pairing">
            <summary className="cursor-pointer"><Chip tone={L.pairing.rule === "fifo" ? "warn" : "ok"}>{L.pairing.rule === "fifo" ? "FIFO" : "resource → FIFO"}</Chip> {t("el_pairing_title")}</summary>
            <p className="help mt-1">{L.pairing.description}</p>
            <table className="pr-t w-full text-[13px] mt-1"><thead><tr><th className="text-left">{t("pr_activity")}</th><th className="text-right">{t("el_pairing_pairs")}</th><th className="text-right">{t("el_pairing_overlap")}</th><th className="text-right">{t("el_pairing_by_resource")}</th><th className="text-right">{t("el_pairing_by_fifo")}</th><th className="text-right">{t("el_pairing_differs")}</th><th className="text-left">{t("el_pairing_rule")}</th></tr></thead>
              <tbody>{L.pairing.activities.map((a) => <tr key={a.activity}><td>{a.activity}</td><td className="text-right">{a.pairs}</td><td className="text-right">{a.overlapping}</td><td className="text-right">{a.by_resource}</td><td className="text-right">{a.by_fifo}</td><td className="text-right">{a.differs_from_fifo}</td><td className="mono">{a.rule_used}</td></tr>)}</tbody></table>
          </details>
        )}
        {L.missing_steps.length > 0 && <p className="text-[13px]">{t("el_missing_steps")}: {L.missing_steps.map((m) => `${m.activity} (${m.cases})`).join(", ")}</p>}
      </div>

      <div className="panel overflow-x-auto" data-testid="pr-times">
        <h2 className="font-semibold mb-1">{t("el_times")}</h2>
        <p className="help mb-2">{prof.times.note} {t("el_stat_fmt")}</p>
        <p className="text-[13px] mb-2">{t("el_cycle")}: n={prof.times.cycle_time_hours.n} · {t("pr_mean")} <N v={prof.times.cycle_time_hours.mean} lang={lang} /> · p25 <N v={prof.times.cycle_time_hours.p25} lang={lang} /> · p50 <N v={prof.times.cycle_time_hours.p50} lang={lang} /> · p75 <N v={prof.times.cycle_time_hours.p75} lang={lang} /> · p90 <N v={prof.times.cycle_time_hours.p90} lang={lang} /> · p95 <N v={prof.times.cycle_time_hours.p95} lang={lang} /> h</p>
        <table className="pr-t w-full text-[13px]">
          <thead><tr><th className="text-left">{t("pr_activity")}</th><th className="text-left">{t("el_separable")}</th><th className="text-right">{t("el_service")}</th><th className="text-right">{t("el_waiting")}</th><th className="text-right">{t("el_elapsed")}</th></tr></thead>
          <tbody>{prof.times.activities.map((a) => (
            <tr key={a.activity}><td>{a.activity}</td><td><Chip tone={a.identifiable === "service_and_waiting" ? "ok" : "warn"}>{t("el_id_" + a.identifiable)}</Chip></td>
              <td className="text-right"><Stat s={a.service_hours} lang={lang} /></td><td className="text-right"><Stat s={a.waiting_hours} lang={lang} /></td><td className="text-right"><Stat s={a.elapsed_since_previous_completion_hours} lang={lang} /></td></tr>
          ))}</tbody>
        </table>
      </div>

      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))" }}>
        <div className="panel overflow-x-auto" data-testid="pr-prof-bottlenecks">
          <h2 className="font-semibold mb-1">{t("el_accumulates")}</h2>
          <table className="pr-t w-full text-[13px]">
            <thead><tr><th className="text-left">{t("pr_activity")}</th><th className="text-left">{t("el_measure")}</th><th className="text-right">{t("pr_share")}</th><th className="text-right">n</th><th className="text-right">p50</th><th className="text-right">p90</th><th className="text-right">{t("el_avg_waiting")}</th></tr></thead>
            <tbody>{prof.bottlenecks.map((b) => <tr key={b.activity}><td>{b.activity}</td><td>{t("el_m_" + b.measure)}</td><td className="text-right">{pct(b.share, lang)}</td><td className="text-right">{b.n}</td><td className="text-right"><N v={b.median_hours} lang={lang} /></td><td className="text-right"><N v={b.p90_hours} lang={lang} /></td><td className="text-right"><N v={b.average_cases_waiting} lang={lang} /></td></tr>)}</tbody>
          </table>
        </div>
        <div className="panel overflow-x-auto" data-testid="pr-concurrency">
          <h2 className="font-semibold mb-1">{t("el_concurrency")}</h2>
          {prof.concurrency.length === 0 ? <p className="help">{prof.times.service_separable ? t("el_no_concurrency") : t("el_concurrency_needs_start")}</p> : (
            <table className="pr-t w-full text-[13px]"><thead><tr><th className="text-left">{t("el_pair")}</th><th className="text-right">{t("pr_cases")}</th><th className="text-right">{t("pr_share")}</th><th className="text-left">{t("pr_examples")}</th></tr></thead>
              <tbody>{prof.concurrency.map((c) => <tr key={c.pair.join()}><td>{c.pair.join(" ∥ ")}</td><td className="text-right">{c.cases}</td><td className="text-right">{pct(c.share_of_cases, lang)}</td><td className="mono help">{c.examples.join(", ")}</td></tr>)}</tbody></table>
          )}
          <p className="help mt-2">{prof.graph.coverage.note} {t("el_coverage")}: {pct(prof.graph.coverage.share_of_transitions, lang)} · {t("el_cases_covered")}: {pct(prof.graph.coverage.share_of_cases, lang)}</p>
        </div>
      </div>

      <div className="panel overflow-x-auto" data-testid="pr-deviations">
        <h2 className="font-semibold mb-1">{t("el_deviations")}</h2>
        {prof.deviations.length === 0 ? <p className="help">{t("el_no_deviations")}</p> : (
          <table className="pr-t w-full text-[13px]">
            <thead><tr><th className="text-left">{t("pr_deviation")}</th><th className="text-left">{t("pr_detail")}</th><th className="text-right">{t("pr_count")}</th><th className="text-left">{t("pr_examples")}</th><th className="text-left">{t("el_why")}</th></tr></thead>
            <tbody>{prof.deviations.map((d) => <tr key={d.type + d.detail}><td>{t("el_dev_" + d.type.replace(/ /g, "_"))}</td><td>{d.detail}</td><td className="text-right">{d.count}</td><td className="mono help">{d.examples.join("; ")}</td><td className="help">{d.why}</td></tr>)}</tbody>
          </table>
        )}
        <p className="help mt-2">{prof.caveat}</p>
      </div>
    </div>
  );
}

const fmtVal = (v, lang) => {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return formatNumber(v, lang, { maximumFractionDigits: 3 });
  if (v.dist) return `${v.dist}, ${formatNumber(v.mean_hours, lang, { maximumFractionDigits: 3 })} h`;
  if (v.type === "poisson") return `poisson ${formatNumber(v.rate, lang, { maximumFractionDigits: 3 })}/h`;
  if (v.type) return `${v.type} (${v.values_count})`;
  return Object.entries(v).map(([k, x]) => `${k} ${pct(x, lang)}`).join(", ");
};

const fmtP = (p, lang) => (typeof p !== "number" ? "—" : p < 0.0001 ? "< 0,0001".replace(",", lang === "es" ? "," : ".") : formatNumber(p, lang, { maximumFractionDigits: 4 }));

/** Hour-of-day and weekday arrival pattern with the chi-square test against a constant rate. */
export function SeasonalityBlock({ s, lang, t }) {
  if (!s) return null;
  const hod = s.hour_of_day, dow = s.day_of_week;
  const bars = (block, labels) => {
    const max = Math.max(...block.rate_per_hour, 1e-12);
    return (
      <div className="flex items-end gap-[2px]" style={{ height: 70 }} role="img" aria-label={t("sn_chart")}>
        {block.rate_per_hour.map((r, i) => (
          <div key={i} className="flex flex-col items-center justify-end" style={{ width: labels ? 42 : 14, height: "100%" }} title={`${labels ? labels[i] : i + ":00"} · ${formatNumber(r, lang, { maximumFractionDigits: 2 })}/h · n=${block.counts[i]}`}>
            <div style={{ width: "100%", height: `${Math.max(2, (r / max) * 52)}px`, background: "currentColor", opacity: block.peaks?.some((p) => p.slot === i) ? 0.9 : 0.35 }} />
            <span className="help" style={{ fontSize: 10 }}>{labels ? labels[i] : i}</span>
          </div>
        ))}
      </div>
    );
  };
  const testLine = (b, label) => b && b.test && (
    <span className="help" key={label}>{label}: χ² = <span className="num">{formatNumber(b.test.statistic, lang, { maximumFractionDigits: 1 })}</span>, {t("sn_df")} {b.test.df}, p = <span className="num">{fmtP(b.test.p_value, lang)}</span>{b.test.reliable === false ? ` (${t("sn_unreliable")})` : ""}</span>
  );
  return (
    <div className="panel flex flex-col gap-2" data-testid="pr-seasonality">
      <h2 className="font-semibold"><Chip tone={s.significant ? "warn" : "ok"}>{s.significant ? t("sn_pattern") : s.evaluable ? t("sn_constant") : t("sn_not_evaluable")}</Chip> {t("sn_title")}</h2>
      <p className="text-[13px]">{s.verdict}</p>
      {s.evaluable && hod && (
        <>
          <div className="overflow-x-auto"><div style={{ minWidth: 24 * 16 }} data-testid="pr-seasonality-hours">{bars(hod)}</div></div>
          {dow && <div className="overflow-x-auto"><div data-testid="pr-seasonality-days">{bars(dow, dow.labels)}</div></div>}
          <p className="flex flex-wrap gap-x-4">{testLine(hod, t("sn_by_hour"))}{testLine(dow, t("sn_by_day"))}</p>
          {hod.peaks?.length > 0 && <p className="help">{t("sn_peaks")}: {hod.peaks.map((p) => `${p.slot}:00 (×${formatNumber(p.ratio_to_mean, lang, { maximumFractionDigits: 1 })})`).join(", ")}</p>}
        </>
      )}
      {s.caveat && <p className="help">{s.caveat}</p>}
    </div>
  );
}

/** Value-by-value editor of an empirical sample: change, remove or add single values; sends set/remove/add. */
function ValuesEditor({ values, cur, set, t, label }) {
  const c = cur || { set: {}, remove: [], add: [] };
  const shown = values.slice(0, 300);
  const [addv, setAddv] = useState("");
  const upd = (patch) => {
    const next = { ...c, ...patch };
    const empty = !Object.keys(next.set).length && !next.remove.length && !next.add.length;
    set(empty ? undefined : next);
  };
  return (
    <details className="text-[13px]" data-testid={`pr-values-${label}`}>
      <summary className="cursor-pointer">{t("el_values_edit")} ({values.length})</summary>
      <div className="flex flex-wrap gap-1 mt-1" style={{ maxHeight: 180, overflowY: "auto" }}>
        {shown.map((v, i) => {
          const removed = c.remove.includes(i);
          return (
            <span key={i} className="flex items-center gap-[2px]" style={removed ? { opacity: 0.4, textDecoration: "line-through" } : undefined}>
              <input className="field num" style={{ width: 68 }} type="number" step="any" min="0" aria-label={`${label}[${i}]`} disabled={removed}
                value={c.set[i] ?? v} onChange={(e) => { const s2 = { ...c.set }; if (e.target.value === "" || Number(e.target.value) === v) delete s2[i]; else s2[i] = Number(e.target.value); upd({ set: s2 }); }} data-testid={`pr-value-${label}-${i}`} />
              <button className="btn btn-sm" type="button" aria-label={`${t("el_values_remove")} ${i}`} onClick={() => upd({ remove: removed ? c.remove.filter((x) => x !== i) : [...c.remove, i] })}>{removed ? "↺" : "×"}</button>
            </span>
          );
        })}
      </div>
      {values.length > shown.length && <p className="help">{t("el_values_more").replace("{n}", values.length - shown.length)}</p>}
      <div className="flex items-center gap-2 mt-1">
        <input className="field num" style={{ width: 90 }} type="number" step="any" min="0" placeholder={t("el_edit_hours")} aria-label={t("el_values_add")} value={addv} onChange={(e) => setAddv(e.target.value)} data-testid={`pr-value-add-${label}`} />
        <button className="btn btn-sm" type="button" disabled={addv === ""} onClick={() => { upd({ add: [...c.add, Number(addv)] }); setAddv(""); }} data-testid={`pr-value-add-btn-${label}`}>{t("el_values_add")}</button>
        {c.add.length > 0 && <span className="help">+ {c.add.join(", ")} <button className="btn btn-sm" type="button" onClick={() => upd({ add: [] })}>×</button></span>}
      </div>
    </details>
  );
}

const DIST_FIELDS = { fixed: ["value"], exponential: ["mean"], uniform: ["min", "max"], triangular: ["min", "mode", "max"], lognormal: ["mean", "sigma"] };
const num = (x) => (x === "" || x === undefined || x === null ? null : Number(x));

/** The edits object the server expects (same shape as the API's model_edits) from the raw draft the inputs hold. */
export function editsFromDraft(draft) {
  const out = {};
  for (const [key, d] of Object.entries(draft)) {
    const [kind, name] = [key.split(":")[0], key.slice(key.indexOf(":") + 1)];
    if (key === "arrivals") { if (num(d) !== null) out.arrival_rate = num(d); continue; }
    if (key === "gaps") { if (d) out.arrival_gaps = d; continue; }
    if (kind === "values") {
      if (d) { out.stations = out.stations || {}; out.stations[name] = { ...(out.stations[name] || {}), service_values: d }; }
      continue;
    }
    if (kind === "servers") { if (num(d) !== null) { out.stations = out.stations || {}; out.stations[name] = { ...(out.stations[name] || {}), servers: num(d) }; } continue; }
    if (kind === "service") {
      if (d && d.dist) {
        const spec = { dist: d.dist };
        for (const f of DIST_FIELDS[d.dist] || []) spec[f] = num(d[f]) ?? d[f] ?? "";
        out.stations = out.stations || {}; out.stations[name] = { ...(out.stations[name] || {}), service: spec };
      }
      continue;
    }
    if (kind === "routing") {
      const shares = Object.fromEntries(Object.entries(d || {}).filter(([, x]) => num(x) !== null).map(([k, x]) => [k, num(x)]));
      if (Object.keys(shares).length) { out.routing = out.routing || {}; if (name === "start") out.routing.start = shares; else { out.routing.next = out.routing.next || {}; out.routing.next[name] = shares; } }
    }
  }
  return out;
}

function ParamEditor({ p, draft, setDraft, t, model }) {
  const key = p.parameter;
  const set = (v) => setDraft({ ...draft, [key]: v });
  const d = draft[key];
  const aria = `${t("el_edit")} ${key}`;
  if (key === "arrivals") {
    const gaps = model?.arrivals?.type === "empirical" && Array.isArray(model.arrivals.values) ? model.arrivals.values : null;
    return (
      <span className="flex flex-col gap-1">
        <input className="field num" style={{ width: 90 }} type="number" min="0" step="any" placeholder={t("el_edit_rate")} aria-label={aria} value={d ?? ""} onChange={(e) => set(e.target.value)} data-testid={`pr-edit-${key}`} />
        {gaps && <ValuesEditor values={gaps} cur={draft.gaps} set={(v) => setDraft({ ...draft, gaps: v })} t={t} label="gaps" />}
      </span>
    );
  }
  if (key.startsWith("service:") && Array.isArray(model?.stations?.[key.slice(8)]?.service?.values)) {
    const name = key.slice(8);
    return (
      <span className="flex flex-col gap-1">
        <ValuesEditor values={model.stations[name].service.values} cur={draft[`values:${name}`]} set={(v) => setDraft({ ...draft, [`values:${name}`]: v })} t={t} label={name} />
      </span>
    );
  }
  if (key.startsWith("servers:")) {
    return <input className="field num" style={{ width: 90 }} type="number" min="1" step="1" placeholder={t("el_edit_servers")} aria-label={aria} value={d ?? ""} onChange={(e) => set(e.target.value)} data-testid={`pr-edit-${key}`} />;
  }
  if (key.startsWith("service:")) {
    const cur = d || {};
    return (
      <span className="flex flex-wrap items-center gap-1">
        <select className="field" aria-label={aria} value={cur.dist || ""} onChange={(e) => (e.target.value ? set({ dist: e.target.value }) : set(undefined))} data-testid={`pr-edit-${key}`}>
          <option value="">{t("el_edit_keep")}</option>
          {Object.keys(DIST_FIELDS).map((x) => <option key={x} value={x}>{x}</option>)}
        </select>
        {(DIST_FIELDS[cur.dist] || []).map((f) => (
          <label key={f} className="help">{f}<input className="field num" style={{ width: 70 }} type="number" step="any" placeholder={t("el_edit_hours")} value={cur[f] ?? ""} onChange={(e) => set({ ...cur, [f]: e.target.value })} data-testid={`pr-edit-${key}-${f}`} /></label>
        ))}
      </span>
    );
  }
  if (key.startsWith("routing:") && p.value && typeof p.value === "object") {
    const cur = d || {};
    return (
      <span className="flex flex-wrap items-center gap-1">
        {Object.keys(p.value).map((target) => (
          <label key={target} className="help">{target}<input className="field num" style={{ width: 64 }} type="number" min="0" step="any" placeholder={t("el_edit_share")} value={cur[target] ?? ""} onChange={(e) => set({ ...cur, [target]: e.target.value })} data-testid={`pr-edit-${key}-${target}`} /></label>
        ))}
      </span>
    );
  }
  return null;
}

export function CalibrationPanel({ cal, lang, t, busy, onRerun }) {
  const v = cal.validation;
  const [draft, setDraft] = useState({});
  const edits = editsFromDraft(draft);
  const nEdits = Object.keys(draft).filter((k) => draft[k] !== undefined && draft[k] !== "").length;
  return (
    <div className="flex flex-col gap-4" data-testid="pr-cal-result">
      <div className="panel flex flex-col gap-2" data-testid="pr-cal-verdict">
        <h2 className="font-semibold"><Chip tone={v.passed ? "ok" : "warn"}>{v.passed ? t("el_cal_passed") : t("el_cal_failed")}</Chip> {t("el_holdout_check")}</h2>
        <p className={"text-[13px]" + (v.passed ? "" : " font-semibold")} role={v.passed ? undefined : "alert"}>{v.verdict}</p>
        <p className="help">{cal.split.rule} {t("el_split")}: <span className="mono">{cal.split.at}</span> · {t("el_fit_cases")}: {cal.split.fit_cases} · {t("el_holdout_cases")}: {cal.split.holdout_cases} · {t("pr_reps")} {v.replications} · seed {v.seed}</p>
        <div className="overflow-x-auto"><table className="pr-t w-full text-[13px]">
          <thead><tr><th className="text-left">{t("pr_metric")}</th><th className="text-right">{t("el_fit_obs")}</th><th className="text-right">{t("el_holdout_obs")}</th><th className="text-right">{t("pr_simulated")}</th><th className="text-right">{t("pr_ci")}</th><th className="text-right">{t("pr_diff")}</th><th className="text-left">{t("el_tolerance")}</th></tr></thead>
          <tbody>{v.rows.map((r) => (
            <tr key={r.metric}><td>{t("el_v_" + r.metric)}{r.activity ? ` (${r.activity})` : ""}</td><td className="text-right"><N v={r.fit_observed} lang={lang} /></td><td className="text-right"><N v={r.holdout_observed} lang={lang} /></td><td className="text-right"><N v={r.simulated} lang={lang} /></td>
              <td className="text-right help">{r.simulated_ci[0] === null ? "—" : <>[<N v={r.simulated_ci[0]} lang={lang} />, <N v={r.simulated_ci[1]} lang={lang} />]</>}</td>
              <td className="text-right">{pct(r.relative_difference, lang)}</td><td>{r.ok === null ? <span className="help">{r.note}</span> : <>{r.ok ? "✓" : "✗"} ≤ {pct(r.tolerance, lang)}</>}</td></tr>
          ))}</tbody>
        </table></div>
        {cal.warnings.map((w, i) => <p key={i} className="help">{w}</p>)}
      </div>

      <SeasonalityBlock s={cal.arrival_seasonality} lang={lang} t={t} />

      {cal.edit_diff && (
        <div className="panel overflow-x-auto" data-testid="pr-edit-diff">
          <h2 className="font-semibold mb-1"><Chip tone="warn">{t("el_edited")}</Chip> {t("el_diff_title")}</h2>
          <p className="text-[13px] mb-1">{cal.edit_diff.verdict}</p>
          <p className="help mb-2">{cal.edit_diff.note}</p>
          <table className="pr-t w-full text-[13px]">
            <thead><tr><th className="text-left">{t("pr_metric")}</th><th className="text-right">{t("el_holdout_obs")}</th><th className="text-right">{t("el_diff_fitted")}</th><th className="text-right">{t("el_diff_edited")}</th><th className="text-right">{t("el_diff_delta")}</th><th className="text-left">{t("el_tolerance")}</th></tr></thead>
            <tbody>{cal.edit_diff.rows.map((r) => (
              <tr key={r.metric}><td>{t("el_v_" + r.metric)}</td><td className="text-right"><N v={r.holdout_observed} lang={lang} /></td><td className="text-right"><N v={r.fitted_simulated} lang={lang} /> {r.fitted_ok === null ? "" : r.fitted_ok ? "✓" : "✗"}</td><td className="text-right"><N v={r.edited_simulated} lang={lang} /> {r.edited_ok === null ? "" : r.edited_ok ? "✓" : "✗"}</td><td className="text-right"><N v={r.delta} lang={lang} /></td><td className="help">{pct(r.edited_relative_difference, lang)}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}

      <div className="panel overflow-x-auto" data-testid="pr-cal-params">
        <h2 className="font-semibold mb-1">{t("el_proposal")}</h2>
        <p className="help mb-2">{t("el_proposal_help")}</p>
        <table className="pr-t w-full text-[13px]">
          <thead><tr><th className="text-left">{t("el_parameter")}</th><th className="text-left">{t("el_value")}</th><th className="text-right">n</th><th className="text-left">{t("el_estimator")}</th><th className="text-left">{t("el_identifiable")}</th>{onRerun && <th className="text-left">{t("el_edit")}</th>}</tr></thead>
          <tbody>{cal.parameters.map((p) => (
            <tr key={p.parameter} data-edited={p.edited ? "true" : undefined}><td className="mono">{p.parameter}</td>
              <td>{p.edited ? <><Chip tone="warn">{t("el_edited")}</Chip> {fmtVal(p.value, lang)} <span className="help">({t("el_original")}: {p.original_value && p.original_value.dist ? fmtVal(p.original_value, lang) : p.parameter.startsWith("servers:") && !p.fitted?.identifiable ? "∞" : fmtVal(p.original_value, lang)})</span></>
                : (p.parameter.startsWith("servers:") && !p.identifiable ? "∞" : fmtVal(p.value, lang))}</td><td className="text-right">{(p.edited ? p.fitted.n : p.n) ?? "—"}</td><td className="help">{p.edited ? <>{p.estimator} · {t("el_original")}: {p.fitted.estimator}</> : p.estimator}</td>
              <td>{(p.edited ? p.fitted.identifiable : p.identifiable) ? <Chip tone="ok">✓</Chip> : <span><Chip tone="warn">✗</Chip> <span className="help">{p.edited ? p.fitted.missing_data : p.missing_data}</span></span>}</td>
              {onRerun && <td><ParamEditor p={p.edited ? { ...p, value: p.original_value } : p} draft={draft} setDraft={setDraft} t={t} model={cal.model} /></td>}</tr>
          ))}</tbody>
        </table>
        {onRerun && (
          <div className="flex flex-wrap items-center gap-2 mt-2">
            <button className="btn btn-primary btn-sm" disabled={!!busy || !nEdits} onClick={() => onRerun(edits)} data-testid="pr-rerun-edits">{busy === "calibrate" ? t("pr_working") : `${t("el_edit_rerun")} (${nEdits})`}</button>
            <button className="btn btn-sm" disabled={!!busy || (!nEdits && cal.model_source !== "edited")} onClick={() => { setDraft({}); onRerun(null); }} data-testid="pr-reset-edits">{t("el_edit_reset")}</button>
            <span className="help">{t("el_edit_help")}</span>
          </div>
        )}
        {cal.scenario_model && <p className="mt-2 text-[13px]">{t("pr_model_saved")}: <a className="mono" href="#/scenarios">{cal.scenario_model}</a></p>}
      </div>

      {cal.alternatives.map((a) => (
        <div key={a.name} className="panel overflow-x-auto" data-testid="pr-cal-alt">
          <h2 className="font-semibold">{a.name} <span className="help">{a.changes.join("; ")}</span></h2>
          {a.warnings.map((w, i) => <p key={i} className="help font-semibold">{w}</p>)}
          <p className="text-[13px] mb-2"><Chip tone={a.headline.distinguishable ? (a.headline.better === "variant" ? "ok" : "warn") : undefined}>{a.headline.distinguishable ? t("pr_distinct") : t("pr_not_distinct")}</Chip> {a.summary}</p>
          <table className="pr-t w-full text-[13px]">
            <thead><tr><th className="text-left">{t("pr_metric")}</th><th className="text-right">{t("pr_base")}</th><th className="text-right">{t("pr_variant")}</th><th className="text-right">{t("pr_delta")}</th><th className="text-right">{t("pr_ci")}</th></tr></thead>
            <tbody>{a.rows.map((r) => (
              <tr key={r.metric}><td>{t("pr_m_" + r.metric) !== "pr_m_" + r.metric ? t("pr_m_" + r.metric) : r.metric}</td><td className="text-right"><N v={r.base} lang={lang} /></td><td className="text-right"><N v={r.variant} lang={lang} /></td><td className="text-right"><N v={r.delta} lang={lang} /></td>
                <td className="text-right help">{r.delta_ci_low === null ? "—" : <>[<N v={r.delta_ci_low} lang={lang} />, <N v={r.delta_ci_high} lang={lang} />]</>}</td></tr>
            ))}</tbody>
          </table>
        </div>
      ))}
      <p className="help">{cal.method} {cal.caveat}</p>
    </div>
  );
}
