import { useCallback, useEffect, useState } from "react";
import { detectLang, dict, type DictKey, type Lang } from "./i18n";

export type Theme = "system" | "light" | "dark";

function safeGet(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function safeSet(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* ignore: private mode / blocked storage */
  }
}

export function useLang(): [Lang, (l: Lang) => void, (k: DictKey) => string] {
  const [lang, setLangState] = useState<Lang>(() => detectLang());

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    safeSet("lh_lang", l);
  }, []);

  const t = useCallback((k: DictKey) => dict[lang][k] ?? String(k), [lang]);

  return [lang, setLang, t];
}

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(() => (safeGet("lh_theme") as Theme) || "system");

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    safeSet("lh_theme", t);
  }, []);

  return [theme, setTheme];
}
