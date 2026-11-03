import React from "react";
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
  const row = (r) => `row ${r.row}${r.case ? ` · ${r.case}` : ""}${r.activity ? ` · ${r.activity}` : ""}${r.value !== undefined ? ` · ${JSON.stringify(r.value)}` : ""}${r.reason ? ` (${r.reason})` : ""}`;
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

      <div className="panel flex flex-col gap-1" data-testid="pr-checks">
        <h2 className="font-semibold mb-1">{t("el_checks")}</h2>
        <p className="help mb-1">{L.ordering}</p>
        <Check testid="pr-check-duplicates" label={t("el_c_duplicates")} block={L.duplicates} render={(r) => `row ${r.row} = row ${r.duplicate_of} · ${r.case} · ${r.activity} · ${r.at}`} />
        <Check testid="pr-check-ties" label={t("el_c_ties")} block={L.ties} render={(r) => `${r.case} · ${r.at} · ${r.activities.join(" = ")} (rows ${r.rows.join(", ")})`} />
        <Check testid="pr-check-invalid" label={t("el_c_invalid")} block={ex.invalid_timestamp} render={row} />
        <Check label={t("el_c_invalid_start")} block={ex.invalid_start} render={row} />
        <Check label={t("el_c_missing_ids")} block={ex.missing_case_or_activity} render={row} />
        <Check label={t("el_c_lifecycle")} block={ex.unknown_lifecycle} render={row} />
        <Check label={t("el_c_no_start")} block={L.complete_without_start} render={(r) => `row ${r.row} · ${r.case} · ${r.activity} · ${r.at}`} />
        <Check label={t("el_c_no_complete")} block={L.start_without_complete} render={(r) => `row ${r.row} · ${r.case} · ${r.activity} · ${r.started}`} />
        <Check label={t("el_c_start_after_end")} block={L.start_after_end} render={(r) => `row ${r.row} · ${r.case} · ${r.activity}`} />
        <Check testid="pr-check-open" label={t("el_c_open")} block={L.open_cases} render={(r) => `${r.case} · ${r.last_activity} · ${r.last_at}${r.in_progress ? " · " + t("el_in_progress") : ""}`} />
        <Check testid="pr-check-zones" label={t("el_c_zones")} block={L.timezone.zone_changes} render={(r) => `${r.case}: ${r.offsets.join(" → ")}`} />
        <Check label={t("el_c_ambiguous")} block={L.timezone.ambiguous_local_times} render={(r) => `row ${r.row} · ${r.case} · ${r.value}`} />
        <p className="help mt-1">{t("el_zone")}: <span className="mono">{L.timezone.declared}</span> → UTC · {Object.entries(L.timezone.offsets).map(([k, v]) => `${k}: ${v}`).join(", ")}. {L.timezone.note}</p>
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

export function CalibrationPanel({ cal, lang, t }) {
  const v = cal.validation;
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

      <div className="panel overflow-x-auto" data-testid="pr-cal-params">
        <h2 className="font-semibold mb-1">{t("el_proposal")}</h2>
        <p className="help mb-2">{t("el_proposal_help")}</p>
        <table className="pr-t w-full text-[13px]">
          <thead><tr><th className="text-left">{t("el_parameter")}</th><th className="text-left">{t("el_value")}</th><th className="text-right">n</th><th className="text-left">{t("el_estimator")}</th><th className="text-left">{t("el_identifiable")}</th></tr></thead>
          <tbody>{cal.parameters.map((p) => (
            <tr key={p.parameter}><td className="mono">{p.parameter}</td><td>{p.parameter.startsWith("servers:") && !p.identifiable ? "∞" : fmtVal(p.value, lang)}</td><td className="text-right">{p.n ?? "—"}</td><td className="help">{p.estimator}</td>
              <td>{p.identifiable ? <Chip tone="ok">✓</Chip> : <span><Chip tone="warn">✗</Chip> <span className="help">{p.missing_data}</span></span>}</td></tr>
          ))}</tbody>
        </table>
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
