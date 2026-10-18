import { useEffect, useRef } from "react";
import { api } from "./api";
import type { Lang } from "./i18n";
import type { Theme } from "./hooks";

type Reference = { kind: string; object_id: string };
type ContextHook = { context: () => Promise<unknown>; onTurnEnd: () => void };
declare global {
  interface Window { hoardCopilot?: ContextHook }
}

export function useCopilotRefresh(refresh: () => unknown) {
  const current = useRef(refresh);
  current.current = refresh;
  useEffect(() => {
    const handler = () => { current.current(); };
    window.addEventListener("laplace:copilot-refresh", handler);
    return () => window.removeEventListener("laplace:copilot-refresh", handler);
  }, []);
}

export function useCopilotContext(section: string, lang: Lang, theme: Theme) {
  const current = useRef({ section, lang });
  const selected = useRef<Reference | null>(null);
  const editor = useRef<HTMLElement | null>(null);
  current.current = { section, lang };
  useEffect(() => { selected.current = null; editor.current = null; }, [section]);
  useEffect(() => {
    document.documentElement.lang = lang;
    const apply = () => {
      const panel = document.querySelector("faustus-chat") as (HTMLElement & { locale: string; theme: string }) | null;
      const channels = getComputedStyle(document.body).backgroundColor.match(/[\d.]+/g)?.slice(0, 3).map(Number) || [];
      const resolvedTheme = theme === "system" ? (channels.reduce((a, b) => a + b, 0) > 384 ? "light" : "dark") : theme;
      if (panel) {
        if (panel.locale !== lang) panel.locale = lang;
        if (panel.theme !== resolvedTheme) panel.theme = resolvedTheme;
      }
    };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [lang, theme]);
  useEffect(() => {
    function remember(event: Event) {
      const target = event.target;
      if (!(target instanceof HTMLElement) || !target.closest(".content")) return;
      const object = target.closest<HTMLElement>("[data-copilot-kind][data-copilot-id]");
      if (object) {
        selected.current = { kind: object.dataset.copilotKind!, object_id: object.dataset.copilotId! };
        editor.current = object;
      }
      const group = target.closest<HTMLElement>("[data-copilot-editor], .card, .cell");
      if (group?.querySelector("input, textarea, select")) editor.current = group;
    }
    document.addEventListener("focusin", remember);
    document.addEventListener("pointerdown", remember);
    const hook: ContextHook = {
      context: async () => {
        const view = { ...current.current };
        const root = document.querySelector<HTMLElement>(".content");
        const primary = root?.querySelector<HTMLElement>("[data-copilot-primary]");
        const group = editor.current?.isConnected ? editor.current
          : root?.querySelector<HTMLElement>("[data-copilot-editor], .card");
        const reference = group?.hasAttribute("data-copilot-new") ? null : selected.current || (primary?.dataset.copilotId
          ? { kind: primary.dataset.copilotKind!, object_id: primary.dataset.copilotId } : null);
        const fields = [...(group?.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>("input, textarea, select") || [])]
          .filter(field => field.type !== "password" && field.type !== "hidden" && field.getClientRects().length)
          .map((field, index) => ({
            name: field.name || "field_" + index,
            label: field.labels?.[0]?.textContent?.trim() || field.closest(".col")?.querySelector("label")?.textContent?.trim()
              || field.getAttribute("aria-label") || field.getAttribute("placeholder") || field.name || "",
            value: field instanceof HTMLInputElement && ["checkbox", "radio"].includes(field.type) ? field.checked : field.value,
          }));
        const text = String(window.getSelection() || "").trim();
        const engine = group?.dataset.copilotEngine;
        // Resolve the saved object against the same form snapshot captured above.
        // Navigation or typing during this request belongs to the next request.
        const resolved = reference ? await api.appContext(reference.kind, reference.object_id)
          : { project: null, active_objects: [] };
        return { ...resolved,
          view: { section: view.section, locale: view.lang,
            editor: { kind: view.section, object_id: resolved.active_objects[0]?.object_id || null,
              engine, fields } },
          selection: text ? { kind: "text", text } : null };
      },
      onTurnEnd: () => window.dispatchEvent(new CustomEvent("laplace:copilot-refresh")),
    };
    window.hoardCopilot = hook;
    return () => {
      document.removeEventListener("focusin", remember);
      document.removeEventListener("pointerdown", remember);
      if (window.hoardCopilot === hook) delete window.hoardCopilot;
    };
  }, []);
}
