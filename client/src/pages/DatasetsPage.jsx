import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { EmptyState, Modal } from "../components/ui.jsx";
import DataGrid from "../components/DataGrid.jsx";
import ColumnProfilePopover from "../components/ColumnProfilePopover.jsx";
import StepToolbar from "../components/StepToolbar.jsx";
import RecipePanel from "../components/RecipePanel.jsx";
import { formatNumber } from "../format.js";

export default function DatasetsPage({ param }) {
  const { t, lang, datasets, refreshDatasets, notify } = useApp();
  const [active, setActive] = useState(param || null);
  const [tab, setTab] = useState("preview");
  const [preview, setPreview] = useState(null);
  const [profile, setProfile] = useState(null);
  const [profileColumn, setProfileColumn] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [deleteDeps, setDeleteDeps] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [refreshQuality, setRefreshQuality] = useState(null);

  useEffect(() => {
    if (!active && datasets.length > 0) setActive(datasets[0].name);
  }, [datasets, active]);
  useEffect(() => {
    if (param) setActive(param);
  }, [param]);

  const dataset = datasets.find((d) => d.name === active);

  const loadPreview = async () => {
    if (!active) return;
    setBusy(true);
    try {
      const p = await api.preview(active, undefined, 200);
      setPreview(p);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const loadProfile = async () => {
    if (!active) return;
    try {
      const p = await api.profile(active);
      setProfile(p);
    } catch (e) {
      setError(e.message);
    }
  };

  useEffect(() => {
    setPreview(null);
    setProfile(null);
    setProfileColumn(null);
    if (active) {
      loadPreview();
      loadProfile();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, dataset?.current_version]);

  useEffect(() => setRefreshQuality(null), [active]);

  const onStepApplied = async () => {
    await refreshDatasets();
    notify("Step applied");
  };

  const openDeleteConfirm = async () => {
    if (!active) return;
    try {
      const deps = await api.datasetDependents(active);
      setDeleteDeps(deps);
    } catch (e) {
      setError(e.message);
    }
  };

  const confirmDelete = async () => {
    if (!active) return;
    setDeleting(true);
    try {
      await api.datasetDelete(active, true);
      setDeleteDeps(null);
      setActive(null);
      await refreshDatasets();
      notify(t("datasets_deleted"));
    } catch (e) {
      setError(e.message);
    } finally {
      setDeleting(false);
    }
  };

  const refreshSource = async () => {
    if (!active || busy) return;
    setBusy(true);
    setRefreshQuality(null);
    try {
      const result = await api.refresh(active);
      await refreshDatasets();
      setRefreshQuality(result.quality || null);
      notify(lang === "es" ? "Datos actualizados" : "Data refreshed");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const columnList = useMemo(() => dataset?.columns || [], [dataset]);

  if (datasets.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <h1 className="text-lg font-semibold">{t("datasets_title")}</h1>
        <EmptyState>{t("datasets_empty")}</EmptyState>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{t("datasets_title")}</h1>
        <select className="field" style={{ width: "auto" }} value={active || ""} onChange={(e) => setActive(e.target.value)} data-testid="dataset-select">
          {datasets.map((d) => (
            <option key={d.name} value={d.name}>{d.name}</option>
          ))}
        </select>
        {dataset && (
          <span className="help">
            <span className="chip chip-accent">v{dataset.current_version}</span>{" "}
            <span className="num">{formatNumber(dataset.row_count, lang)}</span> {t("common_rows")} ·{" "}
            <span className="num">{dataset.columns?.length}</span> {t("common_columns")}
          </span>
        )}
        {active && (
          <a className="btn btn-sm ml-auto" href={`#/lab/explore/${encodeURIComponent(active)}`} data-testid="dataset-open-in-lab">
            {t("lab_open_in_lab")}
          </a>
        )}
        <button type="button" className="btn btn-sm" disabled={busy} onClick={refreshSource}
                data-testid="dataset-refresh-source">
          {t("common_refresh")}
        </button>
        <button type="button" className="btn btn-sm" onClick={openDeleteConfirm} data-testid="dataset-delete-open">
          {t("datasets_delete")}
        </button>
      </div>

      {refreshQuality && (
        <section className="panel text-[13px]" aria-live="polite" data-testid="dataset-refresh-quality">
          {refreshQuality.error ? (
            <p>{lang === "es" ? "Datos actualizados; no se pudieron ejecutar las reglas:" : "Data refreshed; quality rules could not run:"} {refreshQuality.error}</p>
          ) : (
            <>
              <strong>{lang === "es" ? "Calidad tras actualizar" : "Quality after refresh"}: {refreshQuality.passed_rules}/{refreshQuality.checked_rules}</strong>
              {refreshQuality.failed_rules.map((rule) => (
                <p key={rule.rule_id} className="mt-2">
                  {rule.name}: {formatNumber(rule.failed, lang)} {lang === "es" ? "filas fallidas" : "failing rows"}
                  {rule.sample?.[0] && <span className="help"> · {JSON.stringify(rule.sample[0])}</span>}
                </p>
              ))}
            </>
          )}
        </section>
      )}

      {deleteDeps && (
        <Modal title={t("datasets_delete_confirm_title")} onClose={() => setDeleteDeps(null)}>
          <p className="help mb-3">{t("datasets_delete_confirm_body")}</p>
          {deleteDeps.has_dependents && (
            <div className="mb-3 rounded-md border p-3 text-[13px]" style={{ background: "var(--danger-bg)", color: "var(--danger-ink)", borderColor: "var(--danger-line)" }}>
              <div className="mb-1 font-semibold">{t("datasets_delete_dependents_warning")}</div>
              <ul className="list-disc pl-4">
                {deleteDeps.charts.length > 0 && <li>{deleteDeps.charts.length} {t("datasets_delete_dependents_charts")}: {deleteDeps.charts.map((c) => c.name).join(", ")}</li>}
                {deleteDeps.dashboards.length > 0 && <li>{deleteDeps.dashboards.length} {t("datasets_delete_dependents_dashboards")}: {deleteDeps.dashboards.map((d) => d.name).join(", ")}</li>}
                {deleteDeps.models.length > 0 && <li>{deleteDeps.models.length} {t("datasets_delete_dependents_models")}: {deleteDeps.models.map((m) => m.name).join(", ")}</li>}
              </ul>
            </div>
          )}
          <div className="flex justify-end gap-2">
            <button type="button" className="btn btn-sm" onClick={() => setDeleteDeps(null)}>{t("common_cancel")}</button>
            <button type="button" className="btn btn-sm btn-danger" disabled={deleting} onClick={confirmDelete} data-testid="dataset-delete-confirm">
              {t("datasets_delete_confirm_button")}
            </button>
          </div>
        </Modal>
      )}

      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}

      {dataset && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="flex flex-col gap-4">
            <StepToolbar dataset={active} columns={columnList} onApplied={onStepApplied} t={t} />

            <div className="seg self-start" role="tablist">
              <button type="button" aria-pressed={tab === "preview"} onClick={() => setTab("preview")}>{t("datasets_preview_tab")}</button>
              <button type="button" aria-pressed={tab === "profile"} onClick={() => setTab("profile")}>{t("datasets_profile_tab")}</button>
            </div>

            {tab === "preview" && preview && (
              <div style={{ position: "relative" }}>
                <DataGrid
                  columns={preview.columns.map((c) => ({ name: c.name, type: c.type }))}
                  rows={preview.rows}
                  onHeaderClick={(name) => setProfileColumn(name === profileColumn ? null : name)}
                  activeColumn={profileColumn}
                  lang={lang}
                />
                {profileColumn && profile && (
                  <ColumnProfilePopover
                    column={profileColumn}
                    entry={profile.profile?.[profileColumn]}
                    onClose={() => setProfileColumn(null)}
                  />
                )}
              </div>
            )}
            {tab === "preview" && !preview && !busy && <EmptyState>{t("common_loading")}</EmptyState>}

            {tab === "profile" && profile && (
              <div className="panel-white overflow-auto">
                <table className="w-full border-collapse text-[12.5px]">
                  <thead>
                    <tr>
                      {["Column", "Type", "Nulls %", "Distinct", "Min", "Max", "Mean"].map((h) => (
                        <th key={h} className="border-b p-1.5 text-left" style={{ borderColor: "var(--line)" }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {profile.columns.map((c) => {
                      const entry = profile.profile?.[c.name] || {};
                      return (
                        <tr key={c.name} className="cursor-pointer hover:opacity-80" onClick={() => setProfileColumn(c.name)}>
                          <td className="border-b p-1.5 font-medium" style={{ borderColor: "var(--line)" }}>{c.name}</td>
                          <td className="border-b p-1.5" style={{ borderColor: "var(--line)" }}>{c.type}</td>
                          <td className="num border-b p-1.5" style={{ borderColor: "var(--line)" }}>{entry.nulls_pct ?? "—"}</td>
                          <td className="num border-b p-1.5" style={{ borderColor: "var(--line)" }}>{entry.distinct_approx ?? "—"}</td>
                          <td className="num border-b p-1.5" style={{ borderColor: "var(--line)" }}>{typeof entry.min === "number" ? formatNumber(entry.min, lang) : entry.min ?? "—"}</td>
                          <td className="num border-b p-1.5" style={{ borderColor: "var(--line)" }}>{typeof entry.max === "number" ? formatNumber(entry.max, lang) : entry.max ?? "—"}</td>
                          <td className="num border-b p-1.5" style={{ borderColor: "var(--line)" }}>{entry.mean !== undefined ? formatNumber(entry.mean, lang) : "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <RecipePanel
            dataset={active}
            currentVersion={dataset.current_version}
            versionCount={dataset.version_count}
            onChanged={async () => {
              await refreshDatasets();
              await loadPreview();
              await loadProfile();
            }}
            t={t}
            lang={lang}
          />
        </div>
      )}
    </div>
  );
}
