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

const INTEGER_TYPES = new Set([
  "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
  "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT",
]);
const FLOAT_TYPES = new Set(["FLOAT", "DOUBLE", "REAL"]);

function baseType(type) {
  return String(type || "").toUpperCase().split("(")[0].trim();
}

/** Parse a DuckDB DATE/TIMESTAMP value as it comes over JSON (an ISO string
 * from Python's `.isoformat()`: "2025-08-15" for a date, "2025-08-15T00:00:00"
 * — or with a real time — for a timestamp) into a native Date, without
 * shifting it by the viewer's timezone. */
function parseTemporal(value) {
  const s = String(value);
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?)?/.exec(s);
  if (!m) return null;
  const [, y, mo, d, h, mi, se] = m;
  return {
    date: new Date(Number(y), Number(mo) - 1, Number(d), Number(h || 0), Number(mi || 0), Number(se || 0)),
    hasTime: h !== undefined,
    isMidnight: h === undefined || (h === "00" && mi === "00" && se === "00"),
  };
}

/** Format one grid cell by the dataset's own DuckDB column type and the UI's
 * language, so a DATE/TIMESTAMP reads as a date (not `2025-08-15T00:00:00`)
 * and a DOUBLE/DECIMAL reads with thousands separators and a sensible number
 * of digits (not twelve decimals) — matching the person's locale either way.
 * Returns `{ text, raw }`: `text` is what's shown, `raw` is the exact
 * original value for a tooltip/copy so nothing is ever hidden, only tidied. */
export function formatCellValue(value, columnType, lang) {
  const raw = value === null || value === undefined ? "" : String(value);
  if (value === null || value === undefined) return { text: null, raw };
  if (typeof value === "boolean") return { text: value ? "true" : "false", raw };

  const locale = lang === "es" ? "es-ES" : "en-US";
  const type = baseType(columnType);

  if (type === "DATE" || type === "TIMESTAMP" || type === "TIMESTAMP_TZ" || type === "DATETIME") {
    const parsed = parseTemporal(value);
    if (parsed) {
      const showTime = type !== "DATE" && !parsed.isMidnight;
      const text = new Intl.DateTimeFormat(locale, showTime
        ? { dateStyle: "medium", timeStyle: "short" }
        : { dateStyle: "medium" }).format(parsed.date);
      return { text, raw };
    }
  }

  if (typeof value === "number" && !Number.isNaN(value)) {
    if (type.startsWith("DECIMAL") || type.startsWith("NUMERIC")) {
      return { text: new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value), raw };
    }
    if (INTEGER_TYPES.has(type)) {
      return { text: new Intl.NumberFormat(locale, { maximumFractionDigits: 0 }).format(value), raw };
    }
    if (FLOAT_TYPES.has(type) || type === "") {
      return { text: new Intl.NumberFormat(locale, { maximumSignificantDigits: 4 }).format(value), raw };
    }
    return { text: new Intl.NumberFormat(locale, { maximumFractionDigits: 4 }).format(value), raw };
  }

  return { text: raw, raw };
}

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
