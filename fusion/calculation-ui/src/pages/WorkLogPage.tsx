import { useEffect, useState } from "react";
import { BarChart3, RotateCw, Search, X } from "lucide-react";
import { api, type LogItem } from "../api";
import type { DictKey } from "../i18n";
import { useCopilotRefresh } from "../copilot";
import { CiteBadge, copyCite } from "../components/ResultView";

const ENGINES = ["calc", "math", "units", "stats", "dates", "data", "report", "log"];

export function WorkLogPage({ t }: { t: (k: DictKey) => string }) {
  const [items, setItems] = useState<LogItem[]>([]);
  const [engine, setEngine] = useState("");
  const [source, setSource] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<LogItem | null>(null);

  const load = (q = query) =>
    api.log({ limit: 50, engine: engine || undefined, source: source || undefined, query: q || undefined }).then((r) => setItems(r.items));
  useCopilotRefresh(() => load());

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine, source]);

  // search as you type, not only on Enter - debounced so it does not fire a
  // request per keystroke
  useEffect(() => {
    const timer = window.setTimeout(() => load(query), 300);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  async function rerun(id: string) {
    await api.rerun(id);
    load();
  }

  return (
    <div>
      <div className="card">
        <div className="row">
          <div className="row grow" style={{ background: "var(--bg-sunken)", borderRadius: 6, padding: "6px 10px", border: "1px solid var(--border)" }}>
            <Search size={14} className="faint" />
            <input
              type="search"
              className="grow"
              style={{ background: "none", border: "none", padding: 0 }}
              placeholder={t("common_search")}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && load()}
            />
          </div>
          <select value={engine} onChange={(e) => setEngine(e.target.value)}>
            <option value="">{t("log_all_engines")}</option>
            {ENGINES.map((e) => <option key={e} value={e}>{e}</option>)}
          </select>
          <select value={source} onChange={(e) => setSource(e.target.value)}>
            <option value="">{t("log_source_all")}</option>
            <option value="ui">{t("log_source_ui")}</option>
            <option value="agent">{t("log_source_agent")}</option>
          </select>
        </div>
      </div>

      <div className="card">
        {items.length === 0 ? (
          <div className="empty-state">{t("log_empty")}</div>
        ) : (
          <div className="table-scroll" style={{ maxHeight: 560 }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>id</th><th>{t("log_engine")}</th><th>{t("log_col_operation")}</th><th>{t("log_col_source")}</th><th>{t("log_col_status")}</th><th className="num">ms</th><th>{t("log_col_when")}</th><th></th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => (
                  <tr key={it.id} data-copilot-kind="computation" data-copilot-id={it.id}
                    style={{ cursor: "pointer" }} onClick={() => setSelected(it)}>
                    <td className="mono">{it.id}</td>
                    <td>{it.engine}</td>
                    <td className="mono">
                      {it.operation}
                      {it.chart_path && (
                        <span title={t("log_chart")}>
                          <BarChart3 size={12} className="faint" style={{ marginLeft: 6, verticalAlign: -1 }} />
                        </span>
                      )}
                    </td>
                    <td><span className={`badge ${it.source === "agent" ? "badge-ok" : "badge-muted"}`}>{it.source === "agent" ? t("log_source_agent") : t("log_source_ui")}</span></td>
                    <td>{it.ok ? <span className="badge badge-ok">ok</span> : <span className="badge badge-error">error</span>}</td>
                    <td className="num">{it.elapsed_ms.toFixed(1)}</td>
                    <td className="faint">{new Date(it.created_at).toLocaleString()}</td>
                    <td>
                      <button
                        className="icon-btn"
                        title={t("common_rerun")}
                        onClick={(e) => {
                          e.stopPropagation();
                          rerun(it.id);
                        }}
                      >
                        <RotateCw size={13} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selected && <DetailModal item={selected} onClose={() => setSelected(null)} t={t} onRerun={rerun} />}
    </div>
  );
}

function DetailModal({ item, onClose, t, onRerun }: { item: LogItem; onClose: () => void; t: (k: DictKey) => string; onRerun: (id: string) => void }) {
  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)",
        display: "flex", alignItems: "center", justifyContent: "center", zIndex: 30,
      }}
      onClick={onClose}
    >
      <div className="card" style={{ width: "min(640px, 92vw)", maxHeight: "80vh", overflow: "auto" }} onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
          <div className="row">
            <CiteBadge id={item.id} onClick={copyCite} title={t("common_copy_cite")} />
            <span className="badge badge-muted">{item.engine}</span>
            <code>{item.operation}</code>
          </div>
          <button className="icon-btn" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="faint" style={{ marginBottom: 4 }}>{t("log_input")}</div>
        <pre className="mono result-block" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(item.input, null, 2)}</pre>
        <div className="faint" style={{ margin: "10px 0 4px" }}>{t("log_output")}</div>
        <pre className="mono result-block" style={{ whiteSpace: "pre-wrap" }}>
          {item.ok ? JSON.stringify(item.output, null, 2) : item.error}
        </pre>
        {item.chart_path && (
          <>
            <div className="faint" style={{ margin: "10px 0 4px" }}>{t("log_chart")}</div>
            <img
              src={`${import.meta.env.BASE_URL}api/charts/${item.id}`}
              alt={t("log_chart")}
              style={{ maxWidth: "100%", borderRadius: 6, border: "1px solid var(--border)" }}
            />
          </>
        )}
        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn" onClick={() => onRerun(item.id)}>{t("common_rerun")}</button>
        </div>
      </div>
    </div>
  );
}
