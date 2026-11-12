import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { EmptyState } from "../components/ui.jsx";
import VegaChart from "../components/VegaChart.jsx";
import { useCopilotActive, useCopilotRefresh } from "../copilot-hooks.js";

/** Relato: the findings a dataset supports, in order, each with its chart, the
 * sentence that states its figure and the query that produces it. Nothing here
 * is written by hand: the server computes every figure and rejects a sentence
 * whose numbers are not the finding's own. */
export default function StoryPage() {
  const { t, lang, datasets, notify } = useApp();
  const [active, setActive] = useState("");
  const [rewrite, setRewrite] = useState(false);
  const [story, setStory] = useState(null);
  const [saved, setSaved] = useState([]);
  const [editing, setEditing] = useState(null);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [exported, setExported] = useState(null);

  useEffect(() => {
    if (!active && datasets.length > 0) setActive(datasets[0].name);
  }, [datasets, active]);

  const loadSaved = useCallback(async () => {
    try {
      const { stories } = await api.storyList();
      setSaved(stories);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => { loadSaved(); }, [loadSaved]);
  useCopilotRefresh(loadSaved);
  useCopilotActive("dataset", active);

  const build = async () => {
    setError(null);
    setBusy(true);
    setExported(null);
    try {
      const result = await api.storyBuild(active, rewrite);
      setStory(result);
      if (result.notes?.length) notify(result.notes.join(" "));
      await loadSaved();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const open = async (storyId) => {
    setError(null);
    setExported(null);
    try {
      setStory(await api.storyGet(storyId));
    } catch (e) {
      setError(e.message);
    }
  };

  const update = async (body, message) => {
    setError(null);
    try {
      const result = await api.storyUpdate(story.story_id, body);
      setStory(result);
      if (result.rejected?.length) notify(result.rejected.map((item) => item.message).join(" "));
      else if (message) notify(message);
      await loadSaved();
    } catch (e) {
      setError(e.message);
    }
  };

  const move = (index, delta) => {
    const ids = story.findings.map((finding) => finding.id);
    const target = index + delta;
    if (target < 0 || target >= ids.length) return;
    [ids[index], ids[target]] = [ids[target], ids[index]];
    update({ order: ids });
  };

  const remove = (id) => update({ remove: [id] }, t("story_removed"));

  const saveSentence = (finding) => {
    update({ statements: { [finding.id]: draft } }, t("story_saved"));
    setEditing(null);
    setDraft("");
  };

  const exportStory = async (format) => {
    setError(null);
    setBusy(true);
    try {
      const result = await api.storyExport(story.story_id, format);
      setExported(result);
      if (result.ok) notify(format === "html" ? t("story_exported_html") : result.detail);
      else notify(result.detail);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const removeStory = async () => {
    try {
      await api.storyDelete(story.story_id);
      setStory(null);
      setExported(null);
      await loadSaved();
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">{t("story_title")}</h1>
      <p className="help max-w-3xl">{t("story_help")}</p>

      {error && <div className="help" style={{ color: "var(--danger-ink)" }} role="alert">{error}</div>}

      {datasets.length === 0 ? (
        <EmptyState>{t("datasets_empty")}</EmptyState>
      ) : (
        <div className="panel flex flex-col gap-3" data-testid="story-form">
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            <div>
              <label className="label" htmlFor="story-dataset">{t("nav_datasets")}</label>
              <select id="story-dataset" className="field" value={active} onChange={(e) => setActive(e.target.value)}>
                {datasets.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}
              </select>
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-[13px]">
                <input type="checkbox" checked={rewrite} onChange={(e) => setRewrite(e.target.checked)} />
                {t("story_rewrite")}
              </label>
            </div>
            <div className="flex items-end">
              <button type="button" className="btn btn-primary" onClick={build} disabled={busy || !active} data-testid="story-build">
                {busy ? t("story_building") : t("story_build")}
              </button>
            </div>
          </div>
          <p className="help">{t("story_rewrite_help")}</p>
        </div>
      )}

      {saved.length > 0 && (
        <div className="panel flex flex-col gap-2">
          <div className="text-[13px] font-semibold">{t("story_saved")}</div>
          <ul className="flex flex-col gap-1 text-[13px]">
            {saved.map((item) => (
              <li key={item.story_id} className="flex flex-wrap items-baseline justify-between gap-2">
                <button type="button" className="btn-link text-left" onClick={() => open(item.story_id)}>
                  {item.title} · {item.dataset}
                </button>
                <span className="help text-[12px]">{item.findings} {t("story_findings")} · {item.updated_at}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {story && (
        <div className="flex flex-col gap-4" data-testid="story-findings">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-[15px] font-semibold">{story.title}</div>
              <div className="help text-[12px]">
                {story.dataset} · {story.rows} {t("common_rows")} · {story.findings.length} {t("story_findings")}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn btn-sm" onClick={() => exportStory("html")} disabled={busy} data-testid="story-export-html">
                {t("story_export_html")}
              </button>
              <button type="button" className="btn btn-sm" onClick={() => exportStory("presentation")} disabled={busy}>
                {t("story_export_presentation")}
              </button>
              <button type="button" className="btn btn-sm" onClick={removeStory}>{t("story_delete")}</button>
            </div>
          </div>

          {exported && (
            <div className="panel-white text-[13px]" role="status">
              {exported.ok && exported.path ? (
                <a className="btn-link" href={api.storyDownloadUrl(exported.path)} download={exported.filename}>
                  {t("story_download")} · {exported.filename}
                </a>
              ) : (
                <span className="help">{exported.detail}</span>
              )}
            </div>
          )}

          {(story.headline || story.summary?.length > 0) && (
            <div className="panel-white flex flex-col gap-1 border-l-2 border-[var(--accent)]" data-testid="story-lead">
              {story.headline && <p className="text-[15px] font-semibold">{story.headline}</p>}
              {(story.summary || []).map((line) => (
                <p key={line} className="text-[13px]">{line}</p>
              ))}
            </div>
          )}

          {story.findings.length === 0 ? (
            <EmptyState>{t("story_empty")}</EmptyState>
          ) : (
            story.findings.map((finding, index) => (
              <div key={finding.id} className="panel-white flex flex-col gap-2" data-testid={`story-finding-${index}`}>
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <div className="font-medium">
                    <span className="num help mr-2">{index + 1}</span>
                    {finding.title}
                  </div>
                  <div className="flex flex-wrap gap-1">
                    <button type="button" className="btn btn-sm" onClick={() => move(index, -1)} disabled={index === 0}
                      aria-label={`${t("story_move_up")} · ${finding.title}`}>↑</button>
                    <button type="button" className="btn btn-sm" onClick={() => move(index, 1)} disabled={index === story.findings.length - 1}
                      aria-label={`${t("story_move_down")} · ${finding.title}`}>↓</button>
                    <button type="button" className="btn btn-sm" onClick={() => remove(finding.id)}
                      aria-label={`${t("story_remove")} · ${finding.title}`}>{t("story_remove")}</button>
                  </div>
                </div>

                {editing === finding.id ? (
                  <div className="flex flex-col gap-2">
                    <textarea className="field font-mono text-[13px]" rows={3} value={draft} onChange={(e) => setDraft(e.target.value)} />
                    <p className="help">{t("story_edit_help")}</p>
                    <div className="flex gap-2">
                      <button type="button" className="btn btn-primary btn-sm" onClick={() => saveSentence(finding)}>{t("common_save")}</button>
                      <button type="button" className="btn btn-sm" onClick={() => { setEditing(null); setDraft(""); }}>{t("common_cancel")}</button>
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col gap-1">
                    <p className="text-[13px]">{finding.statement}</p>
                    <button type="button" className="btn-link self-start text-[12px]"
                      onClick={() => { setEditing(finding.id); setDraft(finding.statement); }}>
                      {t("story_edit")}
                    </button>
                    {finding.statement_source !== "template" && (
                      <span className="help text-[11px]">{t(`story_source_${finding.statement_source}`)}</span>
                    )}
                  </div>
                )}

                {finding.chart?.vega_lite && <VegaChart spec={finding.chart.vega_lite} />}

                <table className="table text-[12px]">
                  <tbody>
                    {finding.figures.filter((figure) => figure.value !== null).map((figure) => (
                      <tr key={figure.key}>
                        <th scope="row">{figure.label || figure.key}</th>
                        <td className="num">{figure.text}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                <details>
                  <summary className="help text-[12px]">{t("story_query")}</summary>
                  <p className="help text-[12px]">{finding.query.operation}</p>
                  <pre className="overflow-x-auto text-[11px]">{finding.query.sql}</pre>
                </details>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}