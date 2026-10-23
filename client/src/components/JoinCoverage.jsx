import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

export default function JoinCoverage({ dataset, otherDataset, keys, datasets, lang, t }) {
  const leftVersion = datasets.find((item) => item.name === dataset)?.current_version;
  const rightVersion = datasets.find((item) => item.name === otherDataset)?.current_version;
  const signature = JSON.stringify([dataset, leftVersion, otherDataset, rightVersion, keys]);
  const [loaded, setLoaded] = useState(null), [error, setError] = useState(null), [busy, setBusy] = useState(false);
  const generation = useRef(0);
  useEffect(() => { generation.current++; setLoaded(null); setError(null); setBusy(false); return () => { generation.current++; }; }, [signature]);
  const result = loaded?.signature === signature ? loaded.result : null;
  async function check() {
    const current = ++generation.current;
    setLoaded(null); setError(null); setBusy(true);
    try {
      const on = JSON.parse(keys || "[]");
      const next = await api.joinPreview(dataset, otherDataset, on);
      if (current === generation.current) setLoaded({ signature, result: next });
    } catch (failure) { if (current === generation.current) setError({ signature, message: failure.message }); }
    finally { if (current === generation.current) setBusy(false); }
  }
  return <section className="min-w-0 border-t pt-3" style={{ borderColor: "var(--line)" }} aria-label={t("join_coverage")}>
    <div className="flex flex-wrap items-center gap-3">
      <h3 className="text-sm font-semibold">{t("join_coverage")}</h3>
      <button type="button" className="btn btn-sm" onClick={check} disabled={busy}>{busy ? t("join_checking") : t("join_check")}</button>
    </div>
    <p className="help mt-2">{t("join_coverage_help")}</p>
    {error?.signature === signature && <p role="alert" className="help mt-2" style={{ color: "var(--danger-ink)" }}>{error.message}</p>}
    {result && <div className="mt-3" aria-live="polite">
      <p className="help break-all">{dataset} → {otherDataset}</p>
      <dl className="mt-2 grid min-w-0 grid-cols-2 gap-3 sm:grid-cols-4">
        {[["left_rows", result.left_rows], ["matched", result.matched], ["unmatched", result.unmatched], ["rate", new Intl.NumberFormat(lang === "es" ? "es-ES" : "en-US", { style: "percent", maximumFractionDigits: 2 }).format(result.match_rate)]].map(([name, value]) => <div key={name} className="min-w-0">
          <dt className="help">{t(`join_${name}`)}</dt><dd className="num break-all font-semibold">{value}</dd>
        </div>)}
      </dl>
      <p className="help mt-2">{t("join_coverage_rows")}</p>
    </div>}
  </section>;
}
