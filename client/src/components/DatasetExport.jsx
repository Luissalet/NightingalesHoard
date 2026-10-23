import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { formatNumber } from "../format.js";

export default function DatasetExport({ dataset, version, rowCount, lang }) {
  const es = lang === "es";
  const [format, setFormat] = useState("csv");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(null);
  const request = useRef(0);
  useEffect(() => () => { request.current++; }, []);

  async function download(event) {
    event.preventDefault();
    const current = ++request.current;
    const selected = { dataset, version, format };
    setBusy(true); setError(null); setDone(null);
    try {
      const blob = await api.exportDownload(selected);
      if (current !== request.current) return;
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url;
      link.download = `${selected.dataset}_v${selected.version}.${selected.format}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setDone(link.download);
    } catch (cause) {
      if (current === request.current) setError(cause.message);
    } finally {
      if (current === request.current) setBusy(false);
    }
  }
  return <details className="panel" data-testid="dataset-export">
    <summary className="cursor-pointer font-semibold">{es ? "Exportar datos" : "Export data"}</summary>
    <form onSubmit={download} className="mt-3 flex flex-col gap-3">
      <p className="break-words"><strong>{dataset}</strong> · v{version} · {formatNumber(rowCount, lang)} {es ? "filas" : "rows"}</p>
      <p className="help">{es ? "Incluye todas las filas de esta versión, no sólo la vista previa. XLSX crea una hoja de datos: no conserva el libro original ni es una copia de seguridad del proyecto." : "Includes every row of this version, not just the preview. XLSX creates one data sheet: it does not preserve the original workbook and is not a project backup."}</p>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1"><span>{es ? "Formato" : "Format"}</span>
          <select className="field" value={format} disabled={busy} onChange={event => { setFormat(event.target.value); setDone(null); setError(null); }}>
            <option value="csv">CSV</option><option value="json">JSON</option><option value="parquet">Parquet</option><option value="xlsx">XLSX</option>
          </select>
        </label>
        <button className="btn btn-primary" disabled={busy}>{busy ? (es ? "Generando archivo…" : "Generating file…") : `${es ? "Descargar" : "Download"} ${format.toUpperCase()}`}</button>
      </div>
      {error && <p role="alert" style={{ color: "var(--danger-ink)" }}>{error} {es ? "Puedes reintentar con el mismo formato." : "You can retry with the same format."}</p>}
      {done && <p role="status" className="help break-words">{es ? "Archivo generado; descarga solicitada:" : "File generated; download requested:"} {done}</p>}
    </form>
  </details>;
}
