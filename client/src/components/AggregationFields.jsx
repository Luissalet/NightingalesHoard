import React, { useId } from "react";

const FUNCTIONS = ["sum", "avg", "min", "max", "count", "count_distinct", "median", "stddev"];

export function parseGroupColumns(raw) {
  if (Array.isArray(raw)) return raw;
  const text = (raw || "").trim();
  if (text.startsWith("[")) return JSON.parse(text);
  return text.split(",").map((name) => name.trim()).filter(Boolean);
}

export function GroupColumnsField({ value, columnNames, onChange, t }) {
  const id = useId();
  let selected;
  try {
    const parsed = parseGroupColumns(value);
    if (Array.isArray(parsed) && parsed.every((name) => typeof name === "string")) selected = parsed;
  } catch { /* Keep incomplete manual input editable. */ }
  const options = [...new Set([...columnNames, ...(selected || [])])];
  return (
    <fieldset className="min-w-0 flex flex-col gap-2 md:col-span-2">
      <legend className="label">{t("aggregate_group")}</legend>
      <p className="help">{t("aggregate_group_help")}</p>
      {selected && <div className="flex flex-wrap gap-x-4 gap-y-2">
        {options.map((name) => <label key={name} className="flex min-w-0 items-center gap-2 break-all">
          <input type="checkbox" checked={selected.includes(name)} onChange={(e) => onChange(JSON.stringify(e.target.checked ? [...selected, name] : selected.filter((item) => item !== name)))} />
          <span>{name}</span>
        </label>)}
      </div>}
      <label className="label" htmlFor={id}>{t("aggregate_group_manual")}</label>
      <input id={id} className="field min-w-0" value={value ?? ""} onChange={(e) => onChange(e.target.value)} placeholder='["region"]' />
    </fieldset>
  );
}

export default function AggregationFields({ value, columnNames, onChange, t }) {
  const id = useId();
  let rows;
  try {
    const parsed = value?.trim() ? JSON.parse(value) : [];
    if (Array.isArray(parsed) && parsed.every((row) => row && typeof row === "object" && !Array.isArray(row)
      && Object.keys(row).every((key) => ["fn", "column", "alias"].includes(key))
      && (row.fn === undefined || FUNCTIONS.includes(row.fn))
      && [row.column, row.alias].every((item) => item == null || typeof item === "string"))) rows = parsed;
  } catch { /* Never replace malformed or unsupported manual JSON. */ }
  const update = (index, key, next) => {
    const changed = rows.map((row, i) => {
      if (i !== index) return row;
      const result = { ...row, [key]: next };
      if (!next && key !== "fn") delete result[key];
      return result;
    });
    onChange(JSON.stringify(changed));
  };
  return (
    <fieldset className="min-w-0 flex flex-col gap-3 md:col-span-2">
      <legend className="label">{t("aggregate_metrics")}</legend>
      <p className="help">{t("aggregate_help")}</p>
      {rows ? <>
        {rows.map((row, index) => <div key={index} className="min-w-0 border-b pb-3" style={{ borderColor: "var(--line)" }}>
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm font-semibold">{t("aggregate_metric")} {index + 1}</span>
            <button type="button" className="btn btn-sm" aria-label={`${t("aggregate_remove")} ${index + 1}`} onClick={() => onChange(JSON.stringify(rows.filter((_, i) => i !== index)))}>{t("aggregate_remove")}</button>
          </div>
          <div className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-3">
            <div className="min-w-0">
              <label className="label" htmlFor={`${id}-fn-${index}`}>{t("aggregate_function")}</label>
              <select id={`${id}-fn-${index}`} className="field" value={row.fn || "sum"} onChange={(e) => update(index, "fn", e.target.value)}>
                {FUNCTIONS.map((fn) => <option key={fn} value={fn}>{t(`aggregate_fn_${fn}`)}</option>)}
              </select>
            </div>
            <div className="min-w-0">
              <label className="label" htmlFor={`${id}-col-${index}`}>{t("aggregate_column")}</label>
              <select id={`${id}-col-${index}`} className="field" value={row.column || ""} onChange={(e) => update(index, "column", e.target.value)}>
                <option value="">{row.fn === "count" ? t("aggregate_all_rows") : "—"}</option>
                {[...new Set([...columnNames, ...(row.column ? [row.column] : [])])].map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </div>
            <div className="min-w-0">
              <label className="label" htmlFor={`${id}-alias-${index}`}>{t("aggregate_alias")}</label>
              <input id={`${id}-alias-${index}`} className="field" value={row.alias ?? ""} placeholder={`${row.fn || "sum"}_${row.column || "rows"}`} onChange={(e) => update(index, "alias", e.target.value)} />
            </div>
          </div>
        </div>)}
        <button type="button" className="btn self-start" onClick={() => onChange(JSON.stringify([...rows, { fn: "sum", ...(columnNames[0] ? { column: columnNames[0] } : {}) }]))}>{t("aggregate_add")}</button>
      </> : <p className="help">{t("aggregate_manual_only")}</p>}
      <div className="min-w-0">
        <label className="label" htmlFor={`${id}-json`}>{t("aggregate_json")}</label>
        <textarea id={`${id}-json`} className="field" rows={3} value={value ?? ""} onChange={(e) => onChange(e.target.value)} placeholder='[{"column":"amount","fn":"sum","alias":"total"}]' />
      </div>
    </fieldset>
  );
}
