import React from "react";
import { formatNumber } from "../../format.js";

/** A half-circle SVG gauge for the 0-100 quality score. Plain inline SVG, not
 * a second charting library — vega-lite doesn't do gauges cleanly and this is
 * a handful of arithmetic lines, same spirit as the app's own hist-bars CSS
 * widget. Color bands mirror the app's ok/warn/danger chip tones. */
export function QualityGauge({ score }) {
  const s = Math.max(0, Math.min(100, score ?? 0));
  const angle = (s / 100) * Math.PI; // 0..PI, left to right
  const r = 80;
  const cx = 100;
  const cy = 95;
  const x = cx - r * Math.cos(angle);
  const y = cy - r * Math.sin(angle);
  const large = angle > Math.PI / 2 ? 1 : 0;
  const color = s >= 85 ? "var(--ok-ink)" : s >= 60 ? "var(--warn-ink)" : "var(--danger-ink)";
  const track = s >= 85 ? "var(--ok-bg)" : s >= 60 ? "var(--warn-bg)" : "var(--danger-bg)";
  return (
    <svg width="200" height="115" viewBox="0 0 200 115" role="img" aria-label={`Quality score ${s}`}>
      <path d={`M ${cx - r} ${cy} A ${r} ${r} 0 1 1 ${cx + r} ${cy}`} fill="none" stroke={track} strokeWidth="16" strokeLinecap="round" />
      <path d={`M ${cx - r} ${cy} A ${r} ${r} 0 ${large} 1 ${x} ${y}`} fill="none" stroke={color} strokeWidth="16" strokeLinecap="round" />
      <text x={cx} y={cy - 6} textAnchor="middle" fontSize="30" fontWeight="700" fill="var(--ink)" className="num">{Math.round(s)}</text>
      <text x={cx} y={cy + 16} textAnchor="middle" fontSize="11" fill="var(--supporting-ink)">/ 100</text>
    </svg>
  );
}

/** Horizontal bar rows for a 0..1 breakdown (quality components, feature
 * importance) — matches the bar look already used in ModelsPage. */
export function BarRows({ rows, lang, max = 1 }) {
  return (
    <div className="flex flex-col gap-1.5">
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-2 text-[12px]">
          <span className="w-36 shrink-0 truncate" title={r.label}>{r.label}</span>
          <div className="h-2 flex-1 rounded" style={{ background: "var(--bar-bg)" }}>
            <div className="h-2 rounded" style={{ width: `${Math.max(0, Math.min(100, (r.value / max) * 100))}%`, background: r.color || "var(--accent)" }} />
          </div>
          <span className="num w-14 shrink-0 text-right">{formatNumber(r.value, lang, { maximumFractionDigits: 3 })}</span>
        </div>
      ))}
    </div>
  );
}

export function SectionCard({ title, action, children, testId }) {
  return (
    <div className="panel-white flex flex-col gap-3" data-testid={testId}>
      {(title || action) && (
        <div className="flex items-center justify-between gap-2">
          {title && <h3 className="text-[13px] font-semibold">{title}</h3>}
          {action}
        </div>
      )}
      {children}
    </div>
  );
}

export function DatasetSelect({ datasets, value, onChange, testId }) {
  return (
    <select className="field" style={{ width: "auto" }} value={value || ""} onChange={(e) => onChange(e.target.value)} data-testid={testId}>
      {datasets.map((d) => (
        <option key={d.name} value={d.name}>{d.name}</option>
      ))}
    </select>
  );
}

export function ColumnSelect({ columns, value, onChange, allowEmpty = true, testId }) {
  return (
    <select className="field" value={value ?? ""} onChange={(e) => onChange(e.target.value)} data-testid={testId}>
      {allowEmpty && <option value="">—</option>}
      {columns.map((c) => (
        <option key={c} value={c}>{c}</option>
      ))}
    </select>
  );
}

export function SimpleTable({ columns, rows, keyField, renderCell, onRowClick, selected }) {
  return (
    <div className="overflow-auto">
      <table className="w-full border-collapse text-[12.5px]">
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className="border-b p-1.5 text-left" style={{ borderColor: "var(--line)" }}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row[keyField]}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={onRowClick ? "cursor-pointer hover:opacity-80" : undefined}
              style={selected && selected(row) ? { background: "var(--nav-active)" } : undefined}
            >
              {columns.map((c) => (
                <td key={c.key} className="border-b p-1.5" style={{ borderColor: "var(--line)" }}>
                  {renderCell ? renderCell(row, c.key) : String(row[c.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StatTile({ label, value }) {
  return (
    <div className="stat-tile">
      <div className="help">{label}</div>
      <div className="stat-number num">{value === null || value === undefined ? "—" : value}</div>
    </div>
  );
}

export function InstallHint({ note }) {
  if (!note) return null;
  return <span className="help">({note})</span>;
}
