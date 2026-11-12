import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { tokenUrl } from "./api.js";

const Ctx = createContext({ tick: 0, notifTick: 0, toasts: [], dismiss() {}, muted: false, setMuted() {} });
export const useLive = () => useContext(Ctx);

function beep(severity) {
  try {
    const ac = new (window.AudioContext || window.webkitAudioContext)();
    const critical = severity === "CRITICAL" || severity === "HIGH";
    [0, 0.22].forEach((delay, i) => {
      const o = ac.createOscillator();
      const g = ac.createGain();
      o.type = "square";
      o.frequency.value = critical ? (i ? 660 : 880) : 520;
      g.gain.value = 0.06;
      o.connect(g);
      g.connect(ac.destination);
      o.start(ac.currentTime + delay);
      o.stop(ac.currentTime + delay + 0.16);
    });
  } catch (e) {
    /* audio blocked until the user interacts with the page */
  }
}

export function LiveProvider({ children }) {
  const [tick, setTick] = useState(0);
  const [notifTick, setNotifTick] = useState(0);
  const [toasts, setToasts] = useState([]);
  const [muted, setMutedState] = useState(localStorage.getItem("sentinel_muted") === "1");
  const mutedRef = useRef(muted);

  const setMuted = (v) => {
    mutedRef.current = v;
    setMutedState(v);
    localStorage.setItem("sentinel_muted", v ? "1" : "0");
  };
  const dismiss = useCallback((id) => setToasts((t) => t.filter((x) => x.id !== id)), []);

  useEffect(() => {
    const es = new EventSource(tokenUrl("/api/events"));
    es.onmessage = (m) => {
      let ev;
      try { ev = JSON.parse(m.data); } catch { return; }
      if (ev.type === "incident") {
        const i = ev.incident;
        const id = i.incident_id + Date.now();
        setToasts((t) => [{ id, severity: i.severity, title: i.violation.replace(/_/g, " "), camera: i.camera_name, code: i.incident_id }, ...t].slice(0, 4));
        setTimeout(() => dismiss(id), 9000);
        if (!mutedRef.current) beep(i.severity);
      }
      if (ev.type === "notification") {
        // the server only sends a personal notification to the person it is meant for
        const id = `n${ev.incident_id}${Date.now()}`;
        setToasts((t) => [{ id, severity: "MEDIUM", title: ev.title, text: ev.message, code: ev.incident_id }, ...t].slice(0, 4));
        setTimeout(() => dismiss(id), 10000);
        if (!mutedRef.current) beep("MEDIUM");
        setNotifTick((n) => n + 1);
      }
      setTick((n) => n + 1);
    };
    return () => es.close();
  }, [dismiss]);

  return <Ctx.Provider value={{ tick, notifTick, toasts, dismiss, muted, setMuted }}>{children}</Ctx.Provider>;
}
