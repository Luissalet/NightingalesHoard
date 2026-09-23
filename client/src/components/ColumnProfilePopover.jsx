import React from "react";

export default function ColumnProfilePopover({ column, entry, onClose }) {
  if (!entry) return null;
  return (
    <div className="popover" data-testid="column-profile-popover" style={{ top: 40, left: 8 }}>
      <div className="mb-2 flex items-center justify-between">
        <div className="font-semibold">{column}</div>
        <button type="button" className="btn-link text-xs" onClick={onClose}>Close</button>
      </div>
      <div className="help mb-2">{entry.type}</div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[12px]">
        <div>Nulls</div><div className="num text-right">{entry.nulls_pct}%</div>
        <div>Distinct (approx.)</div><div className="num text-right">{entry.distinct_approx}</div>
        {entry.min !== undefined && (<><div>Min</div><div className="num text-right">{String(entry.min)}</div></>)}
        {entry.max !== undefined && (<><div>Max</div><div className="num text-right">{String(entry.max)}</div></>)}
        {entry.mean !== undefined && (<><div>Mean</div><div className="num text-right">{fmt(entry.mean)}</div></>)}
        {entry.median !== undefined && (<><div>Median</div><div className="num text-right">{fmt(entry.median)}</div></>)}
        {entry.sd !== undefined && (<><div>Std dev</div><div className="num text-right">{fmt(entry.sd)}</div></>)}
        {entry.outliers_iqr !== undefined && (<><div>Outliers (IQR)</div><div className="num text-right">{entry.outliers_iqr}</div></>)}
      </div>
      {entry.histogram && (
        <div className="hist-bars mt-3" aria-hidden="true">
          {entry.histogram.map((v, i) => (
            <div key={i} style={{ height: `${Math.max(2, (v / Math.max(...entry.histogram, 1)) * 44)}px` }} />
          ))}
        </div>
      )}
      {entry.top_values && entry.top_values.length > 0 && (
        <div className="mt-3">
          <div className="help mb-1">Top values</div>
          {entry.top_values.map((tv, i) => (
            <div key={i} className="flex justify-between text-[12px]">
              <span className="truncate">{String(tv.value)}</span>
              <span className="num shrink-0">{tv.count}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function fmt(v) {
  return typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 3 }) : String(v);
}
