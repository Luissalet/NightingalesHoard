import { useState } from "react";
import {
  Sigma, Database, Ruler, BarChart3, ScrollText, Bot, Settings as SettingsIcon, Menu,
} from "lucide-react";
import { useLang, useTheme } from "./hooks";
import { useCopilotContext } from "./copilot";
import type { DictKey } from "./i18n";
import { NotebookPage } from "./pages/NotebookPage";
import { DataPage } from "./pages/DataPage";
import { UnitsDatesPage } from "./pages/UnitsDatesPage";
import { StatisticsPage } from "./pages/StatisticsPage";
import { WorkLogPage } from "./pages/WorkLogPage";
import { ActivityPage } from "./pages/ActivityPage";
import { SettingsPage } from "./pages/SettingsPage";

type Tab = "notebook" | "data" | "units" | "stats" | "log" | "activity" | "settings";

const NAV: { id: Tab; icon: typeof Sigma; labelKey: DictKey }[] = [
  { id: "notebook", icon: Sigma, labelKey: "nav_notebook" },
  { id: "data", icon: Database, labelKey: "nav_data" },
  { id: "units", icon: Ruler, labelKey: "nav_units" },
  { id: "stats", icon: BarChart3, labelKey: "nav_stats" },
  { id: "log", icon: ScrollText, labelKey: "nav_log" },
  { id: "activity", icon: Bot, labelKey: "nav_activity" },
  { id: "settings", icon: SettingsIcon, labelKey: "nav_settings" },
];

export default function App() {
  const [lang, setLang, t] = useLang();
  const [theme, setTheme] = useTheme();
  const [tab, setTab] = useState<Tab>("notebook");
  const [sidebarOpen, setSidebarOpen] = useState(false);
  useCopilotContext(tab, lang, theme);

  const activeLabel = t(NAV.find((n) => n.id === tab)!.labelKey);

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <div className="sidebar-brand">
          <div className="sidebar-brand-icon"><img src={`${import.meta.env.BASE_URL}favicon-192.png`} alt="" width={28} height={28} /></div>
          <div>
            <div className="sidebar-brand-name">{t("appName")}</div>
          </div>
        </div>
        {NAV.map((item) => (
          <button
            key={item.id}
            className={`nav-item ${tab === item.id ? "active" : ""}`}
            onClick={() => {
              setTab(item.id);
              setSidebarOpen(false);
            }}
          >
            <item.icon size={16} />
            {t(item.labelKey)}
          </button>
        ))}
      </aside>

      <div className="main">
        <div className="topbar">
          <div className="row">
            <button className="icon-btn menu-btn" onClick={() => setSidebarOpen((s) => !s)} aria-label="Menu">
              <Menu size={18} />
            </button>
            <span className="topbar-title">{activeLabel}</span>
          </div>
        </div>
        <div className="content">
          {tab === "notebook" && <NotebookPage t={t} />}
          {tab === "data" && <DataPage t={t} />}
          {tab === "units" && <UnitsDatesPage t={t} />}
          {tab === "stats" && <StatisticsPage t={t} />}
          {tab === "log" && <WorkLogPage t={t} />}
          {tab === "activity" && <ActivityPage t={t} />}
          {tab === "settings" && (
            <SettingsPage t={t} lang={lang} setLang={setLang} theme={theme} setTheme={setTheme} />
          )}
        </div>
      </div>
    </div>
  );
}
