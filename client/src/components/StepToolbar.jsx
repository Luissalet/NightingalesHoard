import React, { useMemo, useState } from "react";
import { api } from "../api.js";

/** Field specs per step op: keeps the toolbar a real form (not a raw JSON box)
 * for the common cases, while still covering every op type from the brief. */
const STEP_FIELDS = {
  filter: [{ name: "expr", label: "SQL condition", type: "text", placeholder: "amount > 0 AND region = 'Madrid'" }],
  select: [{ name: "columns", label: "Columns to keep (comma-separated)", type: "list" }],
  drop: [{ name: "columns", label: "Columns to drop (comma-separated)", type: "list" }],
  rename: [{ name: "mapping", label: "Renames (old:new, one per line)", type: "kv" }],
  cast: [
    { name: "column", label: "Column", type: "column" },
    { name: "to", label: "To type", type: "select", options: ["integer", "double", "decimal", "varchar", "date", "timestamp", "boolean"] },
    { name: "spanish_number", label: "Parse as Spanish number (1.234,56)", type: "bool" },
    { name: "date_format", label: "Date format (optional, e.g. %d/%m/%Y)", type: "text" },
  ],
  fill_null: [
    { name: "column", label: "Column", type: "column" },
    { name: "strategy", label: "Strategy", type: "select", options: ["value", "mean", "median", "mode", "forward"] },
    { name: "value", label: "Value (strategy=value)", type: "text" },
  ],
  drop_duplicates: [{ name: "subset", label: "Match on columns (comma-separated, empty = all)", type: "list" }],
  derive: [
    { name: "name", label: "New column name", type: "text" },
    { name: "expr", label: "SQL expression", type: "text", placeholder: "qty * price" },
  ],
  split_column: [
    { name: "column", label: "Column", type: "column" },
    { name: "delimiter", label: "Delimiter", type: "text", placeholder: "-" },
    { name: "into", label: "New column names (comma-separated)", type: "list" },
  ],
  text: [
    { name: "column", label: "Column", type: "column" },
    { name: "op", label: "Operation", type: "select", options: ["trim", "upper", "lower", "title", "strip_accents", "collapse_spaces"] },
    { name: "new_column", label: "New column (optional; blank = overwrite)", type: "text" },
  ],
  replace: [
    { name: "column", label: "Column", type: "column" },
    { name: "pattern", label: "Find", type: "text" },
    { name: "replacement", label: "Replace with", type: "text" },
    { name: "regex", label: "Treat find as regex", type: "bool" },
  ],
  bin: [
    { name: "column", label: "Column", type: "column" },
    { name: "bins", label: "Number of bins", type: "number", default: 5 },
    { name: "new_column", label: "New column name (optional)", type: "text" },
  ],
  date_parts: [
    { name: "column", label: "Date/timestamp column", type: "column" },
    { name: "parts", label: "Parts (comma-separated)", type: "list", placeholder: "year,month,day" },
  ],
  group: [
    { name: "group_by", label: "Group by (comma-separated)", type: "list" },
    { name: "aggregations", label: 'Aggregations (JSON list, e.g. [{"column":"qty","fn":"sum","alias":"total_qty"}])', type: "json" },
  ],
  pivot: [
    { name: "on", label: "Pivot column", type: "column" },
    { name: "value", label: "Value column", type: "column" },
    { name: "fn", label: "Aggregate", type: "select", options: ["sum", "avg", "count", "min", "max"] },
    { name: "group_by", label: "Group by (comma-separated, optional)", type: "list" },
  ],
  unpivot: [
    { name: "on", label: "Columns to unpivot (comma-separated)", type: "list" },
    { name: "name_col", label: "Key column name", type: "text", default: "key" },
    { name: "value_col", label: "Value column name", type: "text", default: "value" },
  ],
  join: [
    { name: "other_dataset", label: "Other dataset", type: "text" },
    { name: "on", label: 'Join keys (JSON, e.g. [{"left":"region","right":"region"}])', type: "json" },
    { name: "how", label: "Join type", type: "select", options: ["left", "inner", "right", "full"] },
  ],
  union: [
    { name: "other_dataset", label: "Other dataset", type: "text" },
    { name: "distinct", label: "Remove duplicate rows", type: "bool" },
  ],
  sort: [{ name: "by", label: 'Sort by (JSON, e.g. [{"column":"amount","desc":true}])', type: "json" }],
  sample: [
    { name: "n", label: "Row count", type: "number" },
    { name: "seed", label: "Seed", type: "number", default: 42 },
  ],
  window: [
    { name: "fn", label: "Function", type: "select", options: ["lag", "lead", "rolling_mean", "rolling_sum", "row_number", "rank"] },
    { name: "column", label: "Column", type: "column" },
    { name: "order_by", label: "Order by (comma-separated)", type: "list" },
    { name: "partition_by", label: "Partition by (comma-separated, optional)", type: "list" },
    { name: "new_column", label: "New column name (optional)", type: "text" },
  ],
  sql: [{ name: "sql", label: "SELECT over __prev__", type: "textarea", placeholder: "SELECT * FROM __prev__ WHERE amount > 0" }],
};

export const STEP_OPS = Object.keys(STEP_FIELDS);
export { STEP_FIELDS };

export function parseList(v) {
  return (v || "").split(",").map((s) => s.trim()).filter(Boolean);
}
function parseKv(v) {
  const out = {};
  (v || "").split("\n").forEach((line) => {
    const [k, val] = line.split(":").map((s) => s.trim());
    if (k && val) out[k] = val;
  });
  return out;
}

export function buildParams(op, values) {
  const fields = STEP_FIELDS[op] || [];
  const params = {};
  for (const f of fields) {
    const raw = values[f.name];
    if (raw === undefined || raw === "") continue;
    if (f.type === "list") params[f.name] = parseList(raw);
    else if (f.type === "kv") params[f.name] = parseKv(raw);
    else if (f.type === "bool") params[f.name] = !!raw;
    else if (f.type === "number") params[f.name] = Number(raw);
    else if (f.type === "json") {
      try {
        params[f.name] = JSON.parse(raw);
      } catch {
        throw new Error(`${f.label}: must be valid JSON`);
      }
    } else params[f.name] = raw;
  }
  return params;
}

/** Reverses `buildParams`: turns an already-built params object (as stored on
 * a recorded step) back into the string/bool form the fields above edit.
 * Used by the Lab pipeline editor to open an existing step for editing. */
export function paramsToValues(op, params) {
  const fields = STEP_FIELDS[op] || [];
  const values = {};
  for (const f of fields) {
    const raw = params?.[f.name];
    if (raw === undefined) continue;
    if (f.type === "list") values[f.name] = Array.isArray(raw) ? raw.join(", ") : raw;
    else if (f.type === "kv") values[f.name] = raw && typeof raw === "object" ? Object.entries(raw).map(([k, v]) => `${k}:${v}`).join("\n") : raw;
    else if (f.type === "json") values[f.name] = JSON.stringify(raw);
    else values[f.name] = raw;
  }
  return values;
}

/** The op-specific field grid shared by the dataset Recipe's step toolbar and
 * the Lab pipeline node editor, so both edit the exact same step shapes. */
export function StepFieldsGrid({ fields, values, setField, columnNames }) {
  return (
    <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
      {fields.map((f) => (
        <div key={f.name} className={f.type === "textarea" || f.type === "json" || f.type === "kv" ? "md:col-span-2" : ""}>
          <label className="label">{f.label}</label>
          {f.type === "select" ? (
            <select className="field" value={values[f.name] ?? ""} onChange={(e) => setField(f.name, e.target.value)}>
              <option value="">—</option>
              {f.options.map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          ) : f.type === "column" ? (
            <select className="field" value={values[f.name] ?? ""} onChange={(e) => setField(f.name, e.target.value)}>
              <option value="">—</option>
              {columnNames.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          ) : f.type === "bool" ? (
            <input type="checkbox" checked={!!values[f.name]} onChange={(e) => setField(f.name, e.target.checked)} />
          ) : f.type === "textarea" || f.type === "json" || f.type === "kv" ? (
            <textarea className="field" rows={3} placeholder={f.placeholder} value={values[f.name] ?? ""}
                      onChange={(e) => setField(f.name, e.target.value)} />
          ) : (
            <input className="field" type={f.type === "number" ? "number" : "text"} placeholder={f.placeholder}
                   value={values[f.name] ?? f.default ?? ""} onChange={(e) => setField(f.name, e.target.value)} />
          )}
        </div>
      ))}
    </div>
  );
}

export default function StepToolbar({ dataset, columns, onApplied, t }) {
  const [op, setOp] = useState("filter");
  const [values, setValues] = useState({});
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const fields = STEP_FIELDS[op] || [];
  const columnNames = useMemo(() => columns.map((c) => c.name), [columns]);

  const setField = (name, v) => setValues((old) => ({ ...old, [name]: v }));

  const runPreview = async () => {
    setError(null);
    setBusy(true);
    try {
      const params = buildParams(op, values);
      const result = await api.transform(dataset, op, params, true);
      setPreview(result);
    } catch (e) {
      setError(e.message);
      setPreview(null);
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    setError(null);
    setBusy(true);
    try {
      const params = buildParams(op, values);
      await api.transform(dataset, op, params, false);
      setPreview(null);
      setValues({});
      onApplied();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel flex flex-col gap-3" data-testid="step-toolbar">
      <div className="flex flex-wrap items-center gap-2">
        <label className="label" htmlFor="step-op" style={{ marginBottom: 0 }}>{t("datasets_step_new")}</label>
        <select
          id="step-op"
          className="field"
          style={{ width: "auto" }}
          value={op}
          onChange={(e) => {
            setOp(e.target.value);
            setValues({});
            setPreview(null);
          }}
        >
          {STEP_OPS.map((o) => (
            <option key={o} value={o}>{o}</option>
          ))}
        </select>
      </div>

      <StepFieldsGrid fields={fields} values={values} setField={setField} columnNames={columnNames} />

      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}

      <div className="flex gap-2">
        <button type="button" className="btn" onClick={runPreview} disabled={busy} data-testid="step-preview">
          {t("datasets_step_preview")}
        </button>
        <button type="button" className="btn btn-primary" onClick={apply} disabled={busy || !preview} data-testid="step-apply">
          {t("datasets_step_apply")}
        </button>
      </div>

      {preview && (
        <div className="panel-white text-[12.5px]" data-testid="step-preview-result">
          <div className="mb-2 flex flex-wrap gap-3">
            <span>Rows: <span className="num">{preview.row_count_before}</span> → <span className="num font-semibold">{preview.row_count_after}</span>
              {" "}({preview.row_count_delta >= 0 ? "+" : ""}{preview.row_count_delta})</span>
            {preview.columns_added.length > 0 && <span style={{ color: "var(--ok-ink)" }}>+{preview.columns_added.join(", ")}</span>}
            {preview.columns_removed.length > 0 && <span style={{ color: "var(--danger-ink)" }}>-{preview.columns_removed.join(", ")}</span>}
          </div>
          <div className="overflow-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr>
                  {preview.columns_after.map((c) => (
                    <th key={c.name} className="border-b p-1 text-left" style={{ borderColor: "var(--line)" }}>{c.name}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.preview_rows.slice(0, 8).map((row, i) => (
                  <tr key={i}>
                    {preview.columns_after.map((c) => (
                      <td key={c.name} className="border-b p-1" style={{ borderColor: "var(--line)" }}>{String(row[c.name] ?? "")}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
