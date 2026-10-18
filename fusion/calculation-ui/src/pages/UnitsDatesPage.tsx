import { useState, type ReactNode } from "react";
import { api, ApiError, type DateResult, type UnitsResult } from "../api";
import type { DictKey } from "../i18n";
import { CiteBadge, ErrorBlock, Headline, KeyValues, copyCite, fmtNum } from "../components/ResultView";

type T = (k: DictKey) => string;

const DATE_OPS = ["diff", "business_days", "add", "weekday", "iso_week", "age", "convert_tz", "parse"] as const;
type DateOp = (typeof DATE_OPS)[number];

const UNIT_EXAMPLES: [string, string][] = [
  ["3.5 km/h", "m/s"],
  ["100 degF", "degC"],
  ["5 ft 11 in", "cm"],
  ["12 lb", "kg"],
  ["2.5 kWh", "MJ"],
  ["30 psi", "bar"],
];

export function UnitsDatesPage({ t }: { t: T }) {
  return (
    <div className="grid-2">
      <UnitsCard t={t} />
      <DatesCard t={t} />
    </div>
  );
}

function UnitsCard({ t }: { t: T }) {
  const [quantity, setQuantity] = useState("100 degF");
  const [to, setTo] = useState("degC");
  const [result, setResult] = useState<UnitsResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(q = quantity, target = to) {
    setBusy(true);
    setError(null);
    try {
      setResult(await api.unitsConvert(q, target));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="card-title">{t("units_title")}</h3>
        {result && <CiteBadge id={result.id} onClick={copyCite} title={t("common_copy_cite")} />}
      </div>
      <div className="stack">
        <div className="row" style={{ alignItems: "flex-end" }}>
          <div className="col grow">
            <label className="field-label">{t("units_quantity")}</label>
            <input type="text" className="mono" value={quantity} onChange={(e) => setQuantity(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()} />
          </div>
          <div className="col" style={{ width: 130 }}>
            <label className="field-label">{t("units_to")}</label>
            <input type="text" className="mono" value={to} onChange={(e) => setTo(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()} />
          </div>
          <button className="btn" onClick={() => run()} disabled={busy}>{t("units_convert")}</button>
        </div>
        <div className="chips">
          <span className="faint" style={{ alignSelf: "center" }}>{t("units_examples")}:</span>
          {UNIT_EXAMPLES.map(([q, u]) => (
            <button key={q} className="chip" onClick={() => { setQuantity(q); setTo(u); run(q, u); }}>
              {q} → {u}
            </button>
          ))}
        </div>
        {error && <ErrorBlock message={error} />}
        {result ? (
          <div className="result-block">
            <Headline value={fmtNum(result.to_magnitude, 12)} unit={result.to_unit} label={`${result.input} →`} />
            <div className="faint" style={{ marginTop: 8 }}>{result.precision_note ?? ""}</div>
          </div>
        ) : !error && <div className="result-empty">{t("result_empty")}</div>}
      </div>
    </div>
  );
}

function DatesCard({ t }: { t: T }) {
  const [op, setOp] = useState<DateOp>("business_days");
  const [start, setStart] = useState("2026-04-27");
  const [end, setEnd] = useState("2026-05-08");
  const [value, setValue] = useState("3 de abril de 2026");
  const [birthDate, setBirthDate] = useState("1990-05-15");
  const [on, setOn] = useState("");
  const [days, setDays] = useState(30);
  const [months, setMonths] = useState(0);
  const [country, setCountry] = useState("ES");
  const [subdivision, setSubdivision] = useState("");
  const [includeEnd, setIncludeEnd] = useState(true);
  const [fromTz, setFromTz] = useState("Europe/Madrid");
  const [toTz, setToTz] = useState("America/New_York");
  const [result, setResult] = useState<DateResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = { operation: op };
      if (op === "diff") { payload.start = start; payload.end = end; }
      if (op === "business_days") {
        Object.assign(payload, { start, end, country, include_end: includeEnd });
        if (subdivision.trim()) payload.subdivision = subdivision.trim();
      }
      if (op === "add") { payload.start = start; payload.days = days; payload.months = months; }
      if (op === "weekday" || op === "iso_week") payload.value = value;
      if (op === "parse") payload.text = value;
      if (op === "age") { payload.birth_date = birthDate; if (on.trim()) payload.on = on.trim(); }
      if (op === "convert_tz") { payload.value = value; payload.from_tz = fromTz; payload.to_tz = toTz; }
      setResult(await api.dateCalc(payload));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  const text = (label: string, v: string, set: (s: string) => void, placeholder?: string) => (
    <div className="col grow">
      <label className="field-label">{label}</label>
      <input type="text" className="mono" value={v} placeholder={placeholder} onChange={(e) => set(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && run()} />
    </div>
  );

  return (
    <div className="card">
      <div className="card-head">
        <h3 className="card-title">{t("dates_title")}</h3>
        {result && <CiteBadge id={result.id} onClick={copyCite} title={t("common_copy_cite")} />}
      </div>
      <div className="stack">
        <div className="col">
          <label className="field-label">{t("dates_operation")}</label>
          <select value={op} onChange={(e) => { setOp(e.target.value as DateOp); setResult(null); setError(null); }}>
            {DATE_OPS.map((o) => <option key={o} value={o}>{t(`op_${o}` as DictKey)}</option>)}
          </select>
        </div>
        {(op === "diff" || op === "business_days") && (
          <div className="row">{text(t("dates_start"), start, setStart)}{text(t("dates_end"), end, setEnd)}</div>
        )}
        {op === "business_days" && (
          <>
            <div className="row">
              {text(t("dates_country"), country, setCountry)}
              {text(t("dates_subdivision"), subdivision, setSubdivision, country.toUpperCase() === "ES" ? "MD (Madrid)" : "")}
            </div>
            <label className="row faint" style={{ gap: 6 }}>
              <input type="checkbox" checked={includeEnd} onChange={(e) => setIncludeEnd(e.target.checked)} /> {t("dates_include_end")}
            </label>
          </>
        )}
        {op === "add" && (
          <div className="row">
            {text(t("dates_start"), start, setStart)}
            <div className="col" style={{ width: 100 }}>
              <label className="field-label">{t("dates_days")}</label>
              <input type="number" value={days} onChange={(e) => setDays(Number(e.target.value))} />
            </div>
            <div className="col" style={{ width: 100 }}>
              <label className="field-label">{t("dates_months")}</label>
              <input type="number" value={months} onChange={(e) => setMonths(Number(e.target.value))} />
            </div>
          </div>
        )}
        {(op === "weekday" || op === "iso_week" || op === "parse") &&
          <div className="row">{text(op === "parse" ? t("dates_text") : t("dates_value"), value, setValue)}</div>}
        {op === "age" && <div className="row">{text(t("dates_birth"), birthDate, setBirthDate)}{text(t("dates_on"), on, setOn)}</div>}
        {op === "convert_tz" && (
          <>
            <div className="row">{text(t("dates_datetime"), value, setValue, "2026-09-22 14:00")}</div>
            <div className="row">{text(t("dates_from_tz"), fromTz, setFromTz)}{text(t("dates_to_tz"), toTz, setToTz)}</div>
          </>
        )}
        <div className="faint">{t("dates_hint")}</div>
        <button className="btn" onClick={run} disabled={busy}>{t("dates_run")}</button>
        {error && <ErrorBlock message={error} />}
        {result ? (
          <div className="result-block">
            {dateHeadline(op, result, t)}
            <KeyValues data={result} skip={dateSkip(op)} />
            {op === "business_days" && Array.isArray(result.holidays_excluded) && result.holidays_excluded.length > 0 && (
              <div style={{ marginTop: 10 }}>
                <div className="field-label" style={{ marginBottom: 4 }}>{t("hl_holidays")}</div>
                <div className="chips">
                  {(result.holidays_excluded as { date: string; name: string }[]).map((h) => (
                    <span key={h.date} className="chip"><strong>{h.date}</strong> {h.name}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : !error && <div className="result-empty">{t("result_empty")}</div>}
      </div>
    </div>
  );
}

function dateSkip(op: DateOp): string[] {
  const base = ["input"];
  if (op === "business_days") return [...base, "business_days", "holidays_excluded"];
  if (op === "diff") return [...base, "result", "calendar"];
  if (op === "add") return [...base, "result"];
  return base;
}

function dateHeadline(op: DateOp, r: Record<string, unknown>, t: T): ReactNode {
  switch (op) {
    case "business_days":
      return <Headline value={String(r.business_days)} unit={t("hl_business_days")} label={`${r.start} → ${r.end}`} />;
    case "diff": {
      const cal = r.calendar as { years: number; months: number; days: number };
      return (
        <Headline
          value={String(r.days)}
          unit={t("hl_days")}
          label={`${cal.years} ${t("hl_years")} · ${cal.months} ${t("hl_months")} · ${cal.days} ${t("hl_days")}`}
        />
      );
    }
    case "add":
      return <Headline value={String(r.result)} unit={String(r.weekday)} />;
    case "weekday":
      return <Headline value={String(r.weekday)} unit={String(r.date)} />;
    case "iso_week":
      return <Headline value={`${r.iso_year}-W${String(r.iso_week).padStart(2, "0")}`} unit={t("hl_week")} />;
    case "age":
      return <Headline value={String(r.years)} unit={t("hl_years")} />;
    case "convert_tz":
      return <Headline value={String(r.result).replace("T", " ")} unit={String(r.to_tz)} />;
    case "parse":
      return <Headline value={String(r.date)} unit={String(r.weekday ?? "")} />;
  }
}
