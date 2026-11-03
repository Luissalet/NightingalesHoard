import React, { useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Chip } from "../components/ui.jsx";

const CATS = ["equal", "equal_within_tolerance", "different", "zero_vs_missing", "empty_vs_missing", "only_left_value", "only_right_value", "both_missing"];
const BAD = new Set(["different", "zero_vs_missing", "empty_vs_missing", "only_left_value", "only_right_value"]);

function Rows({ rows, t }) {
  if (!rows || rows.length === 0) return <p className="help">{t("rc_none")}</p>;
  const cols = Object.keys(rows[0]);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px]">
        <thead><tr>{cols.map((c) => <th key={c} className="text-left">{c === "row" ? "#" : c}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} className="mono">{r[c] === null ? <span className="help">∅</span> : String(r[c])}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

export default function ReconcilePage() {
  const { t, datasets, act } = useApp();
  const names = useMemo(() => datasets.map((d) => d.name), [datasets]);
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const [sug, setSug] = useState(null);
  const [keys, setKeys] = useState([]);
  const [compare, setCompare] = useState([]);
  const [opts, setOpts] = useState({ trim: false, case_insensitive: false, numeric: false, empty_as_missing: false, tolerance: "" });
  const [saveAs, setSaveAs] = useState("");
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState("");

  useEffect(() => {
    if (!left && names[0]) setLeft(names[0]);
    if (!right && names[1]) setRight(names[1]);
  }, [names]);

  const body = () => ({
    left, right, keys, compare,
    options: { ...opts, tolerance: opts.tolerance === "" ? 0 : Number(opts.tolerance) },
    sample: 10, save_as: saveAs || null,
  });

  const doSuggest = async () => {
    setBusy("suggest"); setResult(null);
    try {
      const s = await act(() => api.reconcileSuggest(left, right));
      setSug(s);
      const strong = s.key_candidates[0];
      setKeys(strong ? [{ left: strong.left, right: strong.right }] : []);
      const keyL = new Set(strong ? [strong.left] : []);
      setCompare(s.mapping.filter((m) => m.candidates[0] && !keyL.has(m.left)).map((m) => ({ left: m.left, right: m.candidates[0].right })));
    } finally { setBusy(""); }
  };
  const doRun = async () => {
    setBusy("run");
    try { setResult(await act(() => api.reconcileRun(body()), saveAs ? t("rc_saved") : null)); } finally { setBusy(""); }
  };
  const doExport = async () => {
    setBusy("export");
    try {
      const blob = await api.reconcileExport(body());
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = `reconcile-${left}-${right}.csv`; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    } finally { setBusy(""); }
  };

  const lcols = sug ? sug.left.columns.map((c) => c.name) : [];
  const rcols = sug ? sug.right.columns.map((c) => c.name) : [];
  const pairEditor = (list, setList, testid) => (
    <div className="flex flex-col gap-2" data-testid={testid}>
      {list.map((p, i) => (
        <div key={i} className="flex flex-wrap items-center gap-2">
          <select className="field" style={{ width: "auto" }} value={p.left} onChange={(e) => setList(list.map((x, j) => j === i ? { ...x, left: e.target.value } : x))}>{lcols.map((c) => <option key={c}>{c}</option>)}</select>
          <span className="help">↔</span>
          <select className="field" style={{ width: "auto" }} value={p.right} onChange={(e) => setList(list.map((x, j) => j === i ? { ...x, right: e.target.value } : x))}>{rcols.map((c) => <option key={c}>{c}</option>)}</select>
          <button className="btn btn-sm" onClick={() => setList(list.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <button className="btn btn-sm self-start" onClick={() => setList([...list, { left: lcols[0], right: rcols[0] }])} disabled={!sug}>{t("rc_add_pair")}</button>
    </div>
  );

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-lg font-semibold">{t("rc_title")}</h1>
        <p className="help mt-1">{t("rc_intro")}</p>
      </div>
      <div className="panel flex flex-wrap items-end gap-3" data-testid="rc-pick">
        <div><label className="label" htmlFor="rc-left">{t("rc_left")}</label>
          <select id="rc-left" className="field" value={left} onChange={(e) => { setLeft(e.target.value); setSug(null); setResult(null); }}>{names.map((n) => <option key={n}>{n}</option>)}</select></div>
        <div><label className="label" htmlFor="rc-right">{t("rc_right")}</label>
          <select id="rc-right" className="field" value={right} onChange={(e) => { setRight(e.target.value); setSug(null); setResult(null); }}>{names.map((n) => <option key={n}>{n}</option>)}</select></div>
        <button className="btn btn-primary" onClick={doSuggest} disabled={!left || !right || !!busy}>{busy === "suggest" ? t("rc_working") : t("rc_suggest")}</button>
      </div>

      {sug && (
        <div className="grid gap-6" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }}>
          <div className="panel flex flex-col gap-3" data-testid="rc-suggestions">
            <h2 className="font-semibold">{t("rc_suggestions")}</h2>
            <p className="help">{t("rc_suggest_note")}</p>
            <table className="w-full text-[13px]">
              <thead><tr><th className="text-left">{t("rc_left")}</th><th className="text-left">{t("rc_candidates")}</th></tr></thead>
              <tbody>{sug.mapping.map((m) => (
                <tr key={m.left}><td className="mono">{m.left}</td>
                  <td>{m.candidates.length ? m.candidates.map((c) => <span key={c.right} className="mr-2" title={`${t("rc_shared")}: ${c.shared_values} · ${t("rc_name_sim")}: ${c.name_similarity}`}><span className="mono">{c.right}</span> <span className="help">{Math.round(c.score * 100)}%</span></span>) : <span className="help">—</span>}</td></tr>
              ))}</tbody>
            </table>
            <h3 className="font-medium">{t("rc_key_candidates")}</h3>
            {sug.key_candidates.length === 0 && <p className="help">{t("rc_no_keys")}</p>}
            {sug.key_candidates.map((k) => (
              <div key={k.left + k.right} className="flex flex-wrap items-center gap-2">
                <span className="mono">{k.left} ↔ {k.right}</span>
                <Chip tone={k.strength === "strong" ? "ok" : "warn"}>{k.strength === "strong" ? t("rc_strong") : t("rc_weak")}</Chip>
                <span className="help">{k.issues.join("; ")}</span>
                <button className="btn btn-sm" onClick={() => setKeys([{ left: k.left, right: k.right }])}>{t("rc_use_key")}</button>
              </div>
            ))}
          </div>
          <div className="panel flex flex-col gap-3" data-testid="rc-setup">
            <h2 className="font-semibold">{t("rc_keys")}</h2>
            {pairEditor(keys, setKeys, "rc-keys")}
            <h2 className="font-semibold">{t("rc_compare")}</h2>
            {pairEditor(compare, setCompare, "rc-compare")}
            <h2 className="font-semibold">{t("rc_normalise")}</h2>
            <div className="flex flex-wrap gap-3">
              {["trim", "case_insensitive", "numeric", "empty_as_missing"].map((k) => (
                <label key={k} className="flex items-center gap-1 text-[13px]"><input type="checkbox" checked={opts[k]} onChange={(e) => setOpts({ ...opts, [k]: e.target.checked })} />{t("rc_opt_" + k)}</label>
              ))}
              <label className="flex items-center gap-1 text-[13px]">{t("rc_tolerance")}<input className="field num" style={{ width: 90 }} type="number" min="0" step="any" value={opts.tolerance} onChange={(e) => setOpts({ ...opts, tolerance: e.target.value })} /></label>
            </div>
            <div><label className="label" htmlFor="rc-save">{t("rc_save_as")}</label><input id="rc-save" className="field" value={saveAs} onChange={(e) => setSaveAs(e.target.value)} placeholder={`${left}_vs_${right}`} /></div>
            <div className="flex flex-wrap gap-2">
              <button className="btn btn-primary" onClick={doRun} disabled={!keys.length || !!busy}>{busy === "run" ? t("rc_working") : t("rc_run")}</button>
              <button className="btn" onClick={doExport} disabled={!keys.length || !!busy}>{t("rc_export")}</button>
            </div>
          </div>
        </div>
      )}

      {result && (
        <div className="flex flex-col gap-4" data-testid="rc-result">
          <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
            {[["left", result.left, ["paired", "only_left", "ambiguous", "missing_key"]], ["right", result.right, ["paired", "only_right", "ambiguous", "missing_key"]]].map(([side, s, keys2]) => (
              <div key={side} className="panel flex flex-col gap-1">
                <h2 className="font-semibold">{t("rc_" + side)} · <span className="mono">{s.dataset}</span> · {s.rows} {t("rc_rows")}</h2>
                {keys2.map((k) => <div key={k} className="flex justify-between text-[13px]"><span>{t("rc_b_" + k)}</span><span className={"num " + (k !== "paired" && s[k] ? "font-semibold" : "")}>{s[k]}</span></div>)}
                {s.duplicate_keys > 0 && <p className="help">{t("rc_dups")}: {s.duplicate_keys} · {t("rc_extra")}: {s.extra_copies}</p>}
              </div>
            ))}
          </div>
          <p className={result.accounted ? "help" : "font-semibold"}>{result.accounted ? t("rc_accounted") : t("rc_not_accounted")}</p>
          {result.columns.length > 0 && (
            <div className="panel overflow-x-auto" data-testid="rc-cells">
              <h2 className="font-semibold mb-2">{t("rc_cells")} ({result.pairs} {t("rc_pairs")})</h2>
              <table className="w-full text-[13px]">
                <thead><tr><th className="text-left">{t("rc_column")}</th>{CATS.map((c) => <th key={c} className="text-right">{t("rc_c_" + c)}</th>)}</tr></thead>
                <tbody>{result.columns.map((c) => (
                  <tr key={c.left + c.right}><td className="mono">{c.left} ↔ {c.right}</td>
                    {CATS.map((k) => <td key={k} className={"num text-right " + (BAD.has(k) && c.categories[k] ? "font-semibold" : "help")}>{c.categories[k]}</td>)}</tr>
                ))}</tbody>
              </table>
              {result.columns.filter((c) => c.examples.length).map((c) => (
                <div key={"ex" + c.left} className="mt-3">
                  <h3 className="font-medium">{c.left} ↔ {c.right}</h3>
                  <Rows t={t} rows={c.examples.map((e) => ({ ...e.key, [t("rc_left")]: e.left, [t("rc_right")]: e.right, [t("rc_category")]: t("rc_c_" + e.category) }))} />
                </div>
              ))}
            </div>
          )}
          {[["only_left", "rc_b_only_left"], ["only_right", "rc_b_only_right"], ["ambiguous_left", "rc_amb_left"], ["ambiguous_right", "rc_amb_right"], ["missing_key_left", "rc_miss_left"], ["missing_key_right", "rc_miss_right"]]
            .filter(([k]) => result.samples[k].length).map(([k, label]) => (
              <div key={k} className="panel" data-testid={"rc-" + k}><h2 className="font-semibold mb-2">{t(label)}</h2><Rows t={t} rows={result.samples[k]} /></div>
            ))}
          <div className="panel"><h2 className="font-semibold mb-1">{t("rc_how")}</h2><ul className="help list-disc pl-5">{result.explanation.map((e, i) => <li key={i}>{e}</li>)}</ul>
            {result.saved_dataset && <p className="mt-2">{t("rc_saved")}: <a className="mono" href={`#/datasets/${encodeURIComponent(result.saved_dataset)}`}>{result.saved_dataset}</a></p>}</div>
        </div>
      )}
    </div>
  );
}
