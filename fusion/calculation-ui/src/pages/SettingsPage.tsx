import { useEffect, useRef, useState } from "react";
import { MessageSquareText, RefreshCw } from "lucide-react";
import { api, ApiError, type BackendStatus, type HealthInfo, type BackendConfigUpdate } from "../api";
import { useCopilotRefresh } from "../copilot";
import type { Lang } from "../i18n";
import type { DictKey } from "../i18n";
import type { Theme } from "../hooks";

export function SettingsPage({
  t, lang, setLang, theme, setTheme,
}: {
  t: (k: DictKey) => string;
  lang: Lang;
  setLang: (l: Lang) => void;
  theme: Theme;
  setTheme: (t: Theme) => void;
}) {
  const [health, setHealth] = useState<HealthInfo | null>(null);
  useCopilotRefresh(() => api.health().then(setHealth));

  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
  }, []);

  return (
    <div className="stack" style={{ maxWidth: 480 }}>
      <div className="card">
        <h3 className="card-title">{t("settings_language")}</h3>
        <div className="pill-select">
          <button className={lang === "en" ? "active" : ""} onClick={() => setLang("en")}>English</button>
          <button className={lang === "es" ? "active" : ""} onClick={() => setLang("es")}>Español</button>
        </div>
      </div>

      <div className="card">
        <h3 className="card-title">{t("settings_theme")}</h3>
        <div className="pill-select">
          <button className={theme === "system" ? "active" : ""} onClick={() => setTheme("system")}>{t("settings_theme_system")}</button>
          <button className={theme === "light" ? "active" : ""} onClick={() => setTheme("light")}>{t("settings_theme_light")}</button>
          <button className={theme === "dark" ? "active" : ""} onClick={() => setTheme("dark")}>{t("settings_theme_dark")}</button>
        </div>
      </div>

      <ModelsPanel t={t} />

      <div className="card">
        <h3 className="card-title">{t("settings_about")}</h3>
        {health ? (
          <div className="stack">
            <Row label={t("settings_version")} value={health.version} />
            <Row label={t("settings_datasets_registered")} value={String(health.datasets_registered)} />
            <Row label={t("settings_computations_logged")} value={String(health.computations_logged)} />
          </div>
        ) : (
          <div className="faint">{t("common_loading")}</div>
        )}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="row" style={{ justifyContent: "space-between" }}>
      <span className="muted">{label}</span>
      <span className="mono">{value}</span>
    </div>
  );
}

function ModelsPanel({ t }: { t: (k: DictKey) => string }) {
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showOverride, setShowOverride] = useState(false);
  const [faustusUrl, setFaustusUrl] = useState("");
  const [faustusToken, setFaustusToken] = useState("");
  const [capUrl, setCapUrl] = useState("");
  const [capModel, setCapModel] = useState("");
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = useRef(new Set<string>());
  const values = useRef({ faustusUrl, faustusToken, capUrl, capModel });
  values.current = { faustusUrl, faustusToken, capUrl, capModel };

  const load = () =>
    api.backend().then((s) => {
      setStatus(s);
      setLoadError(null);
      // the form shows what is saved, so an override can be seen and cleared
      if (!dirty.current.has("faustusUrl")) setFaustusUrl(s.config.saved.faustus_url ?? "");
      if (!dirty.current.has("capUrl")) setCapUrl(s.config.saved.capabilities.llm?.url ?? "");
      if (!dirty.current.has("capModel")) setCapModel(s.config.saved.capabilities.llm?.model ?? "");
      return s;
    }).catch((e) => setLoadError(e instanceof ApiError ? e.message : String(e)));
  useCopilotRefresh(() => load());

  useEffect(() => {
    load();
  }, []);

  async function recheck() {
    setBusy(true);
    try {
      await api.backendRecheck();
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function save(clearToken = false) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      // "" clears a saved override; an empty token box leaves the stored token alone
      const sent = { ...values.current };
      const body: BackendConfigUpdate = {};
      const fields: (keyof typeof sent)[] = [];
      if (clearToken) {
        body.faustus_token = "";
      } else {
        if (dirty.current.has("faustusUrl")) { body.faustus_url = sent.faustusUrl.trim(); fields.push("faustusUrl"); }
        if (dirty.current.has("faustusToken") && sent.faustusToken) { body.faustus_token = sent.faustusToken; fields.push("faustusToken"); }
        const capability: { url?: string; model?: string } = {};
        if (dirty.current.has("capUrl")) { capability.url = sent.capUrl.trim(); fields.push("capUrl"); }
        if (dirty.current.has("capModel")) { capability.model = sent.capModel.trim(); fields.push("capModel"); }
        if (Object.keys(capability).length) body.capabilities = { llm: capability };
      }
      if (Object.keys(body).length) await api.backendConfig(body);
      for (const field of fields) {
        if (values.current[field] === sent[field]) dirty.current.delete(field);
      }
      if (body.faustus_token !== undefined && values.current.faustusToken === sent.faustusToken) setFaustusToken("");
      setSaved(true);
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="card-title">{t("settings_models")}</h3>
        <button className="icon-btn" onClick={recheck} disabled={busy} title={t("settings_models_recheck")}>
          <RefreshCw size={15} />
        </button>
      </div>
      {!status ? (
        <div className="faint">{loadError ?? t("common_loading")}</div>
      ) : (
        <div className="stack" style={{ gap: 10 }}>
          {status.config.error && (
            <div className="faint" style={{ color: "var(--danger)", fontSize: 12 }}>
              {t("settings_models_config_error")} {status.config.error}
            </div>
          )}
          {Object.entries(status.capabilities).map(([cap, res]) => (
            <div key={cap} className="stack" style={{ gap: 2 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="row" style={{ gap: 6 }}>
                  <MessageSquareText size={14} className="muted" />
                  <span className="mono">{cap}</span>
                  <span className="faint" style={{ fontSize: 12 }}>{t("settings_models_llm_use")}</span>
                </span>
                <span className={`badge ${res.state === "resolved" ? "badge-ok" : "badge-muted"}`}>
                  {res.state === "resolved" ? t("settings_models_state_resolved") : t("settings_models_state_unavailable")}
                </span>
              </div>
              {res.state === "resolved" && (
                <div className="faint mono" style={{ fontSize: 12 }}>{res.provider} · {res.model ?? "?"}</div>
              )}
              <div className="faint" style={{ fontSize: 12 }} title={res.reason}>{res.reason}</div>
            </div>
          ))}

          <button className="btn-ghost" style={{ alignSelf: "flex-start" }} onClick={() => setShowOverride((s) => !s)}>
            {t("settings_models_override")}
          </button>
          {showOverride && (
            <div className="stack" style={{ gap: 8 }}>
              <div className="faint" style={{ fontSize: 12 }}>{t("settings_models_override_hint")}</div>
              <div className="col">
                <label className="field-label">{t("settings_models_faustus_url")}</label>
                <input name="faustus_url" type="text" className="mono" value={faustusUrl}
                  onChange={(e) => { dirty.current.add("faustusUrl"); setFaustusUrl(e.target.value); }} placeholder="http://127.0.0.1:7000" />
              </div>
              <div className="col">
                <label className="field-label">
                  {t("settings_models_faustus_token")}
                  {status.config.token_set && <span className="faint"> ({t("settings_models_token_set")})</span>}
                </label>
                <div className="row" style={{ gap: 8 }}>
                  <input type="password" className="mono grow" value={faustusToken}
                    onChange={(e) => { dirty.current.add("faustusToken"); setFaustusToken(e.target.value); }} placeholder="ody_..." />
                  {status.config.token_set && (
                    <button className="btn-ghost" disabled={busy} onClick={() => save(true)}>{t("settings_models_forget_token")}</button>
                  )}
                </div>
              </div>
              <div className="row">
                <div className="col grow">
                  <label className="field-label">llm {t("settings_models_cap_url")}</label>
                  <input name="llm_url" type="text" className="mono" value={capUrl}
                    onChange={(e) => { dirty.current.add("capUrl"); setCapUrl(e.target.value); }} placeholder="http://127.0.0.1:8081" style={{ width: "100%" }} />
                </div>
                <div className="col grow">
                  <label className="field-label">llm {t("settings_models_cap_model")}</label>
                  <input name="llm_model" type="text" value={capModel}
                    onChange={(e) => { dirty.current.add("capModel"); setCapModel(e.target.value); }} style={{ width: "100%" }} />
                </div>
              </div>
              {error && <div className="faint" style={{ color: "var(--danger)" }}>{error}</div>}
              <button className="btn" disabled={busy} onClick={() => save()}>
                {saved ? t("settings_models_saved") : t("settings_models_save")}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
