import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { EmptyState } from "../components/ui.jsx";

const KINDS = ["file", "folder", "url", "paste"];

export default function SourcesPage() {
  const { t, act } = useApp();
  const [sources, setSources] = useState([]);
  const [kind, setKind] = useState("file");
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [glob, setGlob] = useState("*.csv");
  const [fmt, setFmt] = useState("csv");
  const [optionsRaw, setOptionsRaw] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const { sources } = await api.sources();
    setSources(sources);
  };
  useEffect(() => {
    load();
  }, []);

  const submit = async (e) => {
    e.preventDefault();
    let options = {};
    if (optionsRaw.trim()) {
      try {
        options = JSON.parse(optionsRaw);
      } catch {
        act(() => Promise.reject(new Error("Options must be valid JSON")));
        return;
      }
    }
    const body = { kind, name: name || undefined, options };
    if (kind === "file" || kind === "folder") body.path = path;
    if (kind === "folder") body.glob = glob;
    if (kind === "url") {
      body.url = url;
      body.fmt = fmt;
    }
    if (kind === "paste") {
      body.text = text;
      body.fmt = fmt;
    }
    setBusy(true);
    try {
      await act(() => api.ingest(body), "Ingested");
      setPath("");
      setName("");
      setUrl("");
      setText("");
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">{t("sources_title")}</h1>
        <p className="help mt-1">CSV/TSV (incl. Spanish formats), XLSX/XLS, Parquet, JSON/NDJSON, SQLite, a folder glob, a URL, or pasted text.</p>
      </div>

      <form onSubmit={submit} className="panel flex flex-col gap-3" data-testid="ingest-form">
        <div className="seg" role="tablist" aria-label={t("sources_kind")}>
          {KINDS.map((k) => (
            <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>
              {t(`sources_${k}`)}
            </button>
          ))}
        </div>

        {(kind === "file" || kind === "folder") && (
          <div>
            <label className="label" htmlFor="src-path">{t("sources_path")}</label>
            <input id="src-path" className="field" value={path} onChange={(e) => setPath(e.target.value)}
                   placeholder="/absolute/path/to/file-or-folder" required />
          </div>
        )}
        {kind === "folder" && (
          <div>
            <label className="label" htmlFor="src-glob">Glob pattern</label>
            <input id="src-glob" className="field" value={glob} onChange={(e) => setGlob(e.target.value)} placeholder="*.csv" />
          </div>
        )}
        {kind === "url" && (
          <div>
            <label className="label" htmlFor="src-url">{t("sources_url")}</label>
            <input id="src-url" className="field" value={url} onChange={(e) => setUrl(e.target.value)}
                   placeholder="https://example.com/data.csv" required />
          </div>
        )}
        {kind === "paste" && (
          <div>
            <label className="label" htmlFor="src-text">{t("sources_paste")}</label>
            <textarea id="src-text" className="field" rows={6} value={text} onChange={(e) => setText(e.target.value)}
                      placeholder="a,b\n1,2" required />
          </div>
        )}
        {(kind === "url" || kind === "paste") && (
          <div>
            <label className="label" htmlFor="src-fmt">Format</label>
            <select id="src-fmt" className="field" value={fmt} onChange={(e) => setFmt(e.target.value)}>
              <option value="csv">CSV</option>
              <option value="json">JSON</option>
            </select>
          </div>
        )}
        <div>
          <label className="label" htmlFor="src-name">{t("sources_name")}</label>
          <input id="src-name" className="field" value={name} onChange={(e) => setName(e.target.value)} placeholder="(derived from source if empty)" />
        </div>
        <details>
          <summary className="help cursor-pointer">Advanced options (JSON)</summary>
          <textarea className="field mt-2" rows={3} value={optionsRaw} onChange={(e) => setOptionsRaw(e.target.value)}
                    placeholder='{"delimiter": ";", "encoding": "latin-1"}' />
        </details>
        <div>
          <button type="submit" className="btn btn-primary" disabled={busy} data-testid="ingest-submit">
            {busy ? t("common_loading") : t("sources_ingest")}
          </button>
        </div>
      </form>

      <div>
        <h2 className="mb-2 text-[13px] font-semibold uppercase tracking-wide" style={{ color: "var(--supporting-ink)" }}>
          {t("sources_title")}
        </h2>
        {sources.length === 0 ? (
          <EmptyState>{t("sources_empty")}</EmptyState>
        ) : (
          <div className="panel-white divide-y" style={{ borderColor: "var(--line)" }}>
            {sources.map((s) => (
              <div className="row" key={s.id}>
                <span className="chip">{s.kind}</span>
                <span className="font-medium">{s.name}</span>
                <span className="help truncate">{s.path}</span>
                {s.last_refreshed_at && <span className="help ml-auto shrink-0">refreshed {s.last_refreshed_at}</span>}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
