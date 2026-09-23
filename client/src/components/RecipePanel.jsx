import React, { useEffect, useState } from "react";
import { api } from "../api.js";

/** The lineage/recipe side panel: every step applied to the current dataset,
 * with jump-to-version undo/redo and a recipe export (a runnable SQL script). */
export default function RecipePanel({ dataset, currentVersion, versionCount, onChanged, t }) {
  const [recipe, setRecipe] = useState(null);
  const [lineage, setLineage] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [exported, setExported] = useState(null);

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
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset, currentVersion, versionCount]);

  const withBusy = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged && onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const doUndo = () => withBusy(() => api.undo(dataset, 1));
  const doRedo = () => withBusy(() => api.redo(dataset, 1));
  const doExport = async () => {
    try {
      const r = await api.recipe(dataset, "export");
      setExported(r.sql_script);
    } catch (e) {
      setError(e.message);
    }
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

      {lineage?.source && (
        <div className="help">
          Source: <span className="chip">{lineage.source.kind}</span> <span className="truncate">{lineage.source.path}</span>
        </div>
      )}

      <ol className="flex flex-col gap-1" data-testid="recipe-steps">
        {(recipe?.steps || []).map((s) => (
          <li
            key={s.version}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-[12.5px]"
            style={{
              background: s.version === currentVersion ? "var(--nav-active)" : "var(--white)",
              border: "1px solid var(--line)",
            }}
          >
            <span className="chip">v{s.version}</span>
            <span className="font-medium">{s.op}</span>
            <span className="help num ml-auto shrink-0">{s.row_count?.toLocaleString?.() ?? s.row_count}</span>
          </li>
        ))}
        {(!recipe || recipe.steps.length === 0) && <div className="help">No steps yet.</div>}
      </ol>

      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn btn-sm" onClick={doExport} data-testid="recipe-export">
          {t("common_export")} SQL
        </button>
        <button type="button" className="btn btn-sm" onClick={doReplay} disabled={busy} data-testid="recipe-replay">
          Replay on refresh
        </button>
      </div>

      {exported && (
        <pre className="mono panel-white overflow-auto text-[11px]" style={{ maxHeight: 220 }} data-testid="recipe-sql">
          {exported}
        </pre>
      )}
    </div>
  );
}
