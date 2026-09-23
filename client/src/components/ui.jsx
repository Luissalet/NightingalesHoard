import React, { useEffect } from "react";

export function Icon({ d, size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export function Toast({ message, onClose }) {
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(onClose, 3200);
    return () => clearTimeout(timer);
  }, [message, onClose]);
  if (!message) return null;
  return (
    <div className="toast" role="status" onClick={onClose}>
      {message}
    </div>
  );
}

export function Modal({ title, onClose, children, wide }) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div
        className="panel-white max-h-[85vh] w-full overflow-auto shadow-xl"
        style={{ maxWidth: wide ? 720 : 480 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-[15px] font-semibold">{title}</h2>
          <button type="button" className="btn btn-sm" onClick={onClose} aria-label="Close">
            <Icon d="M6 6l12 12M6 18L18 6" size={14} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Spinner() {
  return <span className="help">…</span>;
}

export function ErrorBanner({ message, onRetry }) {
  if (!message) return null;
  return (
    <div className="mb-4 rounded-md border p-3 text-[13px]" style={{ background: "var(--danger-bg)", color: "var(--danger-ink)", borderColor: "var(--danger-line)" }} role="alert">
      {message} {onRetry && <button type="button" className="btn-link" onClick={onRetry}>Retry</button>}
    </div>
  );
}

export function EmptyState({ children }) {
  return <div className="panel text-center text-[13px]" style={{ color: "var(--supporting-ink)" }}>{children}</div>;
}

export function Chip({ children, tone }) {
  const cls = tone ? `chip chip-${tone}` : "chip";
  return <span className={cls}>{children}</span>;
}
