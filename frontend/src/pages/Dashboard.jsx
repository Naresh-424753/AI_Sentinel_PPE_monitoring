import React, { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, tokenUrl } from "../api.js";
import { useLive } from "../live.jsx";
import { Empty, Sev, Status, fmtTime, pretty } from "../ui.jsx";

const TYPE_COLORS = ["#D62839", "#F08A00", "#2F6B8F", "#1E9A62", "#6B7C85", "#12222B"];

export default function Dashboard() {
  const { tick } = useLive();
  const [days, setDays] = useState(14);
  const [an, setAn] = useState(null);
  const [score, setScore] = useState(null);
  const [stats, setStats] = useState(null);
  const [cams, setCams] = useState([]);
  const [recent, setRecent] = useState([]);
  const [camId, setCamId] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [rep, setRep] = useState(null);   // repeat offenders for the selected period

  // Data that depends on the selected period: reloads on every 7 / 14 / 30 click.
  useEffect(() => {
    let alive = true;
    setLoading(true);
    api("/api/analytics", { params: { days } })
      .then((a) => { if (alive) { setAn(a); setError(""); } })
      .catch((e) => { if (alive) setError(e.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [days, tick]);

  useEffect(() => {
    let alive = true;
    api("/api/repeat-offenders", { params: { days, min_count: 3 } })
      .then((r) => { if (alive) setRep(r); })
      .catch(() => { if (alive) setRep(null); });
    return () => { alive = false; };
  }, [days, tick]);

  // Everything else. allSettled: one failing request no longer blocks the rest of the page.
  useEffect(() => {
    Promise.allSettled([
      api("/api/safety-score", { params: { hours: 24 } }),
      api("/stats"),
      api("/api/cameras"),
      api("/api/incidents", { params: { limit: 6 } }),
    ]).then(([s, st, c, r]) => {
      if (s.status === "fulfilled") setScore(s.value);
      if (st.status === "fulfilled") setStats(st.value);
      if (r.status === "fulfilled") setRecent(r.value.items);
      if (c.status === "fulfilled") {
        setCams(c.value);
        setCamId((cur) => cur ?? (c.value.find((x) => x.status === "online") || c.value[0] || {}).id ?? null);
      }
    });
  }, [tick]);

  const cam = cams.find((c) => c.id === camId);
  const streamSrc = useMemo(() => (camId && cam && cam.status === "online" ? tokenUrl(`/api/cameras/${camId}/stream`) : null), [camId, cam && cam.status]);
  const scoreColor = !score ? "#12222B" : score.safety_score >= 90 ? "#1E9A62" : score.safety_score >= 75 ? "#2F6B8F" : score.safety_score >= 50 ? "#F08A00" : "#D62839";
  const active = stats ? stats.open_incidents + stats.assigned_incidents : 0;

  return (
    <>
      <div className="pagehead">
        <div>
          <h1>Site overview</h1>
          <p>{loading ? "Updating..." : an ? `Last ${an.days} days: ${an.total} violation${an.total === 1 ? "" : "s"}.` : "Live camera view, today's violations and trends."}</p>
        </div>
        <div className="right">
          <Link to="/tv" className="btnlink">TV mode</Link>
          <div className="tabs" style={{ marginBottom: 0 }}>
            {[7, 14, 30].map((d) => (
              <button key={d} type="button" aria-pressed={days === d} className={days === d ? "on" : ""} onClick={() => setDays(d)}>{d} days</button>
            ))}
          </div>
        </div>
      </div>
      {error && <div className="err">{error}</div>}

      <div className="grid g2" style={{ marginBottom: 16 }}>
        <section className="panel">
          <div className="tabs">
            {cams.map((c) => (
              <button key={c.id} className={c.id === camId ? "on" : ""} onClick={() => setCamId(c.id)}>
                <span className={`dot ${c.status}`} />{c.name}{c.open_count ? ` (${c.open_count})` : ""}
              </button>
            ))}
            {!cams.length && <span className="hint">No cameras yet. Add one on the Cameras page.</span>}
          </div>
          <div className="feed">
            {streamSrc ? <img src={streamSrc} alt={`Live feed from ${cam.name}`} /> : (
              <div>
                <b style={{ color: "#fff" }}>{cam ? pretty(cam.status) : "No camera"}</b>
                <div>{cam && cam.error ? cam.error : "The live view appears here when the camera is online."}</div>
              </div>
            )}
          </div>
        </section>

        <section className="panel score">
          <h3>Safety score, last 24 hours</h3>
          <div className="n" style={{ color: scoreColor }}>{score ? Math.round(score.safety_score) : "-"}</div>
          <div className="lbl">{score ? score.label : ""}</div>
          <div className="meter" style={{ "--c": scoreColor }}><i style={{ width: `${score ? score.safety_score : 0}%` }} /></div>
          <p className="hint">{score ? `${score.total_violations} violations in the window. Critical ones weigh most; resolved ones count half.` : ""}</p>
          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", marginTop: 14 }}>
            <div><div style={{ font: "700 32px var(--display)" }}>{active}</div><div className="hint">Waiting for action</div></div>
            <Link to="/incidents?overdue=1" style={{ textDecoration: "none", color: "inherit" }}>
              <div style={{ font: "700 32px var(--display)", color: stats && stats.overdue ? "#D62839" : "inherit" }}>{stats ? stats.overdue : 0}</div>
              <div className="hint">Overdue (past deadline)</div>
            </Link>
            <div><div style={{ font: "700 32px var(--display)", color: stats && stats.critical_open ? "#D62839" : "inherit" }}>{stats ? stats.critical_open : 0}</div><div className="hint">Critical, not resolved</div></div>
            <div><div style={{ font: "700 32px var(--display)" }}>{stats ? stats.today : 0}</div><div className="hint">Today</div></div>
            <div><div style={{ font: "700 32px var(--display)" }}>{stats ? stats.resolved_incidents : 0}</div><div className="hint">Resolved</div></div>
            <div><div style={{ font: "700 32px var(--display)" }}>{an && an.avg_resolution_minutes != null ? `${Math.round(an.avg_resolution_minutes)}m` : "-"}</div><div className="hint">Avg time to resolve</div></div>
            <Link to="/incidents?review=needs" style={{ textDecoration: "none", color: "inherit" }}>
              <div style={{ font: "700 32px var(--display)", color: stats && stats.needs_review ? "#F08A00" : "inherit" }}>{stats ? stats.needs_review : 0}</div>
              <div className="hint">Needs your decision (AI unsure)</div>
            </Link>
            <Link to="/incidents?review=auto" style={{ textDecoration: "none", color: "inherit" }}>
              <div style={{ font: "700 32px var(--display)", color: "#1E9A62" }}>{stats ? stats.auto_resolved : 0}</div>
              <div className="hint">Solved by AI</div>
            </Link>
          </div>
        </section>
      </div>

      <div className="grid g2" style={{ marginBottom: 16 }}>
        <section className="panel">
          <h3>Violations per day</h3>
          <div style={{ height: 230 }}>
            <ResponsiveContainer>
              <AreaChart data={an ? an.trend : []} margin={{ left: -18, right: 8, top: 8 }}>
                <CartesianGrid stroke="#E2E7EA" vertical={false} />
                <XAxis dataKey="date" tickFormatter={(d) => d.slice(5)} fontSize={12} />
                <YAxis allowDecimals={false} fontSize={12} />
                <Tooltip />
                <Area type="monotone" dataKey="count" name="Violations" stroke="#12222B" strokeWidth={2} fill="#FFD60A" fillOpacity={0.7} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>
        <section className="panel">
          <h3>By violation type</h3>
          {an && an.by_type.length ? (
            <div style={{ height: 230 }}>
              <ResponsiveContainer>
                <BarChart data={an.by_type.map((x) => ({ ...x, name: pretty(x.name.replace(/^no_/, "no ")) }))} layout="vertical" margin={{ left: 10, right: 12 }}>
                  <XAxis type="number" allowDecimals={false} fontSize={12} />
                  <YAxis type="category" dataKey="name" width={90} fontSize={12} />
                  <Tooltip />
                  <Bar dataKey="count" name="Violations">
                    {an.by_type.map((_, i) => <Cell key={i} fill={TYPE_COLORS[i % TYPE_COLORS.length]} />)}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          ) : <Empty>No violations in this period.</Empty>}
        </section>
      </div>

      <div className="grid g2" style={{ marginBottom: 16 }}>
        <section className="panel">
          <h3>Busiest hours</h3>
          <div style={{ height: 190 }}>
            <ResponsiveContainer>
              <BarChart data={an ? an.by_hour : []} margin={{ left: -18, right: 8 }}>
                <XAxis dataKey="hour" fontSize={12} tickFormatter={(h) => `${h}h`} interval={2} />
                <YAxis allowDecimals={false} fontSize={12} />
                <Tooltip labelFormatter={(h) => `${h}:00`} />
                <Bar dataKey="count" name="Violations" fill="#2F6B8F" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </section>
        <section className="panel">
          <h3>By shift and camera</h3>
          <table>
            <tbody>
              {an && an.by_shift.map((s) => <tr key={s.shift}><td>{s.shift}</td><td style={{ textAlign: "right" }}><b>{s.count}</b></td></tr>)}
              {an && an.by_camera.slice(0, 4).map((s) => <tr key={s.name}><td>{s.name}</td><td style={{ textAlign: "right" }}><b>{s.count}</b></td></tr>)}
            </tbody>
          </table>
        </section>
      </div>

      <section className="panel" style={{ marginBottom: 16 }}>
        <h3>Repeat offenders, last {days} days</h3>
        <p className="hint" style={{ marginBottom: 10 }}>
          A violation that keeps going is counted once; it counts again only after a gap of 10 minutes. False alarms are left out.
          The camera cannot tell who a person is, so "same person" below means the same tracking number on the same camera on the same day.
        </p>
        {rep && (rep.hotspots.length || rep.tracks.length) ? (
          <div className="grid g2" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <div>
              <h3 style={{ fontSize: 15 }}>Places where it keeps happening</h3>
              {rep.hotspots.length ? (
                <div className="tablewrap">
                  <table>
                    <thead><tr><th>Camera</th><th>Violation</th><th>Times</th><th>Still open</th><th>Last</th></tr></thead>
                    <tbody>
                      {rep.hotspots.map((h) => (
                        <tr key={`${h.camera_id}-${h.zone}-${h.violation}`}>
                          <td>{h.camera}{h.zone ? <div className="hint">{h.zone}</div> : null}</td>
                          <td>{pretty(h.violation)}</td><td><b>{h.occurrences}</b></td><td>{h.open}</td><td>{fmtTime(h.last_seen)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <Empty>Nothing repeats {rep.min_count} or more times.</Empty>}
            </div>
            <div>
              <h3 style={{ fontSize: 15 }}>Same person, same day</h3>
              {rep.tracks.length ? (
                <div className="tablewrap">
                  <table>
                    <thead><tr><th>Camera</th><th>Person #</th><th>Day</th><th>Times</th><th>What</th></tr></thead>
                    <tbody>
                      {rep.tracks.map((t) => (
                        <tr key={`${t.camera_id}-${t.track_id}-${t.date}`}>
                          <td>{t.camera}</td><td>#{t.track_id}</td><td>{t.date}</td><td><b>{t.occurrences}</b></td>
                          <td>{t.violations.map((v) => pretty(v)).join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <Empty>No tracked person repeated {rep.min_count} or more times.</Empty>}
            </div>
          </div>
        ) : <Empty>No repeated violations in this period.</Empty>}
      </section>

      <section className="panel">
        <div className="pagehead" style={{ marginBottom: 6 }}>
          <h3>Latest incidents</h3>
          <Link to="/incidents">See all</Link>
        </div>
        {recent.length ? (
          <div className="tablewrap">
            <table>
              <thead><tr><th>ID</th><th>Time</th><th>Violation</th><th>Severity</th><th>Camera</th><th>Status</th></tr></thead>
              <tbody>
                {recent.map((i) => (
                  <tr key={i.incident_id}>
                    <td>{i.incident_id}</td><td>{fmtTime(i.timestamp)}</td><td>{pretty(i.violation)}</td>
                    <td><Sev value={i.severity} /></td><td>{i.camera_name || "-"}</td><td><Status value={i.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>No incidents yet. When a worker is seen without PPE, it appears here.</Empty>}
      </section>
    </>
  );
}