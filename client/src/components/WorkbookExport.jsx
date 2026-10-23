import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { displaySource, formatNumber } from "../format.js";

export default function WorkbookExport({ dataset, version, lang }) {
  const es = lang === "es";
  const [inventory, setInventory] = useState(null);
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(null);
  const request = useRef(0);
  useEffect(() => () => { request.current++; }, []);

  async function inspect() {
    const ticket = ++request.current;
    setBusy("inventory"); setError(null); setDone(null);
    try {
      const result = await api.workbookInventory(dataset);
      if (ticket === request.current) setInventory(result);
    } catch (cause) {
      if (ticket === request.current) setError(cause.message);
    } finally {
      if (ticket === request.current) setBusy(null);
    }
  }
  async function download() {
    const ticket = ++request.current;
    const selected = { dataset, version };
    setBusy("download"); setError(null); setDone(null);
    try {
      const blob = await api.workbookDownload(selected);
      if (ticket !== request.current) return;
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url;
      link.download = `${selected.dataset}_v${selected.version}_workbook.xlsx`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setDone(link.download);
    } catch (cause) {
      if (ticket === request.current) setError(cause.message);
    } finally {
      if (ticket === request.current) setBusy(null);
    }
  }
  const sheet = inventory?.sheets?.find(item => item.name === inventory.selected_sheet);
  const options = inventory?.selected_import_options;
  const unknown = es ? "No registrado" : "Not recorded";
  return <details className="panel" data-testid="workbook-export" onToggle={event => { if (event.currentTarget.open && !inventory && !busy) inspect(); }}>
    <summary className="cursor-pointer font-semibold">{es ? "Conservar libro original (.xlsx)" : "Preserve original workbook (.xlsx)"}</summary>
    <div className="mt-3 flex min-w-0 max-w-[900px] flex-col gap-3">
      <p className="break-words"><strong>{dataset}</strong> · v{version}</p>
      <p className="help max-w-[72ch]">{es ? "Descarga una copia del libro actualizando sólo los datos de esta hoja con la versión elegida. El archivo original no se sobrescribe; las demás hojas permanecen en la copia." : "Download a copy of the workbook, updating only this sheet's data with the selected version. The original file is not overwritten; the other sheets remain in the copy."}</p>
      {busy === "inventory" && <p className="help" role="status">{es ? "Consultando libro de origen…" : "Reading source workbook…"}</p>}
      {inventory && <>
        <dl className="grid min-w-0 gap-x-5 gap-y-2 text-[13px] sm:grid-cols-2">
          <div><dt className="help">{es ? "Libro de origen" : "Source workbook"}</dt><dd className="break-words">{displaySource(inventory.source_path) || unknown}</dd></div>
          <div><dt className="help">{es ? "Hoja de destino · dimensión registrada" : "Target sheet · recorded dimension"}</dt><dd className="break-words">{inventory.selected_sheet ?? unknown} · {sheet?.dimension ?? unknown}</dd></div>
          <div><dt className="help">{es ? "Importación registrada" : "Recorded import"}</dt><dd>{es ? "Cabecera en fila" : "Header at row"} {options?.header_row ?? unknown} · {formatNumber(options?.initial_data_rows, lang)} {es ? "filas" : "rows"} · {options?.columns?.length ?? unknown} {es ? "columnas" : "columns"}</dd></div>
          <div><dt className="help">{es ? "Pestañas registradas" : "Recorded sheets"}</dt><dd className="break-words">{inventory.sheets?.map(item => item.name).join(", ") || unknown}</dd></div>
        </dl>
        <p className="help">{inventory.source_matches_snapshot === true ? (es ? "El origen coincide con la huella registrada al importar." : "The source matches the fingerprint recorded at import.") : inventory.current_source_sha256 == null ? (es ? "La huella actual del origen no está disponible." : "The current source fingerprint is unavailable.") : (es ? "El origen no coincide con la huella registrada al importar." : "The source does not match the fingerprint recorded at import.")}</p>
        <details className="help"><summary className="cursor-pointer">{es ? "Ruta y huellas SHA-256" : "Path and SHA-256 fingerprints"}</summary>
          <dl className="mt-2 flex min-w-0 flex-col gap-2">
            <div><dt>{es ? "Ruta" : "Path"}</dt><dd className="break-all">{inventory.source_path}</dd></div>
            <div><dt>{es ? "Al importar" : "At import"}</dt><dd className="break-all">{inventory.source_sha256 ?? unknown}</dd></div>
            <div><dt>{es ? "Actual" : "Current"}</dt><dd className="break-all">{inventory.current_source_sha256 ?? unknown}</dd></div>
          </dl>
        </details>
      </>}
      <p className="help max-w-[72ch]">{es ? "Requiere un .xlsx importado con las mismas filas y columnas. No modifica fórmulas ni recalcula fórmulas o gráficos. Conserva validaciones sin comprobar valores. No es un backup del proyecto ni garantiza toda la fidelidad de Excel." : "Requires an imported .xlsx with the same rows and columns. It does not change formulas or recalculate formulas or charts. Validations are preserved without checking values. This is not a project backup or a guarantee of complete Excel fidelity."}</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn" onClick={inspect} disabled={!!busy}>{es ? "Consultar origen" : "Inspect source"}</button>
        {inventory && <button type="button" className="btn btn-primary" onClick={download} disabled={!!busy}>{busy === "download" ? (es ? "Generando libro…" : "Generating workbook…") : (es ? "Descargar libro XLSX" : "Download workbook XLSX")}</button>}
      </div>
      {error && <p role="alert" style={{ color: "var(--danger-ink)" }}>{error} {es ? "Puedes reintentar con el mismo dataset y versión." : "You can retry with the same dataset and version."}</p>}
      {done && <p className="help break-words" role="status">{es ? "Libro generado; descarga solicitada:" : "Workbook generated; download requested:"} {done}</p>}
    </div>
  </details>;
}
