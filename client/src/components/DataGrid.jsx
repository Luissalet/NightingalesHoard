import React, { useRef, useState } from "react";

import { formatCellValue } from "../format.js";

const ROW_HEIGHT = 30;
const COL_WIDTH = 160;
const OVERSCAN = 6;

/** A virtualized table: only rows in (or near) the viewport are rendered, so a
 * dataset with tens of thousands of preview rows still scrolls smoothly.
 * Column headers are clickable (column profile popovers are wired by the
 * caller through `onHeaderClick`). Cells are formatted by column type and
 * `lang` (dates as dates, numbers with thousands separators); the exact raw
 * value stays a hover away (title tooltip) and a double-click copies it. */
export default function DataGrid({ columns, rows, onHeaderClick, activeColumn, height = 420, lang = "en" }) {
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportH, setViewportH] = useState(height);
  const wrapRef = useRef(null);

  const total = rows.length;
  const firstVisible = Math.max(0, Math.floor(scrollTop / ROW_HEIGHT) - OVERSCAN);
  const visibleCount = Math.ceil(viewportH / ROW_HEIGHT) + OVERSCAN * 2;
  const lastVisible = Math.min(total, firstVisible + visibleCount);
  const visibleRows = rows.slice(firstVisible, lastVisible);

  return (
    <div
      className="grid-wrap"
      style={{ height, position: "relative" }}
      ref={wrapRef}
      onScroll={(e) => {
        setScrollTop(e.currentTarget.scrollTop);
        setViewportH(e.currentTarget.clientHeight);
      }}
      data-testid="data-grid"
    >
      <div className="grid-head">
        {columns.map((c) => (
          <div
            key={c.name}
            className="grid-cell"
            style={{ width: COL_WIDTH, background: activeColumn === c.name ? "var(--nav-active)" : undefined }}
            onClick={() => onHeaderClick && onHeaderClick(c.name)}
            role="button"
            tabIndex={0}
            title={c.type}
          >
            {c.name}
            <span className="help ml-1">{c.type}</span>
          </div>
        ))}
      </div>
      <div style={{ height: total * ROW_HEIGHT, position: "relative" }}>
        <div style={{ position: "absolute", top: firstVisible * ROW_HEIGHT, left: 0, right: 0 }}>
          {visibleRows.map((row, i) => (
            <div className="grid-row" key={firstVisible + i} style={{ height: ROW_HEIGHT }}>
              {columns.map((c) => {
                const { text, raw } = formatCellValue(row[c.name], c.type, lang);
                return (
                  <div
                    className="grid-cell"
                    key={c.name}
                    style={{ width: COL_WIDTH, lineHeight: `${ROW_HEIGHT - 8}px` }}
                    title={raw || undefined}
                    onDoubleClick={() => {
                      if (raw && navigator.clipboard) navigator.clipboard.writeText(raw).catch(() => {});
                    }}
                  >
                    {text === null ? <span className="help">null</span> : text}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
