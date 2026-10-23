import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { displaySource, formatNumber } from "../format.js";

/** The lineage/recipe side panel: every step applied to the current dataset,
 * with jump-to-version undo/redo and a recipe export (a runnable SQL script). */
export default function RecipePanel({ dataset, currentVersion, versionCount, onChanged, onCompare, t, lang }) {
  const [recipe, setRecipe] = useState(null);
  const [lineage, setLineage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [exported, setExported] = useState(null);
  const [copied, setCopied] = useState(false);

  const load = async () => {
    try {
      const [r, l] = await Promise.all([api.recipe(dataset, "show"), api.lineage(dataset)]);
      setRecipe(r);
      setLineage(l);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    setExported(null);
    setCopied(false);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset, currentVersion, versionCount]);

  const withBusy = async (fn, changed = true) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      if (changed) await onChanged?.();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const doUndo = () => withBusy(() => api.undo(dataset, 1));
  const doRedo = () => withBusy(() => api.redo(dataset, 1));
  const doExport = () => withBusy(async () => {
      const r = await api.recipe(dataset, "export");
      setExported(r.sql_script);
      setCopied(false);
  }, false);
  const openVersion = (version) => {
    const delta = version - currentVersion;
    if (!delta) return;
    return withBusy(() => delta < 0 ? api.undo(dataset, -delta) : api.redo(dataset, delta));
  };
  const copySql = async () => {
    try {
      await navigator.clipboard.writeText(exported);
      setCopied(true);
      setError(null);
    } catch {
      setError(t("recipe_copy_error"));
    }
  };
  const downloadSql = () => {
    const url = URL.createObjectURL(new Blob([exported], { type: "application/sql;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${dataset.replace(/[^\p{L}\p{N}_.-]/gu, "_")}-v${currentVersion}.sql`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const doReplay = () => withBusy(() => api.recipe(dataset, "replay"));

  const canRedo = recipe && versionCount !== undefined && currentVersion < versionCount - 1;

  return (
    <div className="panel flex flex-col gap-3" data-testid="recipe-panel">
      <div className="flex items-center justify-between">
        <h3 className="text-[13px] font-semibold uppercase tracking-wide" style={{ color: "var(--supporting-ink)" }}>
          {t("datasets_recipe_tab")}
        </h3>
        <div className="flex gap-1">
          <button type="button" className="btn btn-sm" onClick={doUndo} disabled={busy || !recipe || currentVersion === 0}
                  data-testid="recipe-undo" title={t("common_undo")}>
            {t("common_undo")}
          </button>
          <button type="button" className="btn btn-sm" onClick={doRedo} disabled={busy || !canRedo}
                  data-testid="recipe-redo" title={t("common_redo")}>
            {t("common_redo")}
          </button>
        </div>
      </div>

      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}

      <label className="flex flex-col gap-1 text-sm">
        {t("recipe_open_version")}
        <select className="field" value={currentVersion} disabled={busy || !recipe}
                onChange={(e) => openVersion(Number(e.target.value))}>
          {Array.from({ length: versionCount ?? currentVersion + 1 }, (_, version) => (
            <option key={version} value={version}>v{version}{version === currentVersion ? ` · ${t("recipe_current")}` : ""}</option>
          ))}
        </select>
      </label>
      <p className="help">{t("recipe_version_help")}</p>

      {lineage?.source && (
        <div className="help">
          {t("recipe_source")}: <span className="chip">{lineage.source.kind}</span>{" "}
          <span className="truncate" title={lineage.source.path}>{displaySource(lineage.source.path)}</span>
        </div>
      )}

      <ol className="flex flex-col gap-1" data-testid="recipe-steps">
        {(recipe?.steps || []).map((s) => (
          <li
            key={s.version}
            className="rounded-md text-[12.5px]"
            style={{
              background: s.version === currentVersion ? "var(--nav-active)" : "var(--white)",
              border: "1px solid var(--line)",
            }}
          >
            <button type="button" className="recipe-step" disabled={busy || s.version === currentVersion}
                    aria-label={`${t("recipe_open_version")} v${s.version}`}
                    aria-current={s.version === currentVersion ? "step" : undefined}
                    onClick={() => openVersion(s.version)}>
              <span className="chip">v{s.version}</span>
              <span className="font-medium" style={{ overflowWrap: "anywhere" }}>{s.op}</span>
              <span className="help num ml-auto shrink-0">{formatNumber(s.row_count, lang)} {t("common_rows")}</span>
            </button>
          </li>
        ))}
        {(!recipe || recipe.steps.length === 0) && <li className="help">{t("recipe_no_steps")}</li>}
      </ol>

      <div className="flex flex-wrap gap-2">
        {onCompare && <button type="button" className="btn btn-sm" onClick={onCompare}>{t("versions_compare")}</button>}
        <button type="button" className="btn btn-sm" onClick={doExport} disabled={busy || !recipe} data-testid="recipe-export">
          {t("common_export")} SQL
        </button>
        <button type="button" className="btn btn-sm" onClick={doReplay} disabled={busy} data-testid="recipe-replay">
          {t("recipe_replay")}
        </button>
      </div>

      {exported && (
        <div className="flex flex-col gap-2" style={{ minWidth: 0 }}>
          <p className="help">{t("recipe_sql_help")}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn btn-sm" onClick={copySql}>{t("recipe_copy_sql")}</button>
            <button type="button" className="btn btn-sm" onClick={downloadSql}>{t("recipe_download_sql")}</button>
            {copied && <span className="help" role="status">{t("recipe_copied")}</span>}
          </div>
          <pre className="mono panel-white overflow-auto text-[11px]" style={{ maxHeight: 220 }} data-testid="recipe-sql">{exported}</pre>
        </div>
      )}
    </div>
  );
}
