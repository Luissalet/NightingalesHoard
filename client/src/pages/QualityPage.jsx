import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { EmptyState, Chip } from "../components/ui.jsx";

const RULE_FIELDS = {
  not_null: [{ name: "column", label: "Column", type: "column" }],
  unique: [{ name: "columns", label: "Columns (comma-separated)", type: "list" }],
  accepted_values: [
    { name: "column", label: "Column", type: "column" },
    { name: "values", label: "Accepted values (comma-separated)", type: "list" },
  ],
  range: [
    { name: "column", label: "Column", type: "column" },
    { name: "min", label: "Min (optional)", type: "number" },
    { name: "max", label: "Max (optional)", type: "number" },
  ],
  regex: [
    { name: "column", label: "Column", type: "column" },
    { name: "pattern", label: "Pattern", type: "text" },
  ],
  row_count: [
    { name: "min", label: "Min rows (optional)", type: "number" },
    { name: "max", label: "Max rows (optional)", type: "number" },
  ],
  freshness: [
    { name: "column", label: "Date/timestamp column", type: "column" },
    { name: "max_age_days", label: "Max age (days)", type: "number" },
  ],
  referential: [
    { name: "column", label: "Column", type: "column" },
    { name: "ref_table", label: "Referenced dataset/table", type: "text" },
    { name: "ref_column", label: "Referenced column (optional; defaults to same name)", type: "text" },
  ],
  custom_sql: [{ name: "sql", label: "SQL returning failing rows (use __table__)", type: "textarea" }],
};
const RULE_KINDS = Object.keys(RULE_FIELDS);

function parseList(v) {
  return (v || "").split(",").map((s) => s.trim()).filter(Boolean);
}
function buildParams(kind, values) {
  const fields = RULE_FIELDS[kind] || [];
  const params = {};
  for (const f of fields) {
    const raw = values[f.name];
    if (raw === undefined || raw === "") continue;
    if (f.type === "list") params[f.name] = parseList(raw);
    else if (f.type === "number") params[f.name] = Number(raw);
    else params[f.name] = raw;
  }
  return params;
}

export default function QualityPage() {
  const { t, datasets, notify } = useApp();
  const [active, setActive] = useState(null);
  const [report, setReport] = useState(null);
  const [kind, setKind] = useState("not_null");
  const [ruleName, setRuleName] = useState("");
  const [values, setValues] = useState({});
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!active && datasets.length > 0) setActive(datasets[0].name);
  }, [datasets, active]);

  const load = async () => {
    if (!active) return;
    try {
      setReport(await api.qualityReport(active));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const dataset = datasets.find((d) => d.name === active);
  const columnNames = (dataset?.columns || []).map((c) => c.name);
  const fields = RULE_FIELDS[kind] || [];

  const addRule = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const params = buildParams(kind, values);
      await api.qualityDefine(active, { name: ruleName || kind, kind, params });
      setRuleName("");
      setValues({});
      notify("Rule added");
      await load();
    } catch (e2) {
      setError(e2.message);
    } finally {
      setBusy(false);
    }
  };

  const runAll = async () => {
    setBusy(true);
    try {
      await api.qualityRun(active);
      notify(t("quality_run_all"));
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const runOne = async (ruleId) => {
    try {
      await api.qualityRun(active, ruleId);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  const removeRule = async (ruleId) => {
    try {
      await api.qualityDelete(ruleId);
      await load();
    } catch (e) {
      setError(e.message);
    }
  };

  if (datasets.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-lg font-semibold">{t("quality_title")}</h1>
        <EmptyState>{t("datasets_empty")}</EmptyState>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{t("quality_title")}</h1>
        <select className="field" style={{ width: "auto" }} value={active || ""} onChange={(e) => setActive(e.target.value)}>
          {datasets.map((d) => (
            <option key={d.name} value={d.name}>{d.name}</option>
          ))}
        </select>
        <button type="button" className="btn btn-primary ml-auto" onClick={runAll} disabled={busy || !report?.rules?.length} data-testid="quality-run-all">
          {t("quality_run_all")}
        </button>
      </div>

      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}

      <form onSubmit={addRule} className="panel flex flex-col gap-3" data-testid="quality-form">
        <div className="flex flex-wrap items-center gap-2">
          <label className="label" style={{ marginBottom: 0 }}>{t("quality_add")}</label>
          <select className="field" style={{ width: "auto" }} value={kind} onChange={(e) => { setKind(e.target.value); setValues({}); }}>
            {RULE_KINDS.map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
          <input className="field" style={{ width: "auto" }} placeholder={t("common_name")} value={ruleName}
                 onChange={(e) => setRuleName(e.target.value)} />
        </div>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {fields.map((f) => (
            <div key={f.name} className={f.type === "textarea" ? "md:col-span-2" : ""}>
              <label className="label">{f.label}</label>
              {f.type === "column" ? (
                <select className="field" value={values[f.name] ?? ""} onChange={(e) => setValues((o) => ({ ...o, [f.name]: e.target.value }))}>
                  <option value="">—</option>
                  {columnNames.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              ) : f.type === "textarea" ? (
                <textarea className="field" rows={2} value={values[f.name] ?? ""} onChange={(e) => setValues((o) => ({ ...o, [f.name]: e.target.value }))} />
              ) : (
                <input className="field" type={f.type === "number" ? "number" : "text"} value={values[f.name] ?? ""}
                       onChange={(e) => setValues((o) => ({ ...o, [f.name]: e.target.value }))} />
              )}
            </div>
          ))}
        </div>
        <div>
          <button type="submit" className="btn btn-primary" disabled={busy} data-testid="quality-add-rule">{t("quality_add")}</button>
        </div>
      </form>

      {(!report || report.rules.length === 0) ? (
        <EmptyState>{t("quality_empty")}</EmptyState>
      ) : (
        <div className="panel-white divide-y" style={{ borderColor: "var(--line)" }} data-testid="quality-rules">
          {report.rules.map((r) => (
            <div className="row flex-wrap" key={r.rule_id}>
              <span className="chip">{r.kind}</span>
              <span className="font-medium">{r.name}</span>
              {r.last_result ? (
                <Chip tone={r.last_result.passed ? "ok" : "danger"}>
                  {r.last_result.passed ? "passed" : `${r.last_result.failed_count ?? r.last_result.failed} failing`}
                </Chip>
              ) : (
                <span className="help">not run yet</span>
              )}
              <div className="ml-auto flex shrink-0 gap-2">
                <button type="button" className="btn btn-sm" onClick={() => runOne(r.rule_id)}>{t("common_run")}</button>
                <button type="button" className="btn btn-sm btn-danger" onClick={() => removeRule(r.rule_id)}>{t("common_delete")}</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
