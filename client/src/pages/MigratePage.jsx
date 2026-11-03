import React, { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Chip } from "../components/ui.jsx";

const TYPES = ["INTEGER", "REAL", "TEXT", "DATE", "BOOLEAN"];
const REASONS = ["cast_failure", "missing_required", "duplicate_primary_key", "duplicate_unique_key", "foreign_key_not_found"];
const EMPTY = { name: "", tables: [] };
const blank = (v) => (v === null || v === undefined ? <span className="help">∅</span> : String(v));

function save(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export default function MigratePage() {
  const { t, datasets, act } = useApp();
  const names = useMemo(() => datasets.map((d) => d.name), [datasets]);
  const [spec, setSpec] = useState(EMPTY);
  const [tab, setTab] = useState("grid");
  const [jsonText, setJsonText] = useState("");
  const [jsonErr, setJsonErr] = useState("");
  const [specs, setSpecs] = useState([]);
  const [sugDs, setSugDs] = useState("");
  const [sugNote, setSugNote] = useState(null);
  const [saveRejected, setSaveRejected] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState("");
  const typing = useRef(false);

  const refresh = async () => { try { setSpecs((await api.migrateSpecs()).specs); } catch { /* listing is optional */ } };
  useEffect(() => { refresh(); }, []);
  useEffect(() => { if (!sugDs && names[0]) setSugDs(names[0]); }, [names]);
  useEffect(() => { if (!typing.current) { setJsonText(JSON.stringify(spec, null, 2)); setJsonErr(""); } }, [spec]);
  useEffect(() => { typing.current = false; setJsonText(JSON.stringify(spec, null, 2)); setJsonErr(""); }, [tab]);

  const setTable = (i, patch) => setSpec({ ...spec, tables: spec.tables.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  const setCol = (i, k, patch) => setTable(i, { columns: spec.tables[i].columns.map((c, j) => (j === k ? { ...c, ...patch } : c)) });
  const onJson = (text) => {
    typing.current = true;
    setJsonText(text);
    try { const v = JSON.parse(text); setSpec(v); setJsonErr(""); } catch (e) { setJsonErr(String(e.message)); }
  };

  const suggest = async () => {
    setBusy("suggest");
    try {
      const s = await act(() => api.migrateSuggest(sugDs));
      setSugNote(s.note);
      setSpec({ ...spec, name: spec.name || s.spec.name, tables: [...spec.tables, ...s.spec.tables] });
    } finally { setBusy(""); }
  };
  const load = async (name) => {
    const g = await act(() => api.migrateSpec(name));
    setSpec(g.spec); setResult(null);
  };
  const saveSpec = async () => { await act(() => api.migrateSave(spec.name, spec), t("mg_saved_ok")); refresh(); };
  const delSpec = async () => { await act(() => api.migrateDelete(spec.name)); refresh(); };
  const run = async () => {
    setBusy("run");
    try { setResult(await act(() => api.migrateRehearse({ spec, save_rejected: saveRejected || null, sample: 10 }), saveRejected ? t("mg_rejected_saved") : null)); }
    finally { setBusy(""); }
  };
  const ddl = async () => { save(await api.migrateDdl({ spec }), `${spec.name || "schema"}.sql`); };
  const file = async () => { save(await api.migrateFile(result.name), `${result.name}.sqlite`); };

  const s = result && result.summary;
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">{t("mg_title")}</h1>
        <p className="help mt-1">{t("mg_intro")}</p>
      </div>

      <div className="panel flex flex-wrap items-end gap-3" data-testid="mg-top">
        <div><label className="label" htmlFor="mg-name">{t("mg_name")}</label>
          <input id="mg-name" className="field" value={spec.name || ""} onChange={(e) => setSpec({ ...spec, name: e.target.value })} /></div>
        <div><label className="label" htmlFor="mg-saved">{t("mg_saved")}</label>
          <select id="mg-saved" className="field" value="" onChange={(e) => e.target.value && load(e.target.value)}>
            <option value="">{specs.length ? "—" : t("mg_none_saved")}</option>
            {specs.map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}
          </select></div>
        <button className="btn" onClick={saveSpec} disabled={!spec.name || !spec.tables.length}>{t("mg_save")}</button>
        <button className="btn" onClick={delSpec} disabled={!specs.some((x) => x.name === spec.name)}>{t("mg_delete")}</button>
        <div className="flex items-end gap-2 ml-auto">
          <div><label className="label" htmlFor="mg-sug">{t("mg_suggest")}</label>
            <select id="mg-sug" className="field" value={sugDs} onChange={(e) => setSugDs(e.target.value)}>{names.map((n) => <option key={n}>{n}</option>)}</select></div>
          <button className="btn" onClick={suggest} disabled={!sugDs || !!busy}>{t("mg_suggest_btn")}</button>
        </div>
        {sugNote && <p className="help w-full" data-testid="mg-suggest-note">{t("mg_suggest_note")}</p>}
      </div>

      <div className="panel flex flex-col gap-3" data-testid="mg-editor">
        <div className="flex gap-2">
          <button className={"btn btn-sm " + (tab === "grid" ? "btn-primary" : "")} onClick={() => setTab("grid")}>{t("mg_grid")}</button>
          <button className={"btn btn-sm " + (tab === "json" ? "btn-primary" : "")} onClick={() => setTab("json")}>{t("mg_json")}</button>
        </div>
        {tab === "json" ? (
          <div>
            <textarea className="field mono" style={{ minHeight: 320 }} aria-label={t("mg_json")} value={jsonText} onChange={(e) => onJson(e.target.value)} />
            {jsonErr && <p className="font-semibold">{t("mg_json_bad")} {jsonErr}</p>}
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {spec.tables.map((tb, i) => (
              <div key={i} className="flex flex-col gap-2 border-t pt-3" data-testid={"mg-table-" + i}>
                <div className="flex flex-wrap items-end gap-3">
                  <div><label className="label">{t("mg_table")}</label><input className="field" value={tb.name} onChange={(e) => setTable(i, { name: e.target.value })} /></div>
                  <div><label className="label">{t("mg_source")}</label>
                    <select className="field" value={tb.source || ""} onChange={(e) => setTable(i, { source: e.target.value || undefined, source_sql: e.target.value ? undefined : tb.source_sql })}>
                      <option value="">—</option>{names.map((n) => <option key={n}>{n}</option>)}</select></div>
                  {!tb.source && <div className="grow"><label className="label">{t("mg_source_sql")}</label><input className="field mono" value={tb.source_sql || ""} onChange={(e) => setTable(i, { source_sql: e.target.value })} /></div>}
                  <div><label className="label">{t("mg_pk")}</label><input className="field" value={(tb.primary_key || []).join(", ")} onChange={(e) => setTable(i, { primary_key: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></div>
                  <button className="btn btn-sm" onClick={() => setSpec({ ...spec, tables: spec.tables.filter((_, j) => j !== i) })}>{t("mg_remove")}</button>
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-[13px]">
                    <thead><tr><th className="text-left">{t("mg_col")}</th><th className="text-left">{t("mg_type")}</th><th>{t("mg_required")}</th><th className="text-left">{t("mg_expr")}</th><th className="text-left">{t("mg_kind")}</th><th /></tr></thead>
                    <tbody>{tb.columns.map((c, k) => (
                      <tr key={k}>
                        <td><input className="field mono" value={c.name} onChange={(e) => setCol(i, k, { name: e.target.value })} /></td>
                        <td><select className="field" value={c.type} onChange={(e) => setCol(i, k, { type: e.target.value })}>{TYPES.map((x) => <option key={x}>{x}</option>)}</select></td>
                        <td className="text-center"><input type="checkbox" checked={!!c.required} onChange={(e) => setCol(i, k, { required: e.target.checked })} /></td>
                        <td><input className="field mono" value={c.expr || ""} onChange={(e) => setCol(i, k, { expr: e.target.value || undefined })} /></td>
                        <td><select className="field" value={c.kind || "representation"} onChange={(e) => setCol(i, k, { kind: e.target.value })}>
                          <option value="representation">{t("mg_k_representation")}</option><option value="correction">{t("mg_k_correction")}</option></select></td>
                        <td><button className="btn btn-sm" onClick={() => setTable(i, { columns: tb.columns.filter((_, j) => j !== k) })}>✕</button></td>
                      </tr>))}</tbody>
                  </table>
                </div>
                <button className="btn btn-sm self-start" onClick={() => setTable(i, { columns: [...tb.columns, { name: "", type: "TEXT", required: false, kind: "representation" }] })}>{t("mg_add_col")}</button>
              </div>
            ))}
            <button className="btn btn-sm self-start" onClick={() => setSpec({ ...spec, tables: [...spec.tables, { name: "", source: names[0], columns: [], primary_key: [] }] })}>{t("mg_add_table")}</button>
            <p className="help">{t("mg_more")}</p>
          </div>
        )}
        <div className="flex flex-wrap items-end gap-3 border-t pt-3">
          <div><label className="label" htmlFor="mg-rej">{t("mg_save_rejected")}</label><input id="mg-rej" className="field" value={saveRejected} onChange={(e) => setSaveRejected(e.target.value)} /></div>
          <button className="btn btn-primary" onClick={run} disabled={!spec.tables.length || !!jsonErr || !!busy}>{busy === "run" ? t("mg_working") : t("mg_run")}</button>
          <button className="btn" onClick={ddl} disabled={!spec.tables.length || !!jsonErr}>{t("mg_ddl")}</button>
        </div>
      </div>

      {result && (
        <div className="flex flex-col gap-4" data-testid="mg-result">
          <div className="panel flex flex-col gap-2" data-testid="mg-summary">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="font-semibold">{t("mg_summary")}</h2>
              <span className="num">{s.source_rows} {t("mg_s_source")} = {s.loaded} {t("mg_s_loaded")} + {s.rejected} {t("mg_s_rejected")}</span>
              <Chip tone={s.idempotent ? "ok" : "warn"}>{t("mg_idem")}: {s.idempotent ? t("mg_pass") : t("mg_fail")}</Chip>
              {s.flagged_columns > 0 && <Chip tone="warn">{s.flagged_columns} {t("mg_flagged")}</Chip>}
              <button className="btn btn-sm ml-auto" onClick={file}>{t("mg_file")}</button>
            </div>
            <p className={s.reconciled ? "help" : "font-semibold"}>{s.reconciled ? t("mg_reconciled") : t("mg_not_reconciled")}</p>
            <p className="help">{t("mg_order")}: <span className="mono">{result.load_order.join(" → ")}</span></p>
            {s.foreign_key_violations > 0 && <p className="font-semibold">{t("mg_fk_violations")}: {s.foreign_key_violations}</p>}
            {result.saved_rejected_dataset && <p>{t("mg_rejected_saved")}: <a className="mono" href={`#/datasets/${encodeURIComponent(result.saved_rejected_dataset)}`}>{result.saved_rejected_dataset}</a></p>}
          </div>

          {result.tables.map((tb) => (
            <div key={tb.table} className="panel flex flex-col gap-3" data-testid={"mg-t-" + tb.table}>
              <h2 className="font-semibold"><span className="mono">{tb.table}</span> · {tb.source_rows} {t("mg_s_source")} = {tb.loaded} {t("mg_s_loaded")} + {tb.rejected} {t("mg_s_rejected")} · {tb.target_rows} {t("mg_target")}</h2>
              {REASONS.filter((r) => tb.by_reason[r] > 0).map((r) => (
                <div key={r}>
                  <h3 className="font-medium">{t("mg_r_" + r)}: {tb.by_reason[r]}</h3>
                  <div className="overflow-x-auto"><table className="w-full text-[12px]">
                    <thead><tr><th className="text-left">{t("mg_row")}</th><th className="text-left">{t("mg_col")}</th><th className="text-left">{t("mg_value")}</th><th className="text-left">{t("mg_detail")}</th></tr></thead>
                    <tbody>{(tb.samples[r] || []).map((x, i) => <tr key={i}><td className="num">{x.row}</td><td className="mono">{x.column}</td><td className="mono">{blank(x.value)}</td><td>{x.detail}</td></tr>)}</tbody>
                  </table></div>
                </div>
              ))}
              <h3 className="font-medium">{t("mg_columns")}</h3>
              <div className="overflow-x-auto"><table className="w-full text-[13px]">
                <thead><tr><th className="text-left">{t("mg_col")}</th><th className="text-left">{t("mg_kind")}</th><th className="text-right">{t("mg_considered")}</th><th className="text-right">{t("mg_changed")}</th><th className="text-right">{t("mg_format_only")}</th><th className="text-left" /></tr></thead>
                <tbody>{tb.columns.map((c) => (
                  <tr key={c.column}><td className="mono">{c.column}</td><td>{t("mg_k_" + c.kind)}</td>
                    <td className="num text-right">{c.considered ?? "—"}</td>
                    <td className={"num text-right " + (c.flagged ? "font-semibold" : "")}>{c.changed ?? "—"}</td>
                    <td className="num text-right">{c.roundtrip_format_only ?? "—"}</td>
                    <td>{c.flagged ? <Chip tone="warn">{t("mg_flagged")}</Chip> : <span className="help">{c.note}</span>}</td></tr>))}</tbody>
              </table></div>
              {tb.columns.filter((c) => c.changed_examples && c.changed_examples.length).map((c) => (
                <div key={"ex" + c.column}>
                  <h3 className="font-medium mono">{c.column}</h3>
                  <table className="text-[12px]"><thead><tr><th className="text-left">{t("mg_row")}</th><th className="text-left">{t("mg_source_val")}</th><th className="text-left">{t("mg_target_val")}</th></tr></thead>
                    <tbody>{c.changed_examples.map((x, i) => <tr key={i}><td className="num">{x.row}</td><td className="mono pr-4">{blank(x.source)}</td><td className="mono">{blank(x.target)}</td></tr>)}</tbody></table>
                </div>
              ))}
            </div>
          ))}

          <div className="panel overflow-x-auto" data-testid="mg-idem">
            <h2 className="font-semibold mb-2">{t("mg_idem")}</h2>
            <table className="w-full text-[13px]">
              <thead><tr><th className="text-left">{t("mg_table")}</th><th className="text-right">{t("mg_before")}</th><th className="text-right">{t("mg_after")}</th><th className="text-right">{t("mg_dups")}</th><th className="text-left" /></tr></thead>
              <tbody>{result.idempotency.map((x) => (
                <tr key={x.table}><td className="mono">{x.table}</td>
                  <td className="num text-right">{x.rows_before ?? "—"}</td><td className="num text-right">{x.rows_after ?? "—"}</td><td className="num text-right">{x.duplicate_keys ?? "—"}</td>
                  <td>{x.provable ? <Chip tone={x.passed ? "ok" : "warn"}>{x.passed ? t("mg_idem_ok") : t("mg_idem_bad")}</Chip> : <span className="help">{t("mg_idem_na")}</span>}</td></tr>))}</tbody>
            </table>
          </div>

          {result.totals.length > 0 && (
            <div className="panel overflow-x-auto" data-testid="mg-totals">
              <h2 className="font-semibold mb-2">{t("mg_totals")}</h2>
              <table className="w-full text-[13px]">
                <thead><tr><th className="text-left">{t("mg_table")}</th><th className="text-left" /><th className="text-right">{t("mg_source_val")}</th><th className="text-right">{t("mg_target_val")}</th><th className="text-right">{t("mg_diff")}</th><th /></tr></thead>
                <tbody>{result.totals.map((x, i) => (
                  <tr key={i}><td className="mono">{x.name}</td><td className="mono">{x.source_expr}</td><td className="num text-right">{blank(x.source)}</td><td className="num text-right">{blank(x.target)}</td>
                    <td className="num text-right">{blank(x.difference)}</td><td><Chip tone={x.passed ? "ok" : "warn"}>{x.passed ? t("mg_pass") : (x.error || t("mg_fail"))}</Chip></td></tr>))}</tbody>
              </table>
            </div>
          )}

          {result.queries.length > 0 && (
            <div className="panel flex flex-col gap-2" data-testid="mg-queries">
              <h2 className="font-semibold">{t("mg_queries")}</h2>
              {result.queries.map((x, i) => (
                <div key={i}>
                  <div className="flex items-center gap-2"><span className="mono">{x.name}</span><Chip tone={x.passed ? "ok" : "warn"}>{x.passed ? t("mg_pass") : (x.error || t("mg_fail"))}</Chip></div>
                  {!x.passed && !x.error && (
                    <p className="help mono">{t("mg_only_source")}: {JSON.stringify(x.only_in_source)} · {t("mg_only_target")}: {JSON.stringify(x.only_in_target)}</p>)}
                </div>
              ))}
            </div>
          )}

          <div className="panel"><h2 className="font-semibold mb-1">{t("mg_how")}</h2>
            <ul className="help list-disc pl-5">{result.explanation.map((e, i) => <li key={i}>{e}</li>)}</ul></div>
        </div>
      )}
    </div>
  );
}
