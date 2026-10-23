import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

export default function ChartDownload({ chart, dataset, lang, t }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [started, setStarted] = useState(null);
  const request = useRef(0);
  useEffect(() => () => { request.current++; }, []);

  const download = async () => {
    const current = ++request.current;
    const selected = { id: chart.id, lang };
    setBusy(true); setError(""); setStarted(null);
    try {
      const result = await api.chartGet(selected.id, true, selected.lang);
      if (request.current !== current) return;
      const bytes = Uint8Array.from(atob(result.image_base64), (character) => character.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: "image/png" }));
      const link = document.createElement("a");
      link.href = url; link.download = `chart_${selected.id}_${selected.lang}.png`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setStarted(link.download);
    } catch (failure) { if (request.current === current) setError(failure.message); }
    finally { if (request.current === current) setBusy(false); }
  };

  return <div className="flex flex-col items-start gap-2" data-testid={`chart-png-${chart.id}`}>
    <p className="help break-words">ID {chart.id} · {dataset || t("charts_png_dataset_unknown")} · PNG {lang.toUpperCase()}</p>
    <button type="button" className="btn btn-sm" disabled={busy} onClick={download} aria-label={`${busy ? t("charts_png_busy") : error ? t("charts_png_retry") : t("charts_png_download")} · ${chart.name} · ID ${chart.id}`}>
      {busy ? t("charts_png_busy") : error ? t("charts_png_retry") : t("charts_png_download")}
    </button>
    {error && <p className="help break-words" role="alert" style={{ color: "var(--danger-ink)" }}>{t("charts_png_error")} {error}</p>}
    {started && <p className="help break-words" role="status">{t("charts_png_started")} {started}</p>}
  </div>;
}
