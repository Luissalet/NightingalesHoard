import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, setApiLang } from "./api.js";
import { detectLang, makeT, setLang as persistLang } from "./i18n.js";
import { Toast, Icon, ErrorBanner } from "./components/ui.jsx";
import { formatNumber } from "./format.js";
import { useCopilotRefresh } from "./copilot-hooks.js";
import SourcesPage from "./pages/SourcesPage.jsx";
import DatasetsPage from "./pages/DatasetsPage.jsx";
import QualityPage from "./pages/QualityPage.jsx";
import ChartsPage from "./pages/ChartsPage.jsx";
import StoryPage from "./pages/StoryPage.jsx";
import DashboardsPage from "./pages/DashboardsPage.jsx";
import DashboardCodePage from "./pages/DashboardCodePage.jsx";
import ModelsPage from "./pages/ModelsPage.jsx";
import LogPage from "./pages/LogPage.jsx";
import WorkbooksPage from "./pages/WorkbooksPage.jsx";
import ReconcilePage from "./pages/ReconcilePage.jsx";
import ScenariosPage from "./pages/ScenariosPage.jsx";
import FabQuotePage from "./pages/FabQuotePage.jsx";
import ProcessPage from "./pages/ProcessPage.jsx";
import ExperimentsPage from "./pages/ExperimentsPage.jsx";
import MigratePage from "./pages/MigratePage.jsx";
import SettingsPage from "./pages/SettingsPage.jsx";
import LabPage from "./pages/lab/LabPage.jsx";

const PAGES = [
  { path: "sources", labelKey: "nav_sources", icon: "M3 7l9-4 9 4-9 4-9-4zm0 5l9 4 9-4M3 17l9 4 9-4", component: SourcesPage },
  { path: "datasets", labelKey: "nav_datasets", icon: "M4 5h16v4H4zM4 11h16v8H4zM8 15h8", component: DatasetsPage },
  { path: "quality", labelKey: "nav_quality", icon: "M9 12l2 2 4-4m5 2a9 9 0 11-18 0 9 9 0 0118 0z", component: QualityPage },
  { path: "charts", labelKey: "nav_charts", icon: "M4 20V10m5 10V4m5 16v-8m5 8V7", component: ChartsPage },
  { path: "story", labelKey: "nav_story", icon: "M4 5h16M4 10h10M4 15h13M4 20h7", component: StoryPage },
  { path: "dashboards", labelKey: "nav_dashboards", icon: "M4 4h7v7H4zM13 4h7v4h-7zM13 11h7v9h-7zM4 14h7v6H4z", component: DashboardsPage },
  { path: "dashboards-code", labelKey: "nav_dashboards_code", icon: "M8 4l-5 8 5 8M16 4l5 8-5 8M13 3l-2 18", component: DashboardCodePage, hidden: true },
  { path: "workbooks", labelKey: "nav_workbooks", icon: "M4 4h16v16H4zM4 9h16M4 14h16M9 4v16M14 4v16", component: WorkbooksPage },
  { path: "reconcile", labelKey: "nav_reconcile", icon: "M4 7h11m0 0l-3-3m3 3l-3 3M20 17H9m0 0l3-3m-3 3l3 3", component: ReconcilePage },
  { path: "scenarios", labelKey: "nav_scenarios", icon: "M3 20h18M5 20V10m5 10V4m5 16v-7m5 7V8", component: ScenariosPage },
  { path: "fabquote", labelKey: "nav_fabquote", icon: "M12 2v4m0 12v4M4.9 4.9l2.8 2.8m8.6 8.6l2.8 2.8M2 12h4m12 0h4M4.9 19.1l2.8-2.8m8.6-8.6l2.8-2.8M9 9h6v6H9z", component: FabQuotePage },
  { path: "process", labelKey: "nav_process", icon: "M4 5h6v5H4zM14 5h6v5h-6zM9 14h6v5H9zM7 10v2h10v-2M12 12v2", component: ProcessPage },
  { path: "experiments", labelKey: "nav_experiments", icon: "M9 3v6.5L4.5 18a1.8 1.8 0 001.6 2.6h11.8a1.8 1.8 0 001.6-2.6L15 9.5V3M9 3h6M7 15l3-2 2 3 3-4", component: ExperimentsPage },
  { path: "migrate", labelKey: "nav_migrate", icon: "M3 5c0-1.1 3.6-2 8-2s8 .9 8 2-3.6 2-8 2-8-.9-8-2zM3 5v6c0 1.1 3.6 2 8 2m-8 0v6c0 1.1 3.6 2 8 2M21 13l-4 4m4 0l-4-4", component: MigratePage },
  { path: "models", labelKey: "nav_models", icon: "M12 2v6m0 8v6m10-10h-6M8 12H2m14.95-7.05l-4.24 4.24M9.3 14.7l-4.24 4.24m0-13.9l4.24 4.24m5.4 5.4l4.25 4.25", component: ModelsPage },
  { path: "lab", labelKey: "nav_lab", icon: "M9 3v6.5L4.5 18a1.8 1.8 0 001.6 2.6h11.8a1.8 1.8 0 001.6-2.6L15 9.5V3M9 3h6M8.5 15h7", component: LabPage },
  { path: "log", labelKey: "nav_log", icon: "M8 4h13M8 12h13M8 20h13M3 4h.01M3 12h.01M3 20h.01", component: LogPage },
  { path: "settings", labelKey: "nav_settings", icon: "M12 15a3 3 0 100-6 3 3 0 000 6zm7-3a7 7 0 01-.1 1.2l2 1.6-2 3.4-2.4-1a7.4 7.4 0 01-2 1.2l-.4 2.6H9.9l-.4-2.6a7.4 7.4 0 01-2-1.2l-2.4 1-2-3.4 2-1.6A7 7 0 015 12a7 7 0 01.1-1.2l-2-1.6 2-3.4 2.4 1c.6-.5 1.3-.9 2-1.2L9.9 3h4.2l.4 2.6c.7.3 1.4.7 2 1.2l2.4-1 2 3.4-2 1.6c.06.4.1.8.1 1.2z", component: SettingsPage },
];

const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

function useHashRoute() {
  const read = () => {
    const parts = window.location.hash.replace(/^#\/?/, "").split("/");
    return {
      page: parts[0] || "datasets",
      param: parts[1] ? decodeURIComponent(parts[1]) : null,
      // Every segment past the page, undecoded-split so a page needing more
      // than one (Lab: tab + dataset) can read its own route without every
      // other page's `param` contract changing.
      rest: parts.slice(1).map((p) => (p ? decodeURIComponent(p) : "")),
    };
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export default function App() {
  const route = useHashRoute();
  const [lang, setLangState] = useState(() => {
    const initial = detectLang();
    setApiLang(initial);
    return initial;
  });
  const [datasets, setDatasets] = useState([]);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);

  const t = useMemo(() => makeT(lang), [lang]);
  const setLang = useCallback((l) => {
    setApiLang(l);
    setLangState(l);
    persistLang(l);
    // the server keeps it too, so a story built by an agent without ?lang= comes out in this language
    api.setLanguage(l).catch(() => {});
  }, []);

  const refreshDatasets = useCallback(async () => {
    try {
      const { datasets } = await api.datasets();
      setDatasets(datasets);
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => {
    refreshDatasets();
    const timer = setInterval(refreshDatasets, 10000);
    return () => clearInterval(timer);
  }, [refreshDatasets]);

  useCopilotRefresh(refreshDatasets); // the agent may have ingested, cleaned or deleted datasets

  const notify = useCallback((message) => setToast(message), []);
  const act = useCallback(
    async (fn, okMessage) => {
      try {
        const result = await fn();
        if (okMessage) setToast(okMessage);
        await refreshDatasets();
        return result;
      } catch (e) {
        setToast(e.message);
        throw e;
      }
    },
    [refreshDatasets],
  );
  const value = useMemo(() => ({ t, lang, setLang, datasets, refreshDatasets, notify, act }), [t, lang, setLang, datasets, refreshDatasets, notify, act]);

  const page = PAGES.find((p) => p.path === route.page) || PAGES[1];
  const Component = page.component;

  return (
    <AppContext.Provider value={value}>
      <div className="min-h-dvh md:grid md:grid-cols-[220px_minmax(0,1fr)]">
        <aside className="sticky top-0 z-10 border-b md:h-dvh md:border-b-0 md:border-r" style={{ background: "var(--sidebar)", borderColor: "var(--line)" }}>
          <div className="flex items-center gap-2 px-4 py-3 md:px-5 md:py-5">
            <img src={`${import.meta.env.BASE_URL}icon-192.png`} alt="" width="32" height="32" className="h-8 w-8 rounded-md" />
            <div className="leading-tight">
              <div className="text-[15px] font-semibold">Nightingale's Hoard</div>
            </div>
          </div>
          <nav aria-label="Sections" className="flex gap-1 overflow-x-auto px-3 pb-2 md:flex-col md:px-3">
            {PAGES.filter((p) => !p.hidden).map((p) => (
              <a key={p.path} href={`#/${p.path}`} className="nav-link shrink-0" aria-current={p.path === page.path ? "page" : undefined}>
                <Icon d={p.icon} />
                {t(p.labelKey)}
              </a>
            ))}
          </nav>
          {datasets.length > 0 && (
            <div className="hidden px-5 pt-4 md:block">
              <div className="help text-[11px]">{t("nav_datasets")}</div>
              <div className="text-[13px]">
                <span className="num font-semibold">{datasets.length}</span> · <span className="num">{formatNumber(datasets.reduce((s, d) => s + (d.row_count || 0), 0), lang)}</span> {t("common_rows")}
              </div>
            </div>
          )}
        </aside>
        <main className="min-w-0 px-4 py-4 md:px-8 md:py-7">
          <ErrorBanner message={error ? `Could not reach Nightingale's Hoard: ${error}` : null} onRetry={refreshDatasets} />
          <Component param={route.param} route={route} />
        </main>
      </div>
      <Toast message={toast} onClose={() => setToast(null)} />
    </AppContext.Provider>
  );
}
