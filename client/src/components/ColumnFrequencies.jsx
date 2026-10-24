import React, { useEffect, useId, useRef, useState } from "react";
import { api } from "../api.js";
import { formatNumber } from "../format.js";
import { useCopilotForm } from "../copilot-hooks.js";

export default function ColumnFrequencies({ dataset, version, columns, lang }) {
  const es = lang === "es", id = useId();
  const initialColumn = columns[0]?.name || "";
  const [column, setColumn] = useState(initialColumn);
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [failedOffset, setFailedOffset] = useState(0);
  const [open, setOpen] = useState(false);
  const request = useRef(0);
  useEffect(() => () => { request.current++; }, []);
  const applied = result ? { dataset: result.dataset, version: result.version, column: result.column,
    shown: result.rows.length, value_count: result.value_count, row_count: result.row_count,
    next_offset: result.next_offset, value_format: result.value_format } : null;
  // The shared collector ignores unnamed snapshots from a collapsed section.
  useCopilotForm(open ? "column-frequencies" : null, { objectId: dataset,
    initial: { column: initialColumn, version, applied }, current: { column, version, applied } });
  const number = value => formatNumber(value, lang);
  function clear() {
    request.current++; setResult(null); setError(null); setLoading(false); setFailedOffset(0);
  }
  async function load(offset = 0) {
    const generation = ++request.current;
    setLoading(true); setError(null); setFailedOffset(offset);
    if (!offset) setResult(null);
    try {
      const out = await api.frequencies(dataset, column, version, offset, 50);
      if (generation !== request.current) return;
      setResult(previous => ({ ...out, rows: offset && previous ? [...previous.rows, ...out.rows] : out.rows }));
    } catch (cause) { if (generation === request.current) setError(cause.message); }
    finally { if (generation === request.current) setLoading(false); }
  }
  return <details className="panel-white min-w-0" onToggle={event => { setOpen(event.currentTarget.open); if (!event.currentTarget.open) clear(); }}>
    <summary className="font-semibold cursor-pointer">{es ? "Frecuencias por columna" : "Column frequencies"}</summary>
    <p className="help mt-3 mb-3">{es ? "Recuentos de todos los valores de la versión guardada, incluidos los nulos. Consultar no transforma los datos." : "Counts of all values in the saved version, including nulls. Reading does not transform the data."}</p>
    <form className="flex flex-wrap items-end gap-3" onSubmit={event => { event.preventDefault(); void load(); }}>
      <label className="block min-w-0 flex-1" htmlFor={id}><span className="label">{es ? "Columna" : "Column"}</span>
        <select id={id} className="field w-full" value={column} onChange={event => { clear(); setColumn(event.target.value); }}>
          {columns.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
        </select>
      </label>
      <button type="submit" className="btn" disabled={loading || !column}>{es ? "Consultar frecuencias" : "Read frequencies"}</button>
    </form>
    <p className="help mt-2 break-words">{dataset} · v{version}</p>
    {loading && <p className="help my-3" role="status">{es ? "Leyendo frecuencias…" : "Reading frequencies…"}</p>}
    {error && <p role="alert" className="help my-3" style={{ color: "var(--danger-ink)" }}>{error} <button type="button" className="btn" disabled={loading} onClick={() => load(failedOffset)}>{es ? "Reintentar" : "Retry"}</button></p>}
    {result && <>
      <p className="help my-3 break-words" role="status">{result.column} · v{result.version} · {es ? `${number(result.rows.length)} de ${number(result.value_count)} valores · ${number(result.row_count)} filas` : `${number(result.rows.length)} of ${number(result.value_count)} values · ${number(result.row_count)} rows`}</p>
      {result.rows.length ? <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={es ? "Tabla de frecuencias" : "Frequency table"}>
        <table className="table w-full" style={{ tableLayout: "fixed" }}><caption className="help text-left mb-2">{es ? "Orden: recuento descendente, valor ascendente. % sobre todas las filas, no sobre esta página." : "Order: count descending, value ascending. % of all rows, not this page."}</caption>
          <thead><tr><th style={{ width: "55%" }}>{es ? "Valor" : "Value"}</th><th>{es ? "Recuento" : "Count"}</th><th>%</th></tr></thead>
          <tbody>{result.rows.map((row, index) => <tr key={index}>
            <td style={{ overflowWrap: "anywhere", whiteSpace: "pre-wrap" }}>{row.is_null ? <em>NULL · {es ? "nulo" : "null"}</em> : row.value === "" ? <em>{es ? "Cadena vacía" : "Empty string"}</em> : <span>{row.value}</span>}</td>
            <td className="num">{number(row.count)}</td><td className="num">{formatNumber(row.percent, lang, { maximumFractionDigits: 2 })}</td>
          </tr>)}</tbody>
        </table>
      </div> : <p className="help my-3">{es ? "Esta versión no tiene filas." : "This version has no rows."}</p>}
      <p className="help my-3">{es ? "Los valores se muestran como texto de DuckDB, sin recortar. NULL se distingue del texto escrito. Los porcentajes se redondean para mostrarlos." : "Values use untruncated DuckDB text. NULL is distinct from literal text. Percentages are rounded for display."}</p>
      {result.has_more && <button type="button" className="btn" disabled={loading} onClick={() => load(result.next_offset)}>{es ? "Cargar más valores" : "Load more values"}</button>}
    </>}
  </details>;
}
