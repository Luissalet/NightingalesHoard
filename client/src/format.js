/** Shared locale-aware formatting: numbers follow the UI's own language
 * setting (es-ES when Spanish is selected, en-US otherwise) rather than the
 * browser's default locale, so a KPI tile or a chart tooltip always matches
 * whatever the person picked in Settings. */
export function formatNumber(value, lang, opts = {}) {
  if (typeof value !== "number" || Number.isNaN(value)) return value === null || value === undefined ? "—" : String(value);
  const locale = lang === "es" ? "es-ES" : "en-US";
  return new Intl.NumberFormat(locale, { maximumFractionDigits: 2, ...opts }).format(value);
}

/** A d3-format locale definition for vega-embed's `formatLocale` option,
 * matching the same es-ES grouping/decimal marks used above (3.374.888,14).
 * Passed only when the UI language is Spanish; vega-embed's own default
 * (en-US style) is used otherwise. */
export const ES_NUMBER_LOCALE = {
  decimal: ",",
  thousands: ".",
  grouping: [3],
  currency: ["", " €"],
};

/** Never show a raw filesystem path or a bare URL in the UI: a local path
 * shows just its file name (the full path stays available as a tooltip via
 * `title`), and a URL shows host+path only (no scheme, no query string that
 * might carry a token). Used anywhere a dataset's original source is
 * displayed (Sources list, the dataset recipe panel's lineage line). */
export function displaySource(path) {
  if (!path) return "";
  const str = String(path);
  try {
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(str)) {
      const u = new URL(str);
      return u.host + u.pathname;
    }
  } catch {
    /* not a parseable URL — fall through to the filename case */
  }
  const parts = str.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || str;
}
