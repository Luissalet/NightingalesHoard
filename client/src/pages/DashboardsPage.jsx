import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { EmptyState } from "../components/ui.jsx";
import VegaChart from "../components/VegaChart.jsx";
import { formatNumber } from "../format.js";

export default function DashboardsPage() {
  const { t, lang, datasets, notify } = useApp();
  const [dashboards, setDashboards] = useState([]);
  const [active, setActive] = useState(null);
  const [detail, setDetail] = useState(null);
  const [name, setName] = useState("");
  const [charts, setCharts] = useState([]);
  const [pickChart, setPickChart] = useState("");
  const [kpiDataset, setKpiDataset] = useState("");
  const [kpiExpr, setKpiExpr] = useState("COUNT(*)");
  const [kpiLabel, setKpiLabel] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const loadList = async () => {
    try {
      const { dashboards } = await api.dashboardList();
      setDashboards(dashboards);
      if (!active && dashboards.length > 0) setActive(dashboards[0].id);
    } catch (e) {
      setError(e.message);
    }
  };
  const loadCharts = async () => {
    try {
      const { charts } = await api.chartList();
      setCharts(charts);
    } catch {
      /* charts optional */
    }
  };
  useEffect(() => {
    loadList();
    loadCharts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadDetail = async () => {
    if (!active) return setDetail(null);
    try {
      setDetail(await api.dashboardGet(active));
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  };
  useEffect(() => {
    loadDetail();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  const create = async (e) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const r = await api.dashboardCreate({ name });
      setName("");
      notify("Dashboard created");
      await loadList();
      setActive(r.dashboard_id);
    } catch (e2) {
      setError(e2.message);
    } finally {
      setBusy(false);
    }
  };

  const addChartItem = async () => {
    if (!active || !pickChart) return;
    try {
      await api.dashboardAddItem(active, { type: "chart", chart_id: Number(pickChart) });
      setPickChart("");
      await loadDetail();
    } catch (e) {
      setError(e.message);
    }
  };

  const addKpiItem = async () => {
    if (!active || !kpiDataset || !kpiExpr) return;
    try {
      await api.dashboardAddItem(active, { type: "kpi", dataset: kpiDataset, expr: kpiExpr, label: kpiLabel || kpiExpr });
      setKpiExpr("COUNT(*)");
      setKpiLabel("");
      await loadDetail();
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{t("dashboards_title")}</h1>
        {dashboards.length > 0 && (
          <select className="field" style={{ width: "auto" }} value={active || ""} onChange={(e) => setActive(Number(e.target.value))}>
            {dashboards.map((d) => (
              <option key={d.id} value={d.id}>{d.name}</option>
            ))}
          </select>
        )}
      </div>

      {error && <div className="help" style={{ color: "var(--danger-ink)" }}>{error}</div>}

      <form onSubmit={create} className="panel flex flex-wrap items-end gap-2" data-testid="dashboard-create-form">
        <div>
          <label className="label">{t("common_name")}</label>
          <input className="field" value={name} onChange={(e) => setName(e.target.value)} placeholder="Sales overview" />
        </div>
        <button type="submit" className="btn btn-primary" disabled={busy} data-testid="dashboard-create">{t("common_create")}</button>
      </form>

      {dashboards.length === 0 ? (
        <EmptyState>{t("dashboards_empty")}</EmptyState>
      ) : (
        detail && (
          <div className="flex flex-col gap-4">
            <div className="panel flex flex-col gap-3">
              <div className="text-[13px] font-semibold">{t("dashboards_add_item")}</div>
              <div className="flex flex-wrap items-end gap-2">
                <select className="field" style={{ width: "auto" }} value={pickChart} onChange={(e) => setPickChart(e.target.value)}>
                  <option value="">Select a chart…</option>
                  {charts.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </select>
                <button type="button" className="btn btn-sm" onClick={addChartItem} data-testid="dashboard-add-chart">Add chart</button>
              </div>
              <div className="flex flex-wrap items-end gap-2">
                <select className="field" style={{ width: "auto" }} value={kpiDataset} onChange={(e) => setKpiDataset(e.target.value)}>
                  <option value="">Dataset for KPI…</option>
                  {datasets.map((d) => (
                    <option key={d.name} value={d.name}>{d.name}</option>
                  ))}
                </select>
                <input className="field" style={{ width: "auto" }} placeholder="SQL expr, e.g. SUM(amount)" value={kpiExpr}
                       onChange={(e) => setKpiExpr(e.target.value)} />
                <input className="field" style={{ width: "auto" }} placeholder="Label" value={kpiLabel} onChange={(e) => setKpiLabel(e.target.value)} />
                <button type="button" className="btn btn-sm" onClick={addKpiItem} data-testid="dashboard-add-kpi">Add KPI</button>
              </div>
            </div>

            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              {(detail.items || []).map((item, i) =>
                item.type === "kpi" ? (
                  <div className="stat-tile" key={i} data-testid="dashboard-kpi">
                    <div className="help">{item.label || item.expr}</div>
                    <div className="stat-number">{item.error ? "—" : formatNumber(item.value, lang)}</div>
                  </div>
                ) : (
                  <div className="panel-white md:col-span-2" key={i} data-testid="dashboard-chart">
                    <div className="mb-2 font-medium">{item.chart?.name}</div>
                    {item.chart ? <VegaChart spec={item.chart.vega_lite} /> : <div className="help">Chart not found.</div>}
                  </div>
                ),
              )}
              {(!detail.items || detail.items.length === 0) && <EmptyState>{t("dashboards_add_item")}</EmptyState>}
            </div>
          </div>
        )
      )}
    </div>
  );
}
