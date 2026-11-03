import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { EmptyState, Chip } from "../components/ui.jsx";
import WorkbookExplain from "./WorkbookExplain.jsx";
import { useCopilotActive, useCopilotRefresh } from "../copilot-hooks.js";

const EPOCH = Date.UTC(1899, 11, 30);
const dateEpoch = (dateSystem) => dateSystem === "1904" ? Date.UTC(1904, 0, 1) : EPOCH;
const serialToISO = (n, dateSystem) => new Date(dateEpoch(dateSystem) + Math.round(n * 86400000)).toISOString().slice(0, 10);
const isoToSerial = (s, dateSystem) => (Date.parse(s + "T00:00:00Z") - dateEpoch(dateSystem)) / 86400000;

function show(item) {
  if (item.display) return item.display;
  const v = item.value;
  if (v && typeof v === "object" && v.error) return v.error;
  if (typeof v === "boolean") return v ? "TRUE" : "FALSE";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Math.round(v * 1e10) / 1e10);
  return v ?? "";
}

function ParamInput({ p, value, onChange, dateSystem }) {
  const id = `wb-${p.cell}`;
  if (p.options) {
    return (
      <select id={id} className="field" value={String(value ?? "")} onChange={(e) => {
        const opt = p.options.find((o) => String(o) === e.target.value);
        onChange(opt);
      }}>
        {p.options.map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
      </select>
    );
  }
  if (typeof p.value === "boolean") {
    return <input id={id} type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />;
  }
  if (p.display && typeof p.value === "number") {
    return <input id={id} type="date" className="field" value={value == null ? "" : serialToISO(value, dateSystem)} onChange={(e) => onChange(e.target.value ? isoToSerial(e.target.value, dateSystem) : null)} />;
  }
  if (typeof p.value === "number") {
    return <input id={id} type="number" step="any" className="field num" value={value ?? ""} onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />;
  }
  return <input id={id} className="field" value={value ?? ""} onChange={(e) => onChange(e.target.value)} />;
}

export default function WorkbooksPage({ param }) {
  const { t, act } = useApp();
  const [list, setList] = useState([]);
  const [engines, setEngines] = useState([]);
  const [selected, setSelected] = useState(param || null);
  const [meta, setMeta] = useState(null);
  const [values, setValues] = useState({});
  const [result, setResult] = useState(null);
  const [check, setCheck] = useState(null);
  const [verify, setVerify] = useState(null);
  const [trace, setTrace] = useState(null);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState("");
  const [engine, setEngine] = useState("");
  const [cases, setCases] = useState(12);

  const load = async () => {
    const r = await api.workbooks();
    setList(r.workbooks);
    setEngines(r.engines);
    if (!engine && r.engines.length) setEngine(r.engines[0]);
  };
  useEffect(() => { load().catch(() => {}); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);
  useCopilotRefresh(() => load().catch(() => {}));
  useCopilotActive("workbook", selected);

  useEffect(() => {
    setResult(null); setCheck(null); setVerify(null); setTrace(null);
    if (!selected) { setMeta(null); return; }
    api.workbook(selected).then((m) => {
      setMeta(m);
      setValues(Object.fromEntries(m.summary.parameters.map((p) => [p.cell, p.value])));
      api.workbookEvaluate(selected, {}).then(setResult).catch(() => {});
    }).catch(() => setSelected(null));
  }, [selected]);

  const params = meta?.summary.parameters || [];
  const changed = useMemo(() => Object.fromEntries(params.filter((p) => values[p.cell] !== p.value).map((p) => [p.cell, values[p.cell]])), [params, values]);

  const run = async (label, fn) => {
    setBusy(label);
    try { return await fn(); } finally { setBusy(""); }
  };
  const compilePath = (e) => {
    e.preventDefault();
    run("compile", () => act(() => api.workbookCompile({ path }), t("wb_compiled"))).then((m) => { setPath(""); load(); setSelected(m.id); }).catch(() => {});
  };
  const upload = (file) => {
    if (!file) return;
    run("compile", () => act(() => api.workbookUpload(file), t("wb_compiled"))).then((m) => { load(); setSelected(m.id); }).catch(() => {});
  };
  const evaluate = () => run("evaluate", () => act(() => api.workbookEvaluate(selected, changed))).then(setResult).catch(() => {});
  const doCheck = () => run("check", () => act(() => api.workbookCheck(selected))).then((r) => { setCheck(r); load(); }).catch(() => {});
  const doVerify = () => run("verify", () => act(() => api.workbookVerify(selected, { engine: engine || undefined, n: Number(cases) || 12 }))).then((r) => { setVerify(r); load(); }).catch(() => {});
  const doTrace = (cell) => act(() => api.workbookTrace(selected, cell)).then(setTrace).catch(() => {});
  const remove = () => act(() => api.workbookDelete(selected), t("wb_deleted")).then(() => { setSelected(null); load(); }).catch(() => {});

  const s = meta?.summary;
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">{t("wb_title")}</h1>
        <p className="help mt-1">{t("wb_intro")}</p>
      </div>

      <div className="panel flex flex-col gap-3" data-testid="wb-compile">
        <form onSubmit={compilePath} className="flex flex-wrap items-end gap-3">
          <div className="grow" style={{ minWidth: 260 }}>
            <label className="label" htmlFor="wb-path">{t("wb_path")}</label>
            <input id="wb-path" className="field" value={path} onChange={(e) => setPath(e.target.value)} placeholder={"C:\\ruta\\cotizacion.xlsx"} />
          </div>
          <button className="btn" type="submit" disabled={!path || !!busy}>{busy === "compile" ? t("wb_working") : t("wb_compile")}</button>
          <label className="btn">
            {t("wb_upload")}
            <input type="file" accept=".xlsx,.xlsm" hidden onChange={(e) => upload(e.target.files?.[0])} data-testid="wb-upload" />
          </label>
        </form>
      </div>

      {list.length === 0 ? <EmptyState>{t("wb_empty")}</EmptyState> : (
        <div className="panel-white divide-y" style={{ borderColor: "var(--line)" }} data-testid="wb-list">
          {list.map((w) => (
            <div key={w.id} className="row cursor-pointer flex-wrap" aria-current={w.id === selected} onClick={() => setSelected(w.id)}
                 style={w.id === selected ? { background: "var(--soft)" } : undefined}>
              <span className="font-medium">{w.name}</span>
              <Chip>{w.executed}/{w.formulas} {t("wb_formulas")}</Chip>
              {w.frozen > 0 && <Chip tone="danger">{w.frozen} {t("wb_frozen")}</Chip>}
              <Chip>{w.parameters} {t("wb_parameters")}</Chip>
              <Chip>{w.results} {t("wb_results")}</Chip>
              {w.last_check?.compared > 0 && <Chip tone={w.last_check.agree === w.last_check.compared ? "ok" : "danger"}>
                {w.last_check.kind}: {w.last_check.agree}/{w.last_check.compared}</Chip>}
              <span className="help ml-auto">{w.created}</span>
            </div>
          ))}
        </div>
      )}

      {meta && (
        <div className="grid gap-6" style={{ gridTemplateColumns: "minmax(280px, 1fr) minmax(320px, 2fr)" }}>
          <div className="panel flex flex-col gap-3" data-testid="wb-params">
            <h2 className="font-semibold">{t("wb_parameters_title")}</h2>
            {params.length === 0 && <p className="help">{t("wb_no_params")}</p>}
            {params.map((p) => (
              <div key={p.cell}>
                <label className="label" htmlFor={`wb-${p.cell}`}>{p.label || p.cell} <span className="help mono">{p.cell}</span></label>
                <ParamInput p={p} value={values[p.cell]} dateSystem={s.date_system} onChange={(v) => setValues({ ...values, [p.cell]: v })} />
              </div>
            ))}
            <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary" onClick={evaluate} disabled={!!busy}>{busy === "evaluate" ? t("wb_working") : t("wb_calculate")}</button>
              <button className="btn btn-sm" onClick={() => setValues(Object.fromEntries(params.map((p) => [p.cell, p.value])))}>{t("wb_reset")}</button>
            </div>
          </div>

          <div className="flex flex-col gap-4">
            <div className="panel flex flex-col gap-3" data-testid="wb-results">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="font-semibold mr-auto">{t("wb_results_title")}</h2>
                <a className="btn btn-sm" href={`api/sheets/${selected}/export.py`} download>{t("wb_export")}</a>
                <button className="btn btn-sm" onClick={remove}>{t("wb_delete")}</button>
              </div>
              <table className="w-full text-[13px]">
                <thead><tr><th className="text-left">{t("wb_result")}</th><th className="text-left">{t("wb_cell")}</th><th className="text-right">{t("wb_value")}</th><th /></tr></thead>
                <tbody>
                  {(result?.results || s.results).map((r) => (
                    <tr key={r.cell}>
                      <td>{r.label || "—"}</td>
                      <td className="mono help" title={r.formula}>{r.cell}</td>
                      <td className="num text-right">{show(r)}{r.uses_frozen && <Chip tone="danger">{t("wb_uses_frozen")}</Chip>}</td>
                      <td className="text-right"><button className="btn btn-sm" onClick={() => doTrace(r.cell)}>{t("wb_trace")}</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {result && Object.keys(result.inputs).length > 0 && <p className="help">{t("wb_changed")}: {Object.entries(result.inputs).map(([k, v]) => `${k} = ${JSON.stringify(v)}`).join(" · ")}</p>}
            </div>

            {trace && (
              <div className="panel flex flex-col gap-2" data-testid="wb-trace">
                <h2 className="font-semibold">{t("wb_trace_title")} {trace.label || trace.cell}</h2>
                <p className="help">{t("wb_depends_on")}: {trace.parameters.map((p) => p.label || p.cell).join(", ") || "—"}</p>
                <ol className="mono text-[12px] flex flex-col gap-1">
                  {trace.formulas.map((f) => <li key={f.cell}>{f.cell} {f.label ? `(${f.label})` : ""} {f.formula}{f.frozen ? ` — ${t("wb_frozen_one")}: ${f.frozen}` : ""}</li>)}
                </ol>
              </div>
            )}

            <div className="panel flex flex-col gap-3" data-testid="wb-verify">
              <h2 className="font-semibold">{t("wb_verify_title")}</h2>
              <div className="flex flex-wrap items-end gap-3">
                <button className="btn" onClick={doCheck} disabled={!!busy}>{busy === "check" ? t("wb_working") : t("wb_check")}</button>
                {engines.length > 0 ? (
                  <>
                    <select className="field" style={{ width: "auto" }} value={engine} onChange={(e) => setEngine(e.target.value)}>
                      {engines.map((e) => <option key={e} value={e}>{e === "excel" ? "Excel" : "LibreOffice"}</option>)}
                    </select>
                    <input className="field num" style={{ width: 90 }} type="number" min="1" max="200" value={cases} onChange={(e) => setCases(e.target.value)} aria-label={t("wb_cases")} />
                    <button className="btn" onClick={doVerify} disabled={!!busy}>{busy === "verify" ? t("wb_working") : t("wb_verify")}</button>
                  </>
                ) : <span className="help">{t("wb_no_engine")}</span>}
              </div>
              {check && <p>{t("wb_check_result")}: {check.agree}/{check.compared}{check.difference_count > 0 && ` — ${check.difference_count} ${t("wb_differences")}`}</p>}
              {check?.differences?.length > 0 && <pre className="mono text-[11px] overflow-auto">{JSON.stringify(check.differences, null, 1)}</pre>}
              {verify && (verify.status === "agree" || verify.status === "differences"
                ? <p>{verify.engine === "excel" ? "Excel" : "LibreOffice"} · {verify.cases} {t("wb_cases")} · {t("wb_verify_result")}: {verify.agree}/{verify.compared}</p>
                : <p className="help">{verify.reason}</p>)}
              {verify?.report?.filter((r) => r.differences?.length || r.error).map((r) => (
                <pre key={r.case} className="mono text-[11px] overflow-auto">{JSON.stringify(r, null, 1)}</pre>
              ))}
              {verify?.note && <p className="help">{verify.note}</p>}
            </div>

            {(s.frozen.length > 0 || s.cycles.length > 0 || s.volatile.length > 0 || s.diagnostics.length > 0) && (
              <div className="panel flex flex-col gap-2" data-testid="wb-frozen">
                <h2 className="font-semibold">{t("wb_frozen_title")}</h2>
                {s.frozen.map((f) => <div key={f.cell} className="mono text-[12px]">{f.cell} {f.formula} — {f.reason}</div>)}
                {s.volatile.length > 0 && <p className="help">{t("wb_volatile")}: {s.volatile.join(", ")}</p>}
                {s.diagnostics.map((d, i) => <p key={i} className="help">{d.message}</p>)}
              </div>
            )}
            <WorkbookExplain id={selected} params={params} results={result?.results || s.results} />
            <p className="help">{t("wb_functions")}: {s.functions.join(", ")} · {s.tables} {t("wb_table_cells")}</p>
          </div>
        </div>
      )}
    </div>
  );
}
