import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { EmptyState } from "../components/ui.jsx";
import DataGrid from "../components/DataGrid.jsx";
import ColumnProfilePopover from "../components/ColumnProfilePopover.jsx";
import StepToolbar from "../components/StepToolbar.jsx";
import RecipePanel from "../components/RecipePanel.jsx";

export default function DatasetsPage({ param }) {
  const { t, datasets, refreshDatasets, notify } = useApp();
  const [active, setActive] = useState(param || null);
  const [tab, setTab] = useState("preview");
  const [preview, setPreview] = useState(null);
  const [profile, setProfile] = useState(null);
  const [profileColumn, setProfileColumn] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

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

  const onStepApplied = async () => {
    await refreshDatasets();
    notify("Step applied");
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
            <span className="num">{dataset.row_count?.toLocaleString()}</span> {t("common_rows")} ·{" "}
            <span className="num">{dataset.columns?.length}</span> {t("common_columns")}
          </span>
        )}
        <button type="button" className="btn btn-sm ml-auto" onClick={() => api.refresh(active).then(() => { refreshDatasets(); notify("Refreshed"); }).catch((e) => setError(e.message))}
                data-testid="dataset-refresh-source">
          {t("common_refresh")}
        </button>
      </div>

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
                          <td className="num border-b p-1.5" style={{ borderColor: "var(--line)" }}>{entry.min ?? "—"}</td>
                          <td className="num border-b p-1.5" style={{ borderColor: "var(--line)" }}>{entry.max ?? "—"}</td>
                          <td className="num border-b p-1.5" style={{ borderColor: "var(--line)" }}>{entry.mean ?? "—"}</td>
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
          />
        </div>
      )}
    </div>
  );
}
