import { useEffect, useRef, useState, type ReactNode } from "react";
import { Pencil, Play } from "lucide-react";
import { api, ApiError, type Cell } from "../api";
import { useCopilotRefresh } from "../copilot";
import type { DictKey } from "../i18n";
import { CiteBadge, ConfirmDelete, ErrorBlock, Latex, VerifiedBadge, copyCite, fmtNum } from "../components/ResultView";

const ENGINES = ["calc", "math", "units", "dates"] as const;
type Engine = (typeof ENGINES)[number];

const PLACEHOLDER_KEY: Record<Engine, DictKey> = {
  calc: "notebook_placeholder_calc",
  math: "notebook_placeholder_math",
  units: "notebook_placeholder_units",
  dates: "notebook_placeholder_dates",
};

type T = (k: DictKey) => string;

type VisibleCell = Cell & { unavailable?: boolean };

export function NotebookPage({ t }: { t: T }) {
  const [cells, setCells] = useState<VisibleCell[]>([]);
  const editingIds = useRef(new Set<number>());
  const [engine, setEngine] = useState<Engine>("calc");
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const refreshCells = () => api.cells().then(r => {
    const ids = new Set(r.cells.map(cell => cell.id));
    setCells(previous => [...r.cells, ...previous.filter(cell => !ids.has(cell.id) && editingIds.current.has(cell.id))
      .map(cell => ({ ...cell, unavailable: true }))]);
  });
  useCopilotRefresh(refreshCells);

  useEffect(() => {
    api.cells().then((r) => {
      setCells(r.cells);
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, []);

  async function addCell() {
    if (!input.trim() || busy) return;
    setBusy(true);
    setAddError(null);
    try {
      // the backend always answers 200 with an error-shaped result for a
      // bad expression; a thrown error here means the request itself
      // failed (network, or a genuine server bug) and must still be shown
      const sentInput = input.trim();
      await api.createCell(engine, sentInput);
      await refreshCells();
      setInput(value => value.trim() === sentInput ? "" : value);
    } catch (e) {
      setAddError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function removeCell(id: number) {
    editingIds.current.delete(id);
    setCells((c) => c.filter((x) => x.id !== id));
    try {
      await api.deleteCell(id);
    } catch {
      /* best effort */
    }
  }

  return (
    <div>
      <div className="card" data-copilot-editor data-copilot-new data-copilot-engine={engine}>
        <div className="row" style={{ marginBottom: 10, justifyContent: "space-between" }}>
          <div className="pill-select">
            {ENGINES.map((e) => (
              <button key={e} className={engine === e ? "active" : ""} onClick={() => setEngine(e)}>
                {e}
              </button>
            ))}
          </div>
          <span className="faint">{t("notebook_hint")}</span>
        </div>
        <div className="row">
          <input
            type="text"
            name="input"
            className="grow mono"
            placeholder={t(PLACEHOLDER_KEY[engine])}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addCell()}
          />
          <button className="btn" disabled={busy || !input.trim()} onClick={addCell}>
            <Play size={14} style={{ verticalAlign: -2, marginRight: 4 }} />
            {t("notebook_add_cell")}
          </button>
        </div>
        {addError && <ErrorBlock message={addError} />}
      </div>

      <div style={{ marginTop: 16 }}>
        {loaded && cells.length === 0 && <div className="empty-state">{t("notebook_empty")}</div>}
        {[...cells].reverse().map((cell) => (
          <CellCard key={cell.id} cell={cell} onDelete={removeCell}
            onUpdated={refreshCells} onEditingChange={editing => {
              if (editing) editingIds.current.add(cell.id);
              else { editingIds.current.delete(cell.id); if (cell.unavailable) refreshCells(); }
            }} t={t} />
        ))}
      </div>
    </div>
  );
}

function CellCard({ cell, onDelete, onUpdated, onEditingChange, t }: {
  cell: VisibleCell; onDelete: (id: number) => void; onUpdated: () => unknown;
  onEditingChange: (editing: boolean) => void; t: T;
}) {
  const [editing, setEditing] = useState(false);
  const [engine, setEngine] = useState(cell.engine);
  const [input, setInput] = useState(cell.input);
  const [saving, setSaving] = useState(false);
  const [editError, setEditError] = useState("");
  const baseline = useRef({ engine: cell.engine, input: cell.input });
  useEffect(() => {
    const previous = baseline.current;
    setEngine(value => value === previous.engine ? cell.engine : value);
    setInput(value => value === previous.input ? cell.input : value);
    baseline.current = { engine: cell.engine, input: cell.input };
  }, [cell.engine, cell.input]);
  async function save() {
    const patch: { engine?: string; input?: string } = {};
    if (engine !== baseline.current.engine) patch.engine = engine;
    if (input !== baseline.current.input) patch.input = input;
    setSaving(true);
    setEditError("");
    try {
      if (Object.keys(patch).length) await api.patchCell(cell.id, patch);
      await onUpdated();
      setEditing(false);
      onEditingChange(false);
    } catch (error) {
      setEditError(error instanceof Error ? error.message : String(error));
    } finally { setSaving(false); }
  }
  const result = cell.result as Record<string, unknown> | null;
  const isError = result && "error" in result && "message" in result;
  return (
    <div className="cell" data-copilot-kind="cell" data-copilot-id={String(cell.id)}>
      <div className="cell-header">
        <div className="row">
          <span className="badge badge-muted">{cell.engine}</span>
          <code>{cell.input}</code>
        </div>
        <div className="row" style={{ gap: 4 }}>
          <button className="icon-btn" aria-label={t("cell_edit")} onClick={() => {
            setEditing(true); onEditingChange(true);
          }}>
            <Pencil size={14} />
          </button>
          {result && typeof result.id === "string" && <CiteBadge id={result.id} onClick={copyCite} title={t("common_copy_cite")} />}
          <ConfirmDelete onConfirm={() => onDelete(cell.id)} label={t("common_delete")} confirmLabel={t("common_confirm_delete")} />
        </div>
      </div>
      {editing && <div className="stack" data-copilot-editor data-copilot-engine={engine}>
        {cell.unavailable && <ErrorBlock message={t("cell_unavailable")} />}
        <select aria-label={t("cell_engine")} name="engine" disabled={saving}
          value={engine} onChange={event => setEngine(event.target.value)}>
          {ENGINES.map(value => <option key={value} value={value}>{value}</option>)}
        </select>
        <input aria-label={t("cell_input")} name="input" className="mono" disabled={saving}
          value={input} onChange={event => setInput(event.target.value)} />
        <div className="row">
          <button className="btn" disabled={saving} onClick={save}>{t("cell_save")}</button>
          <button className="btn-ghost" disabled={saving} onClick={() => {
            setEditing(false); onEditingChange(false); setEngine(cell.engine); setInput(cell.input); setEditError("");
          }}>{t("common_cancel")}</button>
        </div>
        {editError && <ErrorBlock message={editError} />}
      </div>}
      {result && isError && <ErrorBlock message={String((result as { message?: string }).message ?? "error")} />}
      {result && !isError && <CellResult result={result} t={t} />}
      {!result && <ErrorBlock message={t("notebook_no_result")} />}
    </div>
  );
}

type Solution = { values: Record<string, string>; numeric: Record<string, number | string | null>; verified: boolean };

function CellResult({ result, t }: { result: Record<string, unknown>; t: T }) {
  // math.solve: a list of solutions, each verified by substitution
  if (Array.isArray(result.result)) {
    const sols = result.result as Solution[];
    return (
      <div className="result-block">
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
          <span className="faint">{sols.length} {t("notebook_solutions")}</span>
          <VerifiedBadge verified={result.verified as boolean | null} verifiedLabel={t("notebook_verified")} unverifiedLabel={t("notebook_unverified")} />
        </div>
        {sols.map((s, i) => (
          <div key={i} className="row result-value" style={{ gap: 18 }}>
            {Object.entries(s.values).map(([k, v]) => (
              <span key={k}>
                {k} = {v}
                {typeof s.numeric[k] === "number" && String(s.numeric[k]) !== v && (
                  <span className="faint"> ≈ {fmtNum(s.numeric[k], 10)}</span>
                )}
              </span>
            ))}
          </div>
        ))}
      </div>
    );
  }

  const latex = result.latex as string | null | undefined;
  const exact = result.exact as string | undefined;
  const decimal = result.decimal as string | null | undefined;
  const formatted = result.formatted as string | undefined;
  const isExact = result.is_exact as boolean | undefined;

  let main: ReactNode;
  if (formatted) main = <span>{formatted}</span>;
  else if (latex) main = <Latex tex={latex} display />;
  else if (exact !== undefined) main = <span>{exact}</span>;
  else if (typeof result.result === "string") main = <span>{result.result}</span>;
  else if (result.date) main = <span>{String(result.date)} · {String(result.weekday ?? "")}</span>;
  else main = <span>{JSON.stringify(result)}</span>;

  return (
    <div className="result-block">
      <div className="result-value" style={{ fontSize: 17 }}>{main}</div>
      {decimal !== undefined && decimal !== null && exact !== undefined && decimal !== exact && (
        <div className="muted mono" style={{ marginTop: 6, fontSize: 13 }}>
          <span className="eq">{isExact ? "=" : "≈"}</span>
          {decimal}
        </div>
      )}
      {typeof result.warning === "string" && (
        // e.g. "1.000" read as 1.0: the result is right for what was typed,
        // but maybe not for what was meant - never let that pass silently
        <div role="note" style={{ marginTop: 8, fontSize: 13, color: "var(--danger)" }}>
          ⚠ {result.warning}
        </div>
      )}
    </div>
  );
}
