import { useEffect, useState } from "react";
import { api, ApiError, type DatasetSummary, type StatsResult } from "../api";
import type { DictKey } from "../i18n";
import { useCopilotRefresh } from "../copilot";
import { CiteBadge, ErrorBlock, Headline, KeyValues, copyCite, fmtNum } from "../components/ResultView";

type T = (k: DictKey) => string;

const TESTS = [
  "describe", "ttest_1samp", "ttest_ind", "ttest_rel", "mannwhitneyu", "wilcoxon",
  "chi2_contingency", "fisher_exact", "pearson", "spearman", "linregress",
  "proportion_ci", "normal_ci", "binom_test",
] as const;

// Inline mode: tests that need a second sample pasted in (`data2`).
const NEEDS_SECOND_SAMPLE = new Set(["ttest_ind", "ttest_rel", "mannwhitneyu", "wilcoxon", "pearson", "spearman", "linregress"]);
// Dataset mode: two-sample tests split by `group_by` instead of a second column.
const USES_GROUP_BY = new Set(["ttest_ind", "mannwhitneyu"]);
// Dataset mode: tests that need an explicit second column.
const NEEDS_COLUMN2 = new Set(["ttest_rel", "wilcoxon", "pearson", "spearman", "linregress"]);
const NEEDS_PROPORTION = new Set(["proportion_ci", "binom_test"]);
const TABLE_TESTS = new Set(["chi2_contingency", "fisher_exact"]);
const USES_MU = new Set(["ttest_1samp", "wilcoxon"]);
const USES_CONFIDENCE = new Set(["proportion_ci", "normal_ci", "linregress"]);

// the number worth reading first, per result shape
const HEADLINE_KEYS = ["p_value", "r", "rho", "slope", "mean", "proportion"];

export function StatisticsPage({ t }: { t: T }) {
  const [test, setTest] = useState<string>("ttest_ind");
  const [source, setSource] = useState<"inline" | "dataset">("inline");
  const [data1, setData1] = useState("12.1, 11.8, 12.6, 13.0, 12.4, 11.9, 12.8");
  const [data2, setData2] = useState("12.9, 13.4, 13.1, 12.7, 13.8, 13.2, 13.5");
  const [table, setTable] = useState("8, 2\n1, 9");
  const [datasets, setDatasets] = useState<DatasetSummary[]>([]);
  const [dataset, setDataset] = useState("");
  const [column, setColumn] = useState("");
  const [column2, setColumn2] = useState("");
  const [groupBy, setGroupBy] = useState("");
  const [where, setWhere] = useState("");
  const [successes, setSuccesses] = useState(45);
  const [trials, setTrials] = useState(100);
  const [mu, setMu] = useState(0);
  const [confidence, setConfidence] = useState(0.95);
  const [p0, setP0] = useState(0.5);
  const [result, setResult] = useState<StatsResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useCopilotRefresh(() => api.datasets().then(r => setDatasets(r.datasets)));

  useEffect(() => {
    api.datasets().then((r) => setDatasets(r.datasets)).catch(() => {});
  }, []);
  const columns = datasets.find((d) => d.name === dataset)?.columns ?? [];

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = { test };
      if (NEEDS_PROPORTION.has(test)) {
        payload.successes = successes;
        payload.trials = trials;
        if (test === "binom_test") payload.p0 = p0;
      } else if (TABLE_TESTS.has(test)) {
        payload.data = parseTable(table);
      } else if (source === "inline") {
        payload.data = parseNums(data1);
        if (NEEDS_SECOND_SAMPLE.has(test)) payload.data2 = parseNums(data2);
      } else {
        payload.dataset = dataset;
        payload.column = column;
        if (NEEDS_COLUMN2.has(test)) payload.column2 = column2;
        if (USES_GROUP_BY.has(test) && groupBy) payload.group_by = groupBy;
        if (where) payload.where = where;
      }
      if (USES_MU.has(test)) payload.mu = mu;
      if (USES_CONFIDENCE.has(test)) payload.confidence = confidence;
      setResult(await api.stats(payload));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  const headKey = result ? HEADLINE_KEYS.find((k) => typeof result[k] === "number") : undefined;

  return (
    <div className="grid-form-result">
      <div className="card">
        <h3 className="card-title">{t("stats_title")}</h3>
        <div className="stack">
          <div className="col">
            <label className="field-label">{t("stats_test")}</label>
            <select value={test} onChange={(e) => { setTest(e.target.value); setResult(null); setError(null); }}>
              {TESTS.map((tst) => <option key={tst} value={tst}>{t(`test_${tst}` as DictKey)}</option>)}
            </select>
          </div>

          {!NEEDS_PROPORTION.has(test) && !TABLE_TESTS.has(test) && (
            <div className="pill-select" style={{ alignSelf: "flex-start" }}>
              <button className={source === "inline" ? "active" : ""} onClick={() => setSource("inline")}>{t("stats_inline")}</button>
              <button className={source === "dataset" ? "active" : ""} onClick={() => setSource("dataset")}>{t("stats_dataset")}</button>
            </div>
          )}

          {NEEDS_PROPORTION.has(test) && (
            <div className="row">
              <NumField label={t("stats_successes")} value={successes} onChange={setSuccesses} />
              <NumField label={t("stats_trials")} value={trials} onChange={setTrials} />
              {test === "binom_test" && <NumField label={t("stats_p0")} value={p0} onChange={setP0} step={0.05} />}
            </div>
          )}

          {TABLE_TESTS.has(test) && (
            <div className="col">
              <label className="field-label">{t("stats_table")}</label>
              <textarea className="mono" value={table} onChange={(e) => setTable(e.target.value)} rows={3} />
            </div>
          )}

          {!NEEDS_PROPORTION.has(test) && !TABLE_TESTS.has(test) && source === "inline" && (
            <>
              <div className="col">
                <label className="field-label">{t("stats_data1")}</label>
                <textarea className="mono" value={data1} onChange={(e) => setData1(e.target.value)} rows={2} />
                <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{t("stats_data_hint")}</div>
              </div>
              {NEEDS_SECOND_SAMPLE.has(test) && (
                <div className="col">
                  <label className="field-label">{t("stats_data2")}</label>
                  <textarea className="mono" value={data2} onChange={(e) => setData2(e.target.value)} rows={2} />
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>{t("stats_data_hint")}</div>
                </div>
              )}
            </>
          )}

          {!NEEDS_PROPORTION.has(test) && !TABLE_TESTS.has(test) && source === "dataset" && (
            <>
              <div className="col">
                <label className="field-label">{t("stats_dataset_name")}</label>
                <select value={dataset} onChange={(e) => { setDataset(e.target.value); setColumn(""); setColumn2(""); setGroupBy(""); }}>
                  <option value="">{t("stats_choose_dataset")}</option>
                  {datasets.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}
                </select>
              </div>
              <div className="row">
                <ColumnSelect label={t("stats_column")} value={column} onChange={setColumn} columns={columns} />
                {NEEDS_COLUMN2.has(test) && <ColumnSelect label={t("stats_column2")} value={column2} onChange={setColumn2} columns={columns} />}
                {USES_GROUP_BY.has(test) && <ColumnSelect label={t("stats_group_by")} value={groupBy} onChange={setGroupBy} columns={columns} all />}
              </div>
              <div className="col">
                <label className="field-label">{t("stats_where")}</label>
                <input type="text" className="mono" value={where} onChange={(e) => setWhere(e.target.value)} placeholder="region = 'North'" />
              </div>
            </>
          )}

          {(USES_MU.has(test) || USES_CONFIDENCE.has(test)) && (
            <div className="row">
              {USES_MU.has(test) && <NumField label={t("stats_mu")} value={mu} onChange={setMu} />}
              {USES_CONFIDENCE.has(test) && <NumField label={t("stats_confidence")} value={confidence} onChange={setConfidence} step={0.01} />}
            </div>
          )}

          <button className="btn" onClick={run} disabled={busy}>{t("stats_run")}</button>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h3 className="card-title">{t("stats_result")}</h3>
          {result && <CiteBadge id={result.id} onClick={copyCite} title={t("common_copy_cite")} />}
        </div>
        {error && <ErrorBlock message={error} />}
        {!result && !error && <div className="result-empty">{t("stats_pick")}</div>}
        {result && (
          <>
            <div className="muted" style={{ marginBottom: 8, fontWeight: 600 }}>{String(result.test)}</div>
            {headKey && (
              <Headline
                label={headKey === "p_value" ? t("stats_p") : headKey.replace(/_/g, " ")}
                value={<span title={String(result[headKey])}>{fmtNum(result[headKey], 4)}</span>}
              />
            )}
            <KeyValues data={result} skip={["test", "interpretation", headKey ?? ""]} />
            {result.interpretation && <div className="interpretation">{String(result.interpretation)}</div>}
          </>
        )}
      </div>
    </div>
  );
}

function NumField({ label, value, onChange, step }: { label: string; value: number; onChange: (n: number) => void; step?: number }) {
  return (
    <div className="col grow">
      <label className="field-label">{label}</label>
      <input type="number" value={value} step={step} onChange={(e) => onChange(Number(e.target.value))} />
    </div>
  );
}

function ColumnSelect({ label, value, onChange, columns, all = false }: {
  label: string; value: string; onChange: (v: string) => void; columns: { name: string; type: string }[]; all?: boolean;
}) {
  const numeric = /INT|DOUBLE|FLOAT|DECIMAL|REAL|NUMERIC/i;
  const options = all ? columns : columns.filter((c) => numeric.test(c.type));
  return (
    <div className="col grow">
      <label className="field-label">{label}</label>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {options.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
      </select>
    </div>
  );
}

function parseNums(text: string): number[] {
  // "3,5; 4,2; 5,1" (Spanish decimal commas, values separated by ';' or a
  // newline) used to be split on every comma too, turning 4 values into 8
  // half-values. When the text has a ';' or a line break, split ONLY on
  // those and read a lone ',' inside each token as a decimal point; a plain
  // comma-separated list ("12.1, 11.8, 12.6") is unaffected.
  const hasExplicitSeparator = /[;\n]/.test(text);
  const parts = hasExplicitSeparator ? text.split(/[;\n]+/) : text.split(/[,\s]+/);
  return parts
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => (hasExplicitSeparator && /^-?\d+,\d+$/.test(s) ? s.replace(",", ".") : s))
    .map(Number)
    .filter((n) => !Number.isNaN(n));
}

function parseTable(text: string): number[][] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => parseNums(line));
}
