import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, tokenUrl } from "../api.js";
import { useLive } from "../live.jsx";
import { Sev, fmtTime, pretty } from "../ui.jsx";

function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  return <div className="tv-clock">{now.toLocaleTimeString()}</div>;
}

function columnsFor(n) {
  return n <= 1 ? 1 : n <= 4 ? 2 : n <= 9 ? 3 : 4;
}

// Wall display for a control room: every camera at once, the key numbers, and a loud banner on a new incident.
export default function TV() {
  const { tick } = useLive();
  const [cams, setCams] = useState([]);
  const [stats, setStats] = useState(null);
  const [score, setScore] = useState(null);
  const [recent, setRecent] = useState([]);
  const [flash, setFlash] = useState(null);
  const [full, setFull] = useState(!!document.fullscreenElement);
  const [beat, setBeat] = useState(0);
  const lastTop = useRef(null);
  const flashTimer = useRef(null);

  // refresh on every live event, and every 30 seconds as a safety net
  useEffect(() => {
    const t = setInterval(() => setBeat((b) => b + 1), 30000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    let alive = true;
    Promise.allSettled([
      api("/api/cameras"),
      api("/stats"),
      api("/api/safety-score", { params: { hours: 24 } }),
      api("/api/incidents", { params: { limit: 8 } }),
    ]).then(([c, st, s, r]) => {
      if (!alive) return;
      if (c.status === "fulfilled") setCams(c.value.filter((x) => x.enabled !== 0 && x.enabled !== false));
      if (st.status === "fulfilled") setStats(st.value);
      if (s.status === "fulfilled") setScore(s.value);
      if (r.status === "fulfilled") {
        const items = r.value.items || [];
        setRecent(items);
        const top = items[0] ? items[0].incident_id : null;
        if (lastTop.current && top && top !== lastTop.current) {
          setFlash(items[0]);
          clearTimeout(flashTimer.current);
          flashTimer.current = setTimeout(() => setFlash(null), 10000);
        }
        if (top) lastTop.current = top;
      }
    });
    return () => { alive = false; };
  }, [tick, beat]);

  useEffect(() => () => clearTimeout(flashTimer.current), []);

  // keep the screen awake while this page is open (not every browser supports this)
  useEffect(() => {
    let lock = null;
    const ask = () => {
      if (navigator.wakeLock && document.visibilityState === "visible") {
        navigator.wakeLock.request("screen").then((l) => { lock = l; }).catch(() => {});
      }
    };
    ask();
    document.addEventListener("visibilitychange", ask);
    return () => {
      document.removeEventListener("visibilitychange", ask);
      if (lock) lock.release().catch(() => {});
    };
  }, []);

  useEffect(() => {
    const h = () => setFull(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", h);
    return () => document.removeEventListener("fullscreenchange", h);
  }, []);

  function toggleFull() {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else document.documentElement.requestFullscreen().catch(() => {});
  }

  const active = stats ? stats.open_incidents + stats.assigned_incidents : 0;
  const scoreColor = !score ? "#fff" : score.safety_score >= 90 ? "#4FD08F" : score.safety_score >= 75 ? "#6FB1D6" : score.safety_score >= 50 ? "#F5A623" : "#FF5A68";
  const cols = columnsFor(cams.length);

  return (
    <div className="tv">
      <div className="tv-top">
        <div className="tv-brand">AI Sentinel<small>Live safety wall</small></div>
        <Clock />
        <div className="tv-actions">
          <button type="button" onClick={toggleFull}>{full ? "Exit full screen" : "Full screen"}</button>
          <Link to="/">Back to dashboard</Link>
        </div>
      </div>

      {flash && (
        <div className="tv-flash" role="alert">
          New incident: {pretty(flash.violation)} on {flash.camera_name || "a camera"}
          {flash.zone ? ` (${flash.zone})` : ""} - {flash.incident_id}
        </div>
      )}

      <div className="tv-stats">
        <div className="tv-stat"><b style={{ color: scoreColor }}>{score ? Math.round(score.safety_score) : "-"}</b><span>Safety score, 24 h</span></div>
        <div className="tv-stat"><b>{active}</b><span>Waiting for action</span></div>
        <div className="tv-stat"><b style={{ color: stats && stats.overdue ? "#FF5A68" : "#fff" }}>{stats ? stats.overdue : 0}</b><span>Overdue</span></div>
        <div className="tv-stat"><b style={{ color: stats && stats.critical_open ? "#FF5A68" : "#fff" }}>{stats ? stats.critical_open : 0}</b><span>Critical, not resolved</span></div>
        <div className="tv-stat"><b>{stats ? stats.today : 0}</b><span>Today</span></div>
      </div>

      <div className="tv-main">
        <div className="tv-cams" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
          {cams.map((c) => (
            <div className="tv-cam" key={c.id}>
              {c.status === "online"
                ? <img src={tokenUrl(`/api/cameras/${c.id}/stream`)} alt={`Live feed from ${c.name}`} />
                : <div><b style={{ color: "#fff" }}>{pretty(c.status)}</b><div>{c.error || "No picture"}</div></div>}
              <div className="lbl">
                <span><span className={`dot ${c.status}`} />{c.name}</span>
                {c.open_count ? <span className="cnt">{c.open_count} open</span> : null}
              </div>
            </div>
          ))}
          {!cams.length && <div className="tv-cam"><div>No cameras yet. Add one on the Cameras page.</div></div>}
        </div>

        <aside className="tv-side" aria-label="Latest incidents">
          <h3>Latest incidents</h3>
          {recent.map((i) => (
            <div className="tv-item" key={i.incident_id}>
              <b>{pretty(i.violation)}</b> <Sev value={i.severity} />
              <small>{i.camera_name || "-"} - {fmtTime(i.timestamp)} - {i.incident_id}</small>
            </div>
          ))}
          {!recent.length && <div className="tv-item">No incidents yet.</div>}
        </aside>
      </div>
    </div>
  );
}