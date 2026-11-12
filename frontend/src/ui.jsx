import React, { useEffect } from "react";

export const SEV_COLOR = { CRITICAL: "#D62839", HIGH: "#F08A00", MEDIUM: "#2F6B8F", LOW: "#6B7C85" };
export const STATUS_LABEL = { OPEN: "Open", ASSIGNED: "Assigned", RESOLVED: "Resolved", FALSE_POSITIVE: "False alarm" };
export const pretty = (v) => String(v || "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export function Sev({ value }) {
  const v = value || "MEDIUM";
  return <span className="tag" style={{ "--c": SEV_COLOR[v] }}>{pretty(v.toLowerCase())}</span>;
}

export function Status({ value }) {
  const v = value || "OPEN";
  return <span className={`status status-${v.toLowerCase()}`}>{STATUS_LABEL[v] || v}</span>;
}

export function Modal({ title, onClose, children, wide }) {
  useEffect(() => {
    const h = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header>
          <h3>{title}</h3>
          <button className="ghost" onClick={onClose} aria-label="Close">Close</button>
        </header>
        {children}
      </div>
    </div>
  );
}

export function Empty({ children }) {
  return <div className="empty">{children}</div>;
}

export const fmtTime = (t) => (t ? String(t).replace("T", " ").slice(0, 16) : "-");
