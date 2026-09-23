import React, { useState } from "react";
import { api } from "../../api.js";
import { useApp } from "../../App.jsx";
import VegaChart from "../../components/VegaChart.jsx";
import { Chip } from "../../components/ui.jsx";
import { formatNumber } from "../../format.js";
import { SectionCard, ColumnSelect, SimpleTable } from "./labShared.jsx";
import { driftBarSpec, lineSpec, barSpec } from "./vegaSpecs.js";

export default function LabCompare({ datasets }) {
  const { t } = useApp();
  const [mode, setMode] = useState("drift");
  return (
    <div className="flex flex-col gap-4" data-testid="lab-compare">
      <nav className="seg self-start" role="tablist">
        <button type="button" aria-pressed={mode === "drift"} onClick={() => setMode("drift")}>{t("lab_compare_tab_drift")}</button>
        <button type="button" aria-pressed={mode === "curves"} onClick={() => setMode("curves")}>{t("lab_compare_tab_curves")}</button>
      </nav>
      {mode === "drift" ? <DriftPanel datasets={datasets} /> : <CurvesPanel datasets={datasets} />}
    </div>
  );
}

function DriftPanel({ datasets }) {
  const { t, lang } = useApp();
  const [a, setA] = useState(datasets[0]?.name || "");
  const [b, setB] = useState(datasets[1]?.name || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.lab.drift({ dataset: a, other_dataset: b });
      setResult(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <SectionCard testId="lab-drift">
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        <div>
          <label className="label">{t("lab_compare_dataset_a")}</label>
          <select className="field" value={a} onChange={(e) => setA(e.target.value)} data-testid="lab-drift-a">
            {datasets.map((d) => (
              <option key={d.name} value={d.name}>{d.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">{t("lab_compare_dataset_b")}</label>
          <select className="field" value={b} onChange={(e) => setB(e.target.value)} data-testid="lab-drift-b">
            {datasets.map((d) => (
              <option key={d.name} value={d.name}>{d.name}</option>
            ))}
          </select>
        </div>
        <div className="flex items-end">
          <button type="button" className="btn btn-primary" onClick={run} disabled={busy || !a || !b || a === b} data-testid="lab-drift-run">
            {busy ? t("common_loading") : t("lab_compare_run")}
          </button>
        </div>
      </div>
      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}
      {result && (
        <div className="flex flex-col gap-3" data-testid="lab-drift-result">
          <div className="help">{t("lab_compare_flagged")}: <b className="num">{result.n_flagged}</b> / {result.columns.length}</div>
          <SimpleTable
            columns={[
              { key: "column", label: t("lab_explore_nulls_column") },
              { key: "ks_statistic", label: t("lab_compare_ks") },
              { key: "psi", label: t("lab_compare_psi") },
              { key: "wasserstein_distance", label: t("lab_compare_wasserstein") },
              { key: "mean_shift", label: t("lab_compare_mean_shift") },
              { key: "flagged", label: t("lab_compare_flagged") },
            ]}
            keyField="column"
            rows={result.columns}
            renderCell={(row, key) => {
              if (key === "flagged") return row.flagged ? <Chip tone="danger">{t("lab_compare_flagged")}</Chip> : "—";
              const v = row[key];
              return typeof v === "number" ? formatNumber(v, lang, { maximumFractionDigits: 4 }) : v;
            }}
          />
          <VegaChart spec={driftBarSpec(result.columns)} height={Math.max(180, result.columns.length * 30)} />
        </div>
      )}
    </SectionCard>
  );
}

function CurvesPanel({ datasets }) {
  const { t, lang } = useApp();
  const [dataset, setDataset] = useState(datasets[0]?.name || "");
  const [x, setX] = useState("");
  const [y, setY] = useState("");
  const [group, setGroup] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const columns = (datasets.find((d) => d.name === dataset)?.columns || []).map((c) => c.name);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.lab.compareCurves({ dataset, x, y, group: group || undefined });
      setResult(r);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  const groups = result?.groups ? Object.entries(result.groups) : result?.series ? [["series", result.series]] : [];
  const overlayRows = groups.flatMap(([name, g]) => (g.overlay || []).map((p) => ({ x: p.x, y: p.y, series: name })));

  return (
    <SectionCard testId="lab-curves">
      <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
        <div>
          <label className="label">{t("lab_dataset")}</label>
          <select className="field" value={dataset} onChange={(e) => setDataset(e.target.value)}>
            {datasets.map((d) => (
              <option key={d.name} value={d.name}>{d.name}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">{t("lab_compare_curve_x")}</label>
          <ColumnSelect columns={columns} value={x} onChange={setX} allowEmpty={false} />
        </div>
        <div>
          <label className="label">{t("lab_compare_curve_y")}</label>
          <ColumnSelect columns={columns} value={y} onChange={setY} allowEmpty={false} />
        </div>
        <div>
          <label className="label">{t("lab_compare_curve_group")}</label>
          <ColumnSelect columns={columns} value={group} onChange={setGroup} />
        </div>
      </div>
      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}
      <div>
        <button type="button" className="btn btn-primary" onClick={run} disabled={busy || !x || !y} data-testid="lab-curves-run">
          {busy ? t("common_loading") : t("lab_compare_run")}
        </button>
      </div>
      {result && (
        <div className="flex flex-col gap-3" data-testid="lab-curves-result">
          <VegaChart spec={lineSpec(overlayRows, { xTitle: x, yTitle: y })} />
          <VegaChart spec={barSpec(groups.map(([name, g]) => ({ label: name, value: g.noise_ratio })), { sort: null, yTitle: t("lab_compare_noise") })} height={180} />
          <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {groups.map(([name, g]) => (
              <div key={name} className="panel text-[12.5px]">
                <div className="font-medium mb-1">{name}</div>
                <div>{t("lab_compare_growth")}: {formatNumber(g.growth_rate?.mean, lang, { maximumFractionDigits: 4 })}</div>
                <div>{t("lab_compare_gaps")}: <span className="num">{g.gaps?.length ?? 0}</span></div>
              </div>
            ))}
          </div>
          {result.systematic_bias && (
            <div className="help">{t("lab_compare_bias")}: {result.systematic_bias.group_a} vs {result.systematic_bias.group_b} — mean {formatNumber(result.systematic_bias.mean_bias, lang, { maximumFractionDigits: 4 })}</div>
          )}
        </div>
      )}
    </SectionCard>
  );
}
