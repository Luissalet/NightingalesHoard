import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { EmptyState, Chip } from "../components/ui.jsx";

export default function LogPage() {
  const { t } = useApp();
  const [entries, setEntries] = useState([]);
  const [q, setQ] = useState("");
  const [assistantOnly, setAssistantOnly] = useState(false);
  const [openId, setOpenId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      const params = { limit: 100 };
      if (q) params.q = q;
      if (assistantOnly) params.source = "agent";
      const r = await api.log(params);
      setEntries(r.entries);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistantOnly]);

  const openEntry = async (id) => {
    if (openId === id) {
      setOpenId(null);
      return;
    }
    setOpenId(id);
    try {
      setDetail(await api.logGet(id));
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">{t("log_title")}</h1>

      <div className="flex flex-wrap items-center gap-3">
        <input className="field" style={{ width: "auto", minWidth: 220 }} placeholder={t("common_search")} value={q}
               onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && load()} />
        <button type="button" className="btn btn-sm" onClick={load}>{t("common_search")}</button>
        <label className="flex items-center gap-2 text-[13px]">
          <input type="checkbox" checked={assistantOnly} onChange={(e) => setAssistantOnly(e.target.checked)} />
          {t("log_assistant_only")}
        </label>
      </div>

      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}

      {entries.length === 0 ? (
        <EmptyState>{t("log_empty")}</EmptyState>
      ) : (
        <div className="panel-white divide-y" style={{ borderColor: "var(--line)" }} data-testid="log-entries">
          {entries.map((e) => (
            <div key={e.op_id}>
              <div className="row cursor-pointer flex-wrap" onClick={() => openEntry(e.op_id)}>
                <span className="mono help">{e.op_id}</span>
                <Chip tone={e.source === "agent" ? "accent" : undefined}>{e.source}</Chip>
                <span className="chip">{e.op}</span>
                <span className="truncate">{e.dataset || ""}</span>
                <Chip tone={e.ok ? "ok" : "danger"}>{e.ok ? "ok" : "error"}</Chip>
                <span className="help ml-auto shrink-0 num">{e.elapsed_ms ? `${Math.round(e.elapsed_ms)} ms` : ""}</span>
              </div>
              {openId === e.op_id && detail && (
                <pre className="mono overflow-auto p-3 text-[11px]" style={{ background: "var(--soft)" }}>
                  {JSON.stringify(detail, null, 2)}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
