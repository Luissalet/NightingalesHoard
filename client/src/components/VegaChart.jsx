import React, { useEffect, useRef } from "react";
import { useApp } from "../App.jsx";
import { ES_NUMBER_LOCALE } from "../format.js";

/** Renders a Vega-Lite spec with vega-embed (loaded lazily so the initial
 * bundle stays small). Falls back to a small notice if rendering fails. */
export default function VegaChart({ spec, height = 260 }) {
  const { lang } = useApp();
  const ref = useRef(null);
  const viewRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    if (!spec || !ref.current) return undefined;
    import("vega-embed").then((mod) => {
      if (cancelled || !ref.current) return;
      const embed = mod.default;
      // formatLocale gives every number field's d3-format token (set server
      // side in charts.py) the right thousands/decimal marks for the UI's
      // current language, so an axis, a tooltip, and a KPI tile all agree.
      embed(ref.current, spec, {
        actions: false,
        renderer: "svg",
        formatLocale: lang === "es" ? ES_NUMBER_LOCALE : undefined,
      })
        .then((result) => {
          viewRef.current = result.view;
        })
        .catch(() => {
          if (ref.current) ref.current.textContent = "Could not render chart.";
        });
    });
    return () => {
      cancelled = true;
      if (viewRef.current) {
        viewRef.current.finalize();
        viewRef.current = null;
      }
    };
  }, [spec, lang]);

  // vega-embed's own injected class defaults the container to
  // `display: inline-block`, which — combined with the spec's
  // "width": "container" — sizes to content with no content yet, landing on
  // a 0px chart. Force block + 100% width so it measures the real container.
  return <div ref={ref} style={{ minHeight: height, display: "block", width: "100%" }} data-testid="vega-chart" />;
}
