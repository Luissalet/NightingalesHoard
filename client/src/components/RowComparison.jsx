import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { formatNumber } from "../format.js";

export default function RowComparison({ dataset, a, b, t, lang }) {
  const [result, setResult] = useState(null);
  const [columns, setColumns] = useState([]);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const request = useRef(0);
  const number = value => formatNumber(value, lang);
  async function load(selected = null, offsetA = 0, offsetB = 0) {
    const id = ++request.current;
    setLoading(true); setError(null);
    try {
      const out = await api.compareRows(dataset, {version_a:a, version_b:b, columns:selected, offset_a:offsetA, offset_b:offsetB, limit:25});
      if (id !== request.current) return;
      setResult(out); setColumns(out.compared_columns); setDirty(false);
    } catch (cause) { if (id === request.current) setError(cause.message); }
    finally { if (id === request.current) setLoading(false); }
  }
  useEffect(() => { load(); return () => { request.current++; }; }, [dataset, a, b]);
  function toggle(name) {
    setColumns(previous => previous.includes(name) ? previous.filter(item => item !== name) : [...previous, name]);
    setDirty(true);
  }
  function table(side, label) {
    const page = result[side];
    return <section className="row-diff-side" aria-label={label}>
      <h4 className="font-semibold">{label} <span className="num">· {number(page.count)}</span></h4>
      {page.count ? <>
        <div className="row-diff-scroll" tabIndex={0} role="region" aria-label={label}>
          <table className="table row-diff-table"><thead><tr>{result.compared_columns.map(name => <th key={name} scope="col">{name}</th>)}</tr></thead>
            <tbody>{page.rows.map((row, index) => <tr key={page.offset + index}>{result.compared_columns.map(name => <td key={name}>{row[name] === null ? <span className="help">NULL</span> : row[name]}</td>)}</tr>)}</tbody>
          </table>
        </div>
        <div className="flex flex-wrap items-center gap-3 mt-3">
          <button type="button" className="btn" disabled={!page.offset || loading} onClick={() => load(columns, side === "only_a" ? Math.max(0,page.offset-25) : result.only_a.offset, side === "only_b" ? Math.max(0,page.offset-25) : result.only_b.offset)}>{t("rows_previous")}</button>
          <span className="help num">{number(page.offset+1)}–{number(Math.min(page.offset+page.rows.length,page.count))} / {number(page.count)}</span>
          <button type="button" className="btn" disabled={page.next_offset === null || loading} onClick={() => load(columns, side === "only_a" ? page.next_offset : result.only_a.offset, side === "only_b" ? page.next_offset : result.only_b.offset)}>{t("rows_next")}</button>
        </div>
      </> : <p className="help mt-2">{t("rows_empty")}</p>}
    </section>;
  }
  return <div className="min-w-0" aria-busy={loading}>
    <p className="help mb-3">{t("rows_help")}</p>
    {result && <fieldset className="row-diff-columns" disabled={loading}><legend>{t("rows_columns")}</legend>
      <div className="flex flex-wrap gap-3">{result.available_columns.map(column => <label className="help flex items-center gap-2" key={column.name}><input type="checkbox" checked={columns.includes(column.name)} onChange={() => toggle(column.name)} />{column.name} <span className="num">{column.type}</span></label>)}</div>
      <button type="button" className="btn mt-3" onClick={() => load(columns)}>{t("rows_apply")}</button>
    </fieldset>}
    {error && <p role="alert" className="help" style={{color:"var(--danger-ink)"}}>{error} <button type="button" className="btn" onClick={() => load(result ? columns : null)}>{t("rows_retry")}</button></p>}
    {loading && <p role="status" className="help my-3">{t("common_loading")}</p>}
    {dirty && <p role="status" className="help my-3">{t("rows_pending")}</p>}
    {result && !dirty && !loading && <>
      {result.omitted_columns.length > 0 && <details className="my-3"><summary className="help cursor-pointer">{t("rows_omitted")} · {result.omitted_columns.length}</summary><ul className="help mt-2">{result.omitted_columns.map(column => <li key={column.name}><strong>{column.name}</strong> · {t(`rows_${column.reason}`)} ({column.type_a || "—"} → {column.type_b || "—"})</li>)}</ul></details>}
      {result.status === "no_comparable_columns" ? <p className="help my-3">{t("rows_none")}</p> : <>
        <p className="my-4 num">{t("rows_common")}: <strong>{number(result.common_count)}</strong> · {t("rows_scope")}: <strong>{result.compared_columns.join(", ")}</strong></p>
        <div className="row-diff-grid">{table("only_a", `${t("rows_only_a")} · v${a}`)}{table("only_b", `${t("rows_only_b")} · v${b}`)}</div>
        <p className="help mt-4">{t("rows_preview").replace("{limit}", result.cell_char_limit)}</p>
      </>}
    </>}
  </div>;
}
