import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { formatNumber } from "../format.js";

export default function SettingsPage() {
  const { t, lang, setLang, datasets } = useApp();
  const [available, setAvailable] = useState(null);
  const [question, setQuestion] = useState("");
  const [askDatasets, setAskDatasets] = useState([]);
  const [answer, setAnswer] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState(null);

  useEffect(() => {
    api.askAvailable().then(setAvailable).catch(() => {});
    api.status().then(setStatus).catch(() => {});
  }, []);

  const ask = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setAnswer(null);
    try {
      const r = await api.ask(question, askDatasets.length ? askDatasets : undefined);
      setAnswer(r);
    } catch (e2) {
      setError(e2.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-lg font-semibold">{t("settings_title")}</h1>

      <div className="panel flex flex-col gap-3">
        <label className="label">{t("settings_language")}</label>
        <div className="seg self-start" role="tablist">
          <button type="button" aria-pressed={lang === "en"} onClick={() => setLang("en")}>English</button>
          <button type="button" aria-pressed={lang === "es"} onClick={() => setLang("es")}>Español</button>
        </div>
      </div>

      {status && (
        <div className="panel flex flex-col gap-1 text-[13px]">
          <div className="text-[13px] font-semibold">Status</div>
          <div className="help">Service: {status.service} · version {status.version}</div>
          <div className="help">Data dir: {status.data_dir}</div>
        </div>
      )}

      <div className="panel flex flex-col gap-3">
        <div className="text-[13px] font-semibold">{t("settings_backend_title")}</div>
        <div className="help">{t("settings_backend_hint")}</div>
        {available && (
          <div className="help">
            {available.config_error
              ? <span style={{ color: "var(--danger-ink)" }}>{available.config_error}</span>
              : <span>Ready · capabilities used: {available.used_capabilities.join(", ") || "none yet"}</span>}
          </div>
        )}
      </div>

      <div className="panel flex flex-col gap-3">
        <div className="text-[13px] font-semibold">{t("ask_title")}</div>
        <form onSubmit={ask} className="flex flex-col gap-2">
          <textarea className="field" rows={2} placeholder={t("ask_placeholder")} value={question}
                    onChange={(e) => setQuestion(e.target.value)} data-testid="ask-question" />
          <div>
            <label className="label">{t("nav_datasets")}</label>
            <select className="field" multiple value={askDatasets} size={Math.min(5, datasets.length || 1)}
                    onChange={(e) => setAskDatasets(Array.from(e.target.selectedOptions, (o) => o.value))}>
              {datasets.map((d) => (
                <option key={d.name} value={d.name}>{d.name}</option>
              ))}
            </select>
            <div className="help">Leave empty to consider all datasets.</div>
          </div>
          <div>
            <button type="submit" className="btn btn-primary" disabled={busy || !question.trim()} data-testid="ask-submit">
              {busy ? t("common_loading") : t("common_run")}
            </button>
          </div>
        </form>
        {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}
        {answer && (
          <div className="panel-white flex flex-col gap-2" data-testid="ask-answer">
            <div className="help">Model: {answer.model}</div>
            {answer.sql && <pre className="mono overflow-auto text-[11px]" style={{ background: "var(--soft)", padding: 8, borderRadius: 6 }}>{answer.sql}</pre>}
            {answer.rows && answer.rows.length > 0 && (
              <div className="overflow-auto">
                <table className="w-full border-collapse text-[12px]">
                  <thead>
                    <tr>
                      {Object.keys(answer.rows[0]).map((c) => (
                        <th key={c} className="border-b p-1 text-left" style={{ borderColor: "var(--line)" }}>{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {answer.rows.slice(0, 20).map((row, i) => (
                      <tr key={i}>
                        {Object.keys(answer.rows[0]).map((c) => (
                          <td key={c} className="border-b p-1" style={{ borderColor: "var(--line)" }}>
                            {typeof row[c] === "number" ? formatNumber(row[c], lang) : String(row[c] ?? "")}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="help">{formatNumber(answer.row_count, lang)} row(s)</div>
          </div>
        )}
      </div>
    </div>
  );
}
