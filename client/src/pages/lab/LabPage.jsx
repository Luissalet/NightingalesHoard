import React, { useEffect, useState } from "react";
import { useApp } from "../../App.jsx";
import { EmptyState } from "../../components/ui.jsx";
import { DatasetSelect } from "./labShared.jsx";
import LabExplore from "./LabExplore.jsx";
import LabModels from "./LabModels.jsx";
import LabDiagnose from "./LabDiagnose.jsx";
import LabOptimize from "./LabOptimize.jsx";
import LabCompare from "./LabCompare.jsx";
import LabPipeline from "./LabPipeline.jsx";
import LabReport from "./LabReport.jsx";

const TABS = [
  { id: "explore", labelKey: "lab_tab_explore", Component: LabExplore, needsDataset: true },
  { id: "models", labelKey: "lab_tab_models", Component: LabModels, needsDataset: true },
  { id: "diagnose", labelKey: "lab_tab_diagnose", Component: LabDiagnose, needsDataset: false },
  { id: "optimize", labelKey: "lab_tab_optimize", Component: LabOptimize, needsDataset: false },
  { id: "compare", labelKey: "lab_tab_compare", Component: LabCompare, needsDataset: false },
  { id: "pipeline", labelKey: "lab_tab_pipeline", Component: LabPipeline, needsDataset: true },
  { id: "report", labelKey: "lab_tab_report", Component: LabReport, needsDataset: true },
];

/** The Lab page: exploratory analysis, the model registry, diagnostics,
 * optimization, drift/curve comparison, the visual pipeline editor, and PDF
 * reports — everything under /api/lab/*. Route: #/lab/<tab>/<dataset>, e.g.
 * #/lab/explore/sales (both segments optional; a tab that mostly works off a
 * model id, like Diagnose/Optimize, still gets the dataset for context). */
export default function LabPage({ route }) {
  const { t, datasets } = useApp();
  const rest = route?.rest || [];
  const [tabId, setTabId] = useState(TABS.some((tb) => tb.id === rest[0]) ? rest[0] : "explore");
  const [dataset, setDataset] = useState(rest[1] || null);

  useEffect(() => {
    if (!dataset && datasets.length > 0) setDataset(datasets[0].name);
  }, [datasets, dataset]);

  // The Lab page itself never remounts as long as the top-level route stays
  // "lab" (App.jsx only swaps components on that first hash segment), so a
  // hash change that only touches the tab/dataset segments — a same-document
  // navigation from an external "Open in Lab" link, browser back/forward, or
  // a test driving the app by URL — has to be picked up here explicitly;
  // otherwise the tab/dataset state set by useState's one-time initializer
  // would silently go stale.
  useEffect(() => {
    const r = route?.rest || [];
    if (r[0] && TABS.some((tb) => tb.id === r[0]) && r[0] !== tabId) setTabId(r[0]);
    if (r[1] && r[1] !== dataset) setDataset(r[1]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route]);

  const goTab = (id) => {
    setTabId(id);
    window.location.hash = `#/lab/${id}/${dataset ? encodeURIComponent(dataset) : ""}`;
  };
  const changeDataset = (name) => {
    setDataset(name);
    window.location.hash = `#/lab/${tabId}/${encodeURIComponent(name)}`;
  };

  const tab = TABS.find((tb) => tb.id === tabId) || TABS[0];
  const Component = tab.Component;

  return (
    <div className="flex flex-col gap-4" data-testid="lab-page">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-lg font-semibold">{t("lab_title")}</h1>
        {datasets.length > 0 && (
          <DatasetSelect datasets={datasets} value={dataset} onChange={changeDataset} testId="lab-dataset-select" />
        )}
      </div>
      <p className="help -mt-2">{t("lab_tagline")}</p>

      <nav className="seg self-start flex-wrap" role="tablist" aria-label={t("lab_title")} data-testid="lab-tabs">
        {TABS.map((tb) => (
          <button key={tb.id} type="button" aria-pressed={tabId === tb.id} onClick={() => goTab(tb.id)} data-testid={`lab-tab-${tb.id}`}>
            {t(tb.labelKey)}
          </button>
        ))}
      </nav>

      {tab.needsDataset && datasets.length === 0 ? (
        <EmptyState>{t("datasets_empty")}</EmptyState>
      ) : (
        <Component dataset={dataset} datasets={datasets} setDataset={changeDataset} />
      )}
    </div>
  );
}
