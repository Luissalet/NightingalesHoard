import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { EmptyState } from "../components/ui.jsx";
import VegaChart from "../components/VegaChart.jsx";

const CHART_KINDS = ["bar", "grouped_bar", "stacked_bar", "line", "area", "scatter", "histogram", "box", "heatmap", "pie", "donut"];
const AGGS = ["sum", "avg", "count", "min", "max", "median"];

export default function ChartsPage() {
  const { t, datasets, notify } = useApp();
  const [active, setActive] = useState(null);
  const [kind, setKind] = useState("bar");
  const [x, setX] = useState("");
  const [y, setY] = useState("");
  const [agg, setAgg] = useState("sum");
  const [color, setColor] = useState("");
  const [title, setTitle] = useState("");
  const [charts, setCharts] = useState([]);
  const [rendered, setRendered] = useState({});
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!active && datasets.length > 0) setActive(datasets[0].name);
  }, [datasets, active]);

  const view = async (chartId) => {
    try {
      const result = await api.chartGet(chartId);
      setRendered((r) => ({ ...r, [chartId]: result.vega_lite }));
    } catch (e) {
      setError(e.message);
    }
  };

  const loadCharts = async () => {
    try {
      const { charts } = await api.chartList();
      setCharts(charts);
      // Render every saved chart right away: a hover-to-render pattern never
      // fires on a touch device, which would leave charts permanently blank
      // on mobile.
      charts.forEach((c) => view(c.id));
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    loadCharts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dataset = datasets.find((d) => d.name === active);
  const columnNames = (dataset?.columns || []).map((c) => c.name);

  const create = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const body = { dataset: active, kind, agg, title };
      if (x) body.x = x;
      if (y) body.y = y;
      if (color) body.color = color;
      const result = await api.chartCreate(body);
      setRendered((r) => ({ ...r, [result.chart_id]: result.vega_lite }));
      setTitle("");
      notify("Chart created");
      await loadCharts();
    } catch (e2) {
      setError(e2.message);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (chartId) => {
    try {
      await api.chartDelete(chartId);
      setRendered((r) => {
        const next = { ...r };
        delete next[chartId];
        return next;
      });
      await loadCharts();
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-lg font-semibold">{t("charts_title")}</h1>

      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}

      {datasets.length === 0 ? (
        <EmptyState>{t("datasets_empty")}</EmptyState>
      ) : (
        <form onSubmit={create} className="panel flex flex-col gap-3" data-testid="chart-form">
          <div className="text-[13px] font-semibold">{t("charts_builder")}</div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
            <div>
              <label className="label">{t("nav_datasets")}</label>
              <select className="field" value={active || ""} onChange={(e) => setActive(e.target.value)}>
                {datasets.map((d) => (
                  <option key={d.name} value={d.name}>{d.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">{t("charts_kind")}</label>
              <select className="field" value={kind} onChange={(e) => setKind(e.target.value)}>
                {CHART_KINDS.map((k) => (
                  <option key={k} value={k}>{k}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">{t("charts_agg")}</label>
              <select className="field" value={agg} onChange={(e) => setAgg(e.target.value)}>
                {AGGS.map((a) => (
                  <option key={a} value={a}>{a}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">{t("charts_x")}</label>
              <select className="field" value={x} onChange={(e) => setX(e.target.value)}>
                <option value="">—</option>
                {columnNames.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">{t("charts_y")}</label>
              <select className="field" value={y} onChange={(e) => setY(e.target.value)}>
                <option value="">—</option>
                {columnNames.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="label">{t("charts_color")}</label>
              <select className="field" value={color} onChange={(e) => setColor(e.target.value)}>
                <option value="">—</option>
                {columnNames.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
            </div>
          </div>
          <div>
            <label className="label">{t("common_name")}</label>
            <input className="field" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={`${active}: ${kind}`} />
          </div>
          <div>
            <button type="submit" className="btn btn-primary" disabled={busy} data-testid="chart-save">{t("charts_save")}</button>
          </div>
        </form>
      )}

      {charts.length === 0 ? (
        <EmptyState>{t("charts_empty")}</EmptyState>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2" data-testid="chart-list">
          {charts.map((c) => (
            <div key={c.id} className="panel-white flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <div className="font-medium">{c.name}</div>
                <button type="button" className="btn-link text-xs" onClick={() => remove(c.id)}>{t("common_delete")}</button>
              </div>
              {rendered[c.id] ? <VegaChart spec={rendered[c.id]} /> : <div className="help">{t("common_loading")}</div>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
