import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { EmptyState } from "../components/ui.jsx";
import VegaChart from "../components/VegaChart.jsx";
import { formatNumber } from "../format.js";
import { useCopilotActive, useCopilotForm, useCopilotRefresh } from "../copilot-hooks.js";
import { settleLoad } from "../draft-merge.js";

const NEW_DASHBOARD_TEMPLATE = `version: 1
name: New dashboard
model: my_model
tabs:
  - name: Overview
    rows:
      - widgets:
          - {type: text, markdown: "Pick a semantic model and add widgets here."}
`;

const NEW_SEMANTIC_TEMPLATE = `version: 1
models: {}
`;

/** A tiny markdown subset for text widgets: bold, italics, links. No HTML. */
function renderMarkdown(text) {
  const escaped = String(text || "").replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c]));
  const withLinks = escaped.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  const withBold = withLinks.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  const withItalics = withBold.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  return { __html: withItalics };
}

function IssueList({ issues }) {
  if (!issues || issues.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1 text-[12px]">
      {issues.map((issue, i) => (
        <li key={i} style={{ color: issue.level === "error" ? "var(--danger-ink)" : "var(--supporting-ink)" }}>
          <strong>{issue.path || "(root)"}</strong>: {issue.message}
          {issue.hint && <span className="help"> — {issue.hint}</span>}
        </li>
      ))}
    </ul>
  );
}

function Widget({ widget, lang }) {
  if (widget.error) {
    return (
      <div className="panel-white" data-testid="dac-widget-error">
        {widget.title && <div className="mb-1 text-[12px] font-medium">{widget.title}</div>}
        <div className="help" style={{ color: "var(--danger-ink)" }}>{widget.error}</div>
      </div>
    );
  }
  if (widget.type === "metric") {
    const up = widget.delta_pct != null && widget.delta_pct >= 0;
    return (
      <div className="stat-tile" data-testid="dac-widget-metric">
        <div className="help">{widget.title}</div>
        <div className="stat-number">{formatNumber(widget.value, lang)}</div>
        {widget.delta_pct != null && (
          <div className="help" style={{ color: up ? "var(--success-ink, green)" : "var(--danger-ink)" }}>
            {up ? "▲" : "▼"} {formatNumber(Math.abs(widget.delta_pct), lang)}%
          </div>
        )}
      </div>
    );
  }
  if (widget.type === "chart") {
    return (
      <div className="panel-white" data-testid="dac-widget-chart">
        {widget.title && <div className="mb-2 font-medium">{widget.title}</div>}
        <VegaChart spec={widget.vega_lite} />
      </div>
    );
  }
  if (widget.type === "table") {
    return (
      <div className="panel-white overflow-auto" data-testid="dac-widget-table">
        {widget.title && <div className="mb-2 font-medium">{widget.title}</div>}
        <table className="w-full text-[12px]">
          <thead>
            <tr>{(widget.columns || []).map((c) => <th key={c} className="text-left">{c}</th>)}</tr>
          </thead>
          <tbody>
            {(widget.rows || []).map((row, i) => (
              <tr key={i}>{(widget.columns || []).map((c) => <td key={c}>{formatNumber(row[c], lang) ?? String(row[c] ?? "")}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  if (widget.type === "text") {
    return (
      <div className="panel-white" data-testid="dac-widget-text">
        {widget.title && <div className="mb-1 font-medium">{widget.title}</div>}
        <div dangerouslySetInnerHTML={renderMarkdown(widget.markdown)} />
      </div>
    );
  }
  return null;
}

function FilterControl({ filter, value, onChange }) {
  if (filter.type === "date-range") {
    const [a, b] = Array.isArray(value) ? value : ["", ""];
    return (
      <div className="flex items-center gap-1">
        <span className="help">{filter.name}</span>
        <input className="field" type="date" style={{ width: "auto" }} value={a || ""} onChange={(e) => onChange([e.target.value, b])} />
        <input className="field" type="date" style={{ width: "auto" }} value={b || ""} onChange={(e) => onChange([a, e.target.value])} />
      </div>
    );
  }
  if (filter.type === "select") {
    return (
      <select className="field" style={{ width: "auto" }} value={value ?? "all"} onChange={(e) => onChange(e.target.value)}>
        <option value="all">{filter.name}: all</option>
        {(filter.options || []).map((opt) => <option key={String(opt)} value={opt}>{String(opt)}</option>)}
      </select>
    );
  }
  return (
    <input className="field" style={{ width: "auto" }} placeholder={filter.name} value={value ?? ""}
           onChange={(e) => onChange(e.target.value)} />
  );
}

export default function DashboardCodePage() {
  const { t, lang, datasets, notify } = useApp();
  const [view, setView] = useState("dashboards"); // "dashboards" | "semantic"

  // ---- dashboards ----
  const [list, setList] = useState([]);
  const [slug, setSlug] = useState(null);
  const [text, setText] = useState("");
  const [issues, setIssues] = useState([]);
  const [rendered, setRendered] = useState(null);
  const [filterValues, setFilterValues] = useState({});
  const [activeTab, setActiveTab] = useState(0);
  const [history, setHistory] = useState([]);
  const [diffSel, setDiffSel] = useState(["", "current"]);
  const [diffText, setDiffText] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  // ---- semantic ----
  const [semanticText, setSemanticText] = useState("");
  const [semanticIssues, setSemanticIssues] = useState([]);
  const [suggestDataset, setSuggestDataset] = useState("");

  // what each editor last loaded or saved: unsaved changes are the difference, and an agent reload never overwrites them
  const savedText = useRef("");
  const savedSemantic = useRef("");
  // the live values, readable from async callbacks that started before the user's latest keystroke
  const textRef = useRef("");
  const semanticRef = useRef("");
  const slugRef = useRef(null);
  const createdSlug = useRef(null); // the slug a just-sent create produced: its load must not erase later typing
  textRef.current = text;
  semanticRef.current = semanticText;
  slugRef.current = slug;

  const loadList = useCallback(async () => {
    try {
      const { dashboards } = await api.dac.list();
      setList(dashboards);
      if (dashboards.length > 0) setSlug((cur) => cur || dashboards[0].slug);
    } catch (e) {
      setError(e.message);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    loadList();
    const started = semanticRef.current;
    api.dac.semanticGet().then((r) => {
      const next = settleLoad({ started, current: semanticRef.current, loaded: r.text || "" });
      savedSemantic.current = next.baseline;
      if (next.replace) setSemanticText(next.text);
    }).catch(() => {});
  }, [loadList]);

  const loadDashboard = useCallback(async (s) => {
    if (!s) return;
    // right after a create the editor already holds the saved text: keep whatever was typed since it was sent
    const afterCreate = createdSlug.current === s;
    if (afterCreate) createdSlug.current = null;
    const started = afterCreate ? savedText.current : textRef.current;
    try {
      const r = await api.dac.get(s);
      if (slugRef.current !== s) return;
      const next = settleLoad({ started, current: textRef.current, loaded: r.text, keepBaselineOnEdit: afterCreate });
      savedText.current = next.baseline;
      if (next.replace) setText(next.text);
      setIssues([]);
      setError(null);
    } catch (e) {
      if (slugRef.current !== s) return;
      setError(e.message);
    }
  }, []);
  useEffect(() => {
    loadDashboard(slug);
  }, [slug, loadDashboard]);

  const doRender = useCallback(async (s, filters) => {
    if (!s) return;
    try {
      const r = await api.dac.render(s, filters);
      setRendered(r);
      setActiveTab(0);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => {
    if (slug) doRender(slug, {});
    setFilterValues({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slug]);

  const validate = async () => {
    try {
      const r = await api.dac.validate(slug, text);
      setIssues(r.issues || []);
      if ((r.issues || []).length === 0) notify(t("dac_validated_ok"));
    } catch (e) {
      setError(e.message);
    }
  };

  const save = async () => {
    setBusy(true);
    try {
      if (slug) {
        // saving rewrites the whole YAML: do not silently drop a change made meanwhile (e.g. by the agent)
        const current = await api.dac.get(slug).catch(() => null);
        if (current && current.text !== savedText.current && !window.confirm(t("dac_changed_elsewhere"))) return;
      }
      const sent = text;
      const r = slug ? await api.dac.put(slug, sent) : await api.dac.create(sent);
      savedText.current = sent; // the baseline is what was sent; anything typed while the request was pending stays unsaved
      if (!slug) createdSlug.current = r.slug;
      setIssues(r.issues || []);
      setSlug(r.slug);
      await loadList();
      notify(t("common_save"));
      await doRender(r.slug, filterValues);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const createNew = async () => {
    setSlug(null);
    savedText.current = NEW_DASHBOARD_TEMPLATE;
    setText(NEW_DASHBOARD_TEMPLATE);
    setIssues([]);
    setRendered(null);
  };

  const applyFilters = async () => {
    await doRender(slug, filterValues);
  };

  const loadHistory = async () => {
    if (!slug) return;
    try {
      const r = await api.dac.history(slug);
      setHistory(r.history || []);
    } catch (e) {
      setError(e.message);
    }
  };

  const runDiff = async () => {
    try {
      const r = await api.dac.diff(slug, diffSel[0] || "current", diffSel[1] || "current");
      setDiffText(r.diff);
    } catch (e) {
      setError(e.message);
    }
  };

  const exportDashboard = async () => {
    try {
      const r = await api.dac.export(slug, filterValues);
      notify(`${t("dac_exported")} (#${r.dashboard_id})`);
    } catch (e) {
      setError(e.message);
    }
  };

  const deleteDashboard = async () => {
    if (!slug) return;
    try {
      await api.dac.delete(slug);
      setSlug(null);
      savedText.current = "";
      setText("");
      setRendered(null);
      await loadList();
    } catch (e) {
      setError(e.message);
    }
  };

  // ---- semantic actions ----
  const validateSemantic = async (textOverride) => {
    try {
      const r = await api.dac.semanticValidate(textOverride ?? semanticText);
      setSemanticIssues(r.issues || []);
    } catch (e) {
      setError(e.message);
    }
  };
  const saveSemantic = async () => {
    setBusy(true);
    try {
      const sent = semanticText;
      const r = await api.dac.semanticPut(sent);
      savedSemantic.current = sent;
      setSemanticIssues(r.issues || []);
      notify(t("common_save"));
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const suggestFromDataset = async () => {
    if (!suggestDataset) return;
    try {
      const r = await api.dac.semanticSuggest(suggestDataset);
      const YAML_HEADER = "# suggested — review and merge into your semantic.yaml, then Save\n";
      setSemanticText(YAML_HEADER + JSON.stringify(r.doc, null, 2));
    } catch (e) {
      setError(e.message);
    }
  };

  // An agent turn may have edited the dashboards or the semantic layer: reload them in place, keeping unsaved edits.
  useCopilotRefresh(async () => {
    loadList();
    // the answers are judged against what the editors hold when they ARRIVE, never against what they held at the start
    const semanticStarted = savedSemantic.current;
    api.dac.semanticGet().then((r) => {
      const next = settleLoad({ started: semanticStarted, current: semanticRef.current, loaded: r.text || "" });
      savedSemantic.current = next.baseline;
      if (next.replace) setSemanticText(next.text);
    }).catch(() => {});
    const target = slug;
    if (!target) return;
    const started = savedText.current;
    try {
      const r = await api.dac.get(target);
      if (slugRef.current !== target) return; // the user opened another dashboard meanwhile
      const next = settleLoad({ started, current: textRef.current, loaded: r.text });
      savedText.current = next.baseline;
      if (next.replace) setText(next.text);
      doRender(target, filterValues);
    } catch { /* the dashboard may be gone now */ }
  });
  useCopilotActive("dashboard-code", view === "dashboards" ? slug : null);
  // the open editor reports its unsaved text; a dashboard that is not stored yet is a draft
  useCopilotForm(
    view === "semantic" ? "semantic-layer" : slug ? "dashboard-code" : "dashboard-code-draft",
    view === "semantic"
      ? { initial: { text: savedSemantic.current }, current: { text: semanticText } }
      : { objectId: slug, initial: { text: savedText.current }, current: { text } },
  );

  const tabs = rendered?.tabs || [];
  const currentTab = tabs[activeTab];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{t("dac_title")}</h1>
        <a href="#/dashboards" className="btn btn-sm">{t("dashboards_title")}</a>
        <div className="flex gap-1">
          <button type="button" className={`btn btn-sm ${view === "dashboards" ? "btn-primary" : ""}`} onClick={() => setView("dashboards")}>
            {t("dac_tab_dashboards")}
          </button>
          <button type="button" className={`btn btn-sm ${view === "semantic" ? "btn-primary" : ""}`} onClick={() => setView("semantic")}>
            {t("dac_tab_semantic")}
          </button>
        </div>
      </div>

      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}

      {view === "semantic" ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <div className="panel flex flex-col gap-2">
            <div className="flex flex-wrap items-end gap-2">
              <select className="field" style={{ width: "auto" }} value={suggestDataset} onChange={(e) => setSuggestDataset(e.target.value)}>
                <option value="">{t("dac_pick_dataset")}</option>
                {datasets.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}
              </select>
              <button type="button" className="btn btn-sm" onClick={suggestFromDataset} data-testid="dac-suggest">{t("dac_suggest")}</button>
              <button type="button" className="btn btn-sm" onClick={() => validateSemantic()} data-testid="dac-semantic-validate">{t("dac_validate")}</button>
              <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={saveSemantic} data-testid="dac-semantic-save">{t("common_save")}</button>
            </div>
            <textarea className="field font-mono" style={{ minHeight: 420, fontFamily: "monospace", fontSize: 12 }}
                      value={semanticText} onChange={(e) => setSemanticText(e.target.value)} spellCheck={false}
                      data-testid="dac-semantic-editor" />
          </div>
          <div className="panel flex flex-col gap-2">
            <div className="text-[13px] font-semibold">{t("dac_issues")}</div>
            {semanticIssues.length === 0 ? <EmptyState>{t("dac_no_issues")}</EmptyState> : <IssueList issues={semanticIssues} />}
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
          <div className="panel flex flex-col gap-2">
            <button type="button" className="btn btn-sm" onClick={createNew} data-testid="dac-new">{t("common_create")}</button>
            <div className="flex flex-col gap-1">
              {list.map((d) => (
                <button key={d.slug} type="button"
                        className={`btn-link text-left ${d.slug === slug ? "font-semibold" : ""}`}
                        onClick={() => setSlug(d.slug)} data-testid="dac-list-item">
                  {d.name}
                </button>
              ))}
              {list.length === 0 && <EmptyState>{t("dashboards_empty")}</EmptyState>}
            </div>
          </div>

          <div className="flex flex-col gap-4">
            <div className="panel flex flex-col gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <button type="button" className="btn btn-sm" onClick={validate} data-testid="dac-validate">{t("dac_validate")}</button>
                <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={save} data-testid="dac-save">{t("common_save")}</button>
                {slug && (
                  <>
                    <button type="button" className="btn btn-sm" onClick={exportDashboard} data-testid="dac-export">{t("dac_export")}</button>
                    <button type="button" className="btn btn-sm" onClick={loadHistory} data-testid="dac-history-load">{t("dac_history")}</button>
                    <button type="button" className="btn btn-sm" onClick={deleteDashboard} data-testid="dac-delete">{t("common_delete")}</button>
                  </>
                )}
              </div>
              <textarea className="field font-mono" style={{ minHeight: 260, fontFamily: "monospace", fontSize: 12 }}
                        value={text} onChange={(e) => setText(e.target.value)} spellCheck={false}
                        data-testid="dac-editor" />
              <IssueList issues={issues} />
            </div>

            {history.length > 0 && (
              <div className="panel flex flex-col gap-2">
                <div className="text-[13px] font-semibold">{t("dac_history")}</div>
                <div className="flex flex-wrap items-center gap-2">
                  <select className="field" style={{ width: "auto" }} value={diffSel[0]} onChange={(e) => setDiffSel([e.target.value, diffSel[1]])}>
                    <option value="">…</option>
                    {history.map((h) => <option key={h.id} value={h.id}>{new Date(h.saved_at * 1000).toLocaleString(lang)}</option>)}
                  </select>
                  <span className="help">→</span>
                  <select className="field" style={{ width: "auto" }} value={diffSel[1]} onChange={(e) => setDiffSel([diffSel[0], e.target.value])}>
                    <option value="current">{t("dac_current")}</option>
                    {history.map((h) => <option key={h.id} value={h.id}>{new Date(h.saved_at * 1000).toLocaleString(lang)}</option>)}
                  </select>
                  <button type="button" className="btn btn-sm" onClick={runDiff} data-testid="dac-diff-run">{t("dac_diff")}</button>
                </div>
                {diffText && <pre className="overflow-auto text-[11px]" style={{ maxHeight: 260 }}>{diffText}</pre>}
              </div>
            )}

            {rendered && (
              <div className="panel flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  {(rendered.filters || []).map((f) => (
                    <FilterControl key={f.name} filter={f} value={filterValues[f.name] ?? f.value}
                                    onChange={(v) => setFilterValues((prev) => ({ ...prev, [f.name]: v }))} />
                  ))}
                  {(rendered.filters || []).length > 0 && (
                    <button type="button" className="btn btn-sm" onClick={applyFilters} data-testid="dac-apply-filters">{t("common_apply")}</button>
                  )}
                </div>
                {rendered.warnings && rendered.warnings.length > 0 && (
                  <div className="help">{rendered.warnings.join(" · ")}</div>
                )}
                {tabs.length > 1 && (
                  <div className="flex gap-1">
                    {tabs.map((tab, i) => (
                      <button key={tab.name} type="button" className={`btn btn-sm ${i === activeTab ? "btn-primary" : ""}`}
                              onClick={() => setActiveTab(i)}>
                        {tab.name}
                      </button>
                    ))}
                  </div>
                )}
                {currentTab && (
                  <div className="flex flex-col gap-3">
                    {currentTab.rows.map((row, ri) => (
                      <div key={ri} className="grid gap-3" style={{ gridTemplateColumns: `repeat(${Math.min(row.cols || row.widgets.length || 1, 6)}, minmax(0, 1fr))` }}>
                        {row.widgets.map((w, wi) => <Widget key={wi} widget={w} lang={lang} />)}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
