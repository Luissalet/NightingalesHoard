import React, { useId } from "react";

export default function JoinKeyPairs({ value, columnNames, rightColumnNames, onChange, t }) {
  const id = useId();
  let rows;
  try {
    const parsed = value?.trim() ? JSON.parse(value) : [];
    if (Array.isArray(parsed) && parsed.every((row) => row && typeof row === "object" && !Array.isArray(row)
      && Object.keys(row).every((key) => ["left", "right"].includes(key))
      && [row.left, row.right].every((name) => name === undefined || typeof name === "string"))) rows = parsed;
  } catch { /* Preserve incomplete or unsupported manual JSON. */ }
  const update = (index, side, next) => onChange(JSON.stringify(rows.map((row, i) => i === index ? { ...row, [side]: next } : row)));
  return <fieldset className="min-w-0 flex flex-col gap-3 md:col-span-2">
    <legend className="label">{t("join_keys")}</legend>
    <p className="help">{t("join_keys_help")}</p>
    {rows ? <>
      {rows.map((row, index) => <div key={index} className="min-w-0 border-b pb-3" style={{ borderColor: "var(--line)" }}>
        <div className="grid min-w-0 grid-cols-1 items-end gap-2 sm:grid-cols-[1fr_1fr_auto]">
          {[["left", columnNames], ["right", rightColumnNames]].map(([side, names]) => <div key={side} className="min-w-0">
            <label className="label" htmlFor={`${id}-${side}-${index}`}>{t(`join_${side}_column`)} {index + 1}</label>
            <select id={`${id}-${side}-${index}`} className="field" value={row[side] || ""} onChange={(event) => update(index, side, event.target.value)}>
              <option value="">—</option>
              {[...new Set([...names, ...(row[side] ? [row[side]] : [])])].map((name) => <option key={name} value={name}>{name}</option>)}
            </select>
          </div>)}
          <button type="button" className="btn btn-sm" aria-label={`${t("join_remove_key")} ${index + 1}`} onClick={() => onChange(JSON.stringify(rows.filter((_, i) => i !== index)))}>{t("join_remove_key")}</button>
        </div>
      </div>)}
      <button type="button" className="btn btn-sm self-start" onClick={() => onChange(JSON.stringify([...rows, { left: "", right: "" }]))}>{t("join_add_key")}</button>
    </> : <p className="help">{t("join_manual_only")}</p>}
    <label className="label" htmlFor={`${id}-manual`}>{t("join_manual")}</label>
    <textarea id={`${id}-manual`} className="field" rows={3} value={value ?? ""} onChange={(event) => onChange(event.target.value)} placeholder='[{"left":"region_id","right":"id"}]' />
  </fieldset>;
}
