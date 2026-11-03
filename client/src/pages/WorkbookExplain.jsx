import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Chip } from "../components/ui.jsx";

const fmt = (v) => {
  if (v && typeof v === "object" && v.error) return v.error;
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Math.round(v * 1e6) / 1e6);
  if (typeof v === "boolean") return v ? "TRUE" : "FALSE";
  return v ?? "";
};
const pct = (x) => (x == null ? "" : `${(x * 100).toFixed(1)}%`);

function Tornado({ target }) {
  const { t } = useApp();
  const ranked = target.rows.filter((r) => r.status === "ok");
  const failed = target.rows.filter((r) => r.status !== "ok");
  const max = Math.max(...ranked.map((r) => r.swing), 0) || 1;
  return (
    <div className="flex flex-col gap-1" data-testid="wbx-tornado">
      <div className="font-medium">{target.label || target.output} <span className="help mono">{target.output} = {fmt(target.base)}</span></div>
      {target.status !== "ok" && <p className="help">{target.note}</p>}
      {target.dominant && <p className="help">{t("wbx_dominant")}: {target.dominant.label || target.dominant.parameter} ({pct(target.dominant.share)})</p>}
      {ranked.map((r) => (
        <div key={r.parameter} className="flex items-center gap-2 text-[13px]" title={`${t("wbx_out_range")}: ${fmt(r.output_low)} → ${fmt(r.output_high)}`}>
          <span style={{ width: 150 }} className="truncate">{r.label || r.parameter}</span>
          <div className="grow" style={{ background: "var(--soft)", borderRadius: 4, height: 14 }}>
            <div style={{ width: `${(r.swing / max) * 100}%`, background: "var(--accent)", height: "100%", borderRadius: 4, minWidth: 2 }} />
          </div>
          <span className="num mono help" style={{ width: 200, textAlign: "right" }}>{fmt(r.low)} / {fmt(r.high)} → {fmt(r.swing)} · {pct(r.share)}</span>
        </div>
      ))}
      {failed.length > 0 && (
        <p className="help">{t("wbx_not_ranked")}: {failed.map((r) => `${r.label || r.parameter} (${r.status})`).join(", ")}</p>
      )}
      {target.no_effect?.length > 0 && <p className="help">{t("wbx_no_effect")}: {target.no_effect.join(", ")}</p>}
    </div>
  );
}

function Sensitivity({ id, results }) {
  const { t, act } = useApp();
  const [output, setOutput] = useState("");
  const [delta, setDelta] = useState(10);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const run = () => {
    setBusy(true);
    act(() => api.workbookSensitivity(id, { output: output || null, delta: (Number(delta) || 10) / 100 }))
      .then(setData).catch(() => {}).finally(() => setBusy(false));
  };
  return (
    <div className="panel flex flex-col gap-3" data-testid="wbx-sensitivity">
      <h2 className="font-semibold">{t("wbx_sens_title")}</h2>
      <p className="help">{t("wbx_sens_help")}</p>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="label" htmlFor="wbx-output">{t("wbx_output")}</label>
          <select id="wbx-output" className="field" value={output} onChange={(e) => setOutput(e.target.value)}>
            <option value="">{t("wbx_all_results")}</option>
            {results.map((r) => <option key={r.cell} value={r.cell}>{r.label || r.cell}</option>)}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="wbx-delta">{t("wbx_delta")}</label>
          <input id="wbx-delta" className="field num" style={{ width: 90 }} type="number" min="0.1" step="any" value={delta} onChange={(e) => setDelta(e.target.value)} />
        </div>
        <button className="btn" onClick={run} disabled={busy}>{busy ? t("wb_working") : t("wbx_run")}</button>
      </div>
      {data?.targets.map((tg) => <Tornado key={tg.output} target={tg} />)}
      {data?.skipped.length > 0 && (
        <p className="help">{t("wbx_skipped")}: {data.skipped.map((s) => `${s.label || s.parameter} (${s.reason})`).join("; ")}</p>
      )}
    </div>
  );
}

function parseField(p, text) {
  if (typeof p.value === "number") return Number(text);
  if (typeof p.value === "boolean") return /^(true|1|yes|sí|si)$/i.test(text);
  return text;
}

function Scenarios({ id, params }) {
  const { t, act } = useApp();
  const [list, setList] = useState([]);
  const [name, setName] = useState("");
  const [fields, setFields] = useState({});
  const [cmp, setCmp] = useState(null);
  const refresh = () => api.workbookScenarios(id).then((r) => setList(r.scenarios)).catch(() => {});
  useEffect(() => { setName(""); setFields({}); setCmp(null); refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [id]);
  const save = () => {
    const inputs = {};
    for (const p of params) {
      const text = fields[p.cell];
      if (text !== undefined && text !== "" && !(typeof p.value === "number" && Number.isNaN(Number(text)))) inputs[p.cell] = parseField(p, String(text));
    }
    act(() => api.workbookScenarioSave(id, name, inputs), t("wbx_sc_saved")).then(() => { refresh(); setCmp(null); }).catch(() => {});
  };
  const edit = (sc) => {
    setName(sc.name);
    setFields(Object.fromEntries(Object.entries(sc.inputs).map(([k, v]) => [k, String(v)])));
  };
  const remove = (n) => act(() => api.workbookScenarioDelete(id, n), t("wbx_sc_deleted")).then(() => { refresh(); setCmp(null); }).catch(() => {});
  const compare = () => act(() => api.workbookScenarioCompare(id, null)).then(setCmp).catch(() => {});
  return (
    <div className="panel flex flex-col gap-3" data-testid="wbx-scenarios">
      <h2 className="font-semibold">{t("wbx_sc_title")}</h2>
      <p className="help">{t("wbx_sc_help")}</p>
      {list.length === 0 && <p className="help">{t("wbx_sc_none")}</p>}
      {list.map((sc) => (
        <div key={sc.name} className="flex flex-wrap items-center gap-2 text-[13px]">
          <span className="font-medium">{sc.name}</span>
          <span className="help mono grow">{Object.entries(sc.inputs).map(([k, v]) => `${k} = ${fmt(v)}`).join(" · ")}</span>
          <button className="btn btn-sm" onClick={() => edit(sc)}>{t("wbx_sc_edit")}</button>
          <button className="btn btn-sm" onClick={() => remove(sc.name)}>{t("wb_delete")}</button>
        </div>
      ))}
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="label" htmlFor="wbx-sc-name">{t("wbx_sc_name")}</label>
          <input id="wbx-sc-name" className="field" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <button className="btn" onClick={save} disabled={!name.trim()}>{t("wbx_sc_save")}</button>
        <button className="btn" onClick={compare} disabled={list.length === 0}>{t("wbx_sc_compare")}</button>
      </div>
      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))" }}>
        {params.map((p) => (
          <div key={p.cell}>
            <label className="label" htmlFor={`wbx-f-${p.cell}`}>{p.label || p.cell}</label>
            <input id={`wbx-f-${p.cell}`} className="field" value={fields[p.cell] ?? ""} placeholder={String(fmt(p.value))}
                   onChange={(e) => setFields({ ...fields, [p.cell]: e.target.value })} />
          </div>
        ))}
      </div>
      {cmp && (
        <div className="overflow-auto">
          {Object.entries(cmp.failed || {}).map(([n, m]) => <p key={n} className="help">{n}: {m}</p>)}
          <table className="w-full text-[13px]" data-testid="wbx-compare">
            <thead><tr><th className="text-left">{t("wb_result")}</th>{cmp.columns.map((c) => <th key={c} className="text-right">{c === "Base" ? t("wbx_base") : c}</th>)}</tr></thead>
            <tbody>
              {cmp.rows.map((r) => (
                <tr key={r.cell}>
                  <td>{r.label || r.cell}</td>
                  {cmp.columns.map((c) => {
                    const v = r.values[c];
                    return (
                      <td key={c} className="num text-right">{fmt(v.value)}
                        {v.delta != null && v.delta !== 0 && <span className="help"> {v.delta > 0 ? "+" : ""}{fmt(v.delta)}{v.delta_pct != null ? ` (${v.delta > 0 ? "+" : ""}${pct(v.delta_pct)})` : ""}</span>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Units({ id, params }) {
  const { t, act } = useApp();
  const [report, setReport] = useState(null);
  const [draft, setDraft] = useState({});
  useEffect(() => {
    setReport(null); setDraft({});
    api.workbookUnits(id).then((r) => { setReport(r); setDraft(r.declared || {}); }).catch(() => {});
  }, [id]);
  const save = () => {
    const units = {};
    const declared = report?.declared || {};
    for (const p of params) {
      const v = (draft[p.cell] || "").trim();
      if (v !== (declared[p.cell] || "")) units[p.cell] = v || null;
    }
    act(() => api.workbookUnitsSave(id, units), t("wbx_units_saved")).then((r) => { setReport(r); setDraft(r.declared || {}); }).catch(() => {});
  };
  const fromFormat = Object.fromEntries((report?.inputs || []).filter((i) => i.source === "format").map((i) => [i.cell, i.unit]));
  return (
    <div className="panel flex flex-col gap-3" data-testid="wbx-units">
      <h2 className="font-semibold">{t("wbx_units_title")}</h2>
      <p className="help">{t("wbx_units_help")}</p>
      <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))" }}>
        {params.map((p) => (
          <div key={p.cell}>
            <label className="label" htmlFor={`wbx-u-${p.cell}`}>{p.label || p.cell}</label>
            <input id={`wbx-u-${p.cell}`} className="field" value={draft[p.cell] ?? ""} placeholder={fromFormat[p.cell] || t("wbx_unit")}
                   onChange={(e) => setDraft({ ...draft, [p.cell]: e.target.value })} />
          </div>
        ))}
      </div>
      <div><button className="btn" onClick={save}>{t("wbx_units_save")}</button></div>
      {report && (
        <>
          <h3 className="font-medium">{t("wbx_unit_errors")}</h3>
          {report.errors.length === 0 ? <p className="help">{t("wbx_unit_none")}</p> : report.errors.map((e, i) => (
            <div key={i} className="text-[13px]" data-testid="wbx-unit-error">
              <Chip tone="danger">{e.cell}</Chip> <span className="mono">{e.formula}</span>
              <div className="help">{e.message} ({e.left_unit} / {e.right_unit})</div>
            </div>
          ))}
          <h3 className="font-medium">{t("wbx_inferred")}</h3>
          {report.cells.length > 0 && (
            <table className="w-full text-[13px]">
              <thead><tr><th className="text-left">{t("wb_result")}</th><th className="text-left">{t("wb_cell")}</th><th className="text-left">{t("wbx_unit")}</th><th className="text-left">{t("wbx_source")}</th></tr></thead>
              <tbody>
                {report.cells.map((c) => (
                  <tr key={c.cell}><td>{c.label || "—"}</td><td className="mono help" title={c.formula}>{c.cell}</td><td className="mono">{c.unit}</td><td className="help">{c.source}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="help">{t("wbx_unit_unknown")}: {report.unit_unknown} / {report.formula_cells}</p>
        </>
      )}
    </div>
  );
}

export default function WorkbookExplain({ id, params, results }) {
  return (
    <>
      <Sensitivity id={id} results={results} />
      <Scenarios id={id} params={params} />
      <Units id={id} params={params} />
    </>
  );
}
