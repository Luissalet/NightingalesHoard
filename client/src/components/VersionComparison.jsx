import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { formatNumber } from "../format.js";

export default function VersionComparison({ dataset, currentVersion, versionCount, t, lang }) {
  const [a, setA] = useState(0);
  const [b, setB] = useState(currentVersion);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [onlyChanged, setOnlyChanged] = useState(false);
  useEffect(() => { setA(0); setB(currentVersion); }, [dataset, currentVersion]);
  useEffect(() => {
    let live = true;
    setResult(null); setError(null);
    api.compareVersions(dataset, a, b).then((out) => { if (live) setResult(out); })
      .catch((cause) => { if (live) setError(cause.message); });
    return () => { live = false; };
  }, [dataset, a, b, currentVersion, versionCount]);
  const versions = Array.from({length: versionCount || 1}, (_, index) => index);
  const number = (value) => value === null || value === undefined ? "—" : formatNumber(value, lang);
  const delta = (value) => `${value > 0 ? "+" : ""}${number(value)}`;
  const pair = (column, key) => `${number(column.before?.[key])} → ${number(column.after?.[key])}`;
  const rangeValue = (value) => typeof value === "number" ? number(value) : value ?? "—";
  const columns = (result?.columns || []).filter((column) => !onlyChanged || column.profile_changed);
  return <section className="panel-white min-w-0" aria-label={t("versions_compare")}>
    <h3 className="font-semibold mb-3">{t("versions_compare")}</h3>
    <div className="flex flex-wrap items-end gap-3 mb-3">
      <label className="block"><span className="label">{t("versions_a")}</span><select className="field" value={a} onChange={(event) => setA(Number(event.target.value))}>{versions.map((version) => <option key={version} value={version}>v{version}</option>)}</select></label>
      <label className="block"><span className="label">{t("versions_b")}</span><select className="field" value={b} onChange={(event) => setB(Number(event.target.value))}>{versions.map((version) => <option key={version} value={version}>v{version}</option>)}</select></label>
      <label className="help flex items-center gap-2 pb-2"><input type="checkbox" checked={onlyChanged} onChange={(event) => setOnlyChanged(event.target.checked)} />{t("versions_only_changed")}</label>
    </div>
    <p className="help mb-4">{t("versions_help")}</p>
    {error && <p role="alert" className="help" style={{color:"var(--danger-ink)"}}>{error}</p>}
    {!result && !error && <p className="help" role="status">{t("common_loading")}</p>}
    {result && <>
      <div className="flex flex-wrap gap-x-6 gap-y-2 mb-4 num">
        <p>{t("versions_rows")}: <strong>{number(result.a.row_count)} → {number(result.b.row_count)}</strong> ({delta(result.row_delta)})</p>
        <p>{t("versions_columns")}: <strong>{number(result.a.column_count)} → {number(result.b.column_count)}</strong> ({delta(result.column_delta)})</p>
      </div>
      <p className="help mb-3">{t("versions_schema_summary").replace("{added}",result.added_columns.length).replace("{removed}",result.removed_columns.length).replace("{types}",result.type_changed_columns.length)}</p>
      {columns.length ? <div className="overflow-x-auto"><table className="table version-comparison-table"><caption className="help text-left mb-2">v{result.a.version} → v{result.b.version} · {t("versions_deltas")}</caption><thead><tr><th>{t("versions_column")}</th><th>{t("versions_change")}</th><th>{t("versions_types")}</th><th>{t("versions_nulls")}</th><th>{t("versions_distinct")}</th></tr></thead><tbody>
        {columns.map((column) => <React.Fragment key={column.name}><tr><th scope="row">{column.name}</th><td>{t(`versions_${column.status}`)}</td><td>{column.before?.type || "—"} → {column.after?.type || "—"}</td><td className="num whitespace-nowrap">{pair(column,"nulls")}{column.nulls_delta !== null && ` (${delta(column.nulls_delta)})`}</td><td className="num whitespace-nowrap">{pair(column,"distinct_approx")}{column.distinct_approx_delta !== null && ` (${delta(column.distinct_approx_delta)})`}</td></tr>
          {(column.before?.min !== undefined || column.after?.min !== undefined) && <tr><td colSpan={5}><details><summary className="help cursor-pointer">{t("versions_range")} · {column.name}</summary><p className="help num mt-1">A: {rangeValue(column.before?.min)} – {rangeValue(column.before?.max)} · B: {rangeValue(column.after?.min)} – {rangeValue(column.after?.max)}</p></details></td></tr>}
        </React.Fragment>)}
      </tbody></table></div> : <p className="help">{t("versions_no_changed_profiles")}</p>}
    </>}
  </section>;
}
