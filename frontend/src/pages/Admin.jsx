import React, { useCallback, useEffect, useState } from "react";
import { api, download } from "../api.js";
import { useAuth } from "../auth.jsx";
import { Empty, fmtTime } from "../ui.jsx";

const ROLES = ["VIEWER", "SUPERVISOR", "ADMIN", "SUPER_ADMIN"];

function Users() {
  const { user, can } = useAuth();
  const [rows, setRows] = useState([]);
  const [err, setErr] = useState("");
  const load = useCallback(() => api("/api/users").then(setRows).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); }, [load]);

  async function patch(id, body) {
    setErr("");
    try { await api(`/api/users/${id}`, { method: "PATCH", body }); load(); } catch (e) { setErr(e.message); }
  }
  const roleOptions = (current) => {
    const opts = can("SUPER_ADMIN") ? ROLES : ROLES.filter((r) => r !== "ADMIN" && r !== "SUPER_ADMIN");
    return current && !opts.includes(current) ? [current, ...opts] : opts;
  };

  return (
    <>
      {err && <div className="err">{err}</div>}
      <div className="tablewrap">
        <table>
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Joined</th></tr></thead>
          <tbody>
            {rows.map((u) => (
              <tr key={u.id}>
                <td><b>{u.name}</b></td><td>{u.email}</td>
                <td>
                  <select aria-label={`Role for ${u.name}`} value={u.role} disabled={u.id === user.id}
                    onChange={(e) => patch(u.id, { role: e.target.value })} style={{ width: 150 }}>
                    {roleOptions(u.role).map((r) => <option key={r} value={r}>{r.replace("_", " ").toLowerCase()}</option>)}
                  </select>
                </td>
                <td>
                  <button className="ghost" disabled={u.id === user.id}
                    onClick={() => patch(u.id, { status: u.status === "ACTIVE" ? "INACTIVE" : "ACTIVE" })}>
                    {u.status === "ACTIVE" ? "Active - deactivate" : "Inactive - activate"}
                  </button>
                </td>
                <td>{fmtTime(u.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="hint">Viewers can look. Supervisors can assign and close incidents. Admins manage cameras and users.</p>
    </>
  );
}

function Audit() {
  const [rows, setRows] = useState([]);
  useEffect(() => { api("/api/audit").then(setRows).catch(() => {}); }, []);
  if (!rows.length) return <Empty>No activity recorded yet.</Empty>;
  return (
    <div className="tablewrap">
      <table>
        <thead><tr><th>Time</th><th>Who</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}><td>{fmtTime(r.ts)}</td><td>{r.actor}</td><td><b>{r.action}</b></td><td>{[r.target_type, r.target_id].filter(Boolean).join(" ")}</td><td>{r.detail}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Alerts() {
  const [st, setSt] = useState(null);
  const [msg, setMsg] = useState("");
  useEffect(() => { api("/api/alerts/status").then(setSt).catch(() => {}); }, []);
  async function test() {
    try { const r = await api("/api/alerts/test", { method: "POST" }); setMsg(r.message); } catch (e) { setMsg(e.message); }
  }
  if (!st) return null;
  const names = { telegram: "Telegram", email: "Email", whatsapp: "WhatsApp (Twilio)", webhook: "Webhook" };
  const any = Object.keys(names).some((k) => st[k]);
  return (
    <>
      <table style={{ maxWidth: 520 }}>
        <tbody>
          {Object.entries(names).map(([k, label]) => (
            <tr key={k}><td>{label}</td><td><span className={`dot ${st[k] ? "online" : ""}`} />{st[k] ? "Configured" : "Not configured"}</td></tr>
          ))}
          <tr><td>Alert from severity</td><td><b>{st.min_severity}</b></td></tr>
        </tbody>
      </table>
      <p className="hint">Channels are set in the .env file on the server. Restart the backend after changing it.</p>
      <button className="hivis" disabled={!any} onClick={test}>Send test alert</button>
      {msg && <div className="ok">{msg}</div>}
    </>
  );
}

const DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
const DAY_NAME = { MON: "Mon", TUE: "Tue", WED: "Wed", THU: "Thu", FRI: "Fri", SAT: "Sat", SUN: "Sun" };

function QuietHours() {
  const [s, setS] = useState(null);
  const [msg, setMsg] = useState({ ok: true, text: "" });
  const [busy, setBusy] = useState(false);
  useEffect(() => { api("/api/settings/mute").then(setS).catch((e) => setMsg({ ok: false, text: e.message })); }, []);
  if (!s) return msg.text ? <div className="err">{msg.text}</div> : null;

  const setWin = (i, patch) => setS({ ...s, windows: s.windows.map((w, j) => (j === i ? { ...w, ...patch } : w)) });
  const toggleDay = (i, d) => {
    const w = s.windows[i];
    setWin(i, { days: w.days.includes(d) ? w.days.filter((x) => x !== d) : [...w.days, d] });
  };
  const addWin = () => setS({ ...s, windows: [...s.windows, { start: "13:00", end: "14:00", days: DAYS.slice(0, 5) }] });
  const delWin = (i) => setS({ ...s, windows: s.windows.filter((_, j) => j !== i) });

  async function save() {
    setBusy(true); setMsg({ ok: true, text: "" });
    try {
      const r = await api("/api/settings/mute", { method: "PUT", body: { enabled: !!s.enabled, allow_critical: !!s.allow_critical, windows: s.windows } });
      setS(r); setMsg({ ok: true, text: "Saved." });
    } catch (e) { setMsg({ ok: false, text: e.message }); }
    setBusy(false);
  }

  const row = { display: "flex", gap: 8, alignItems: "center", color: "inherit", fontSize: 14, margin: 0 };
  return (
    <>
      <label style={{ ...row, marginBottom: 8 }}>
        <input type="checkbox" style={{ width: "auto" }} checked={!!s.enabled} onChange={(e) => setS({ ...s, enabled: e.target.checked })} />
        Hold back alerts during these hours
      </label>
      <label style={{ ...row, marginBottom: 14 }}>
        <input type="checkbox" style={{ width: "auto" }} checked={!!s.allow_critical} onChange={(e) => setS({ ...s, allow_critical: e.target.checked })} />
        Still send Critical alerts
      </label>

      {s.windows.map((w, i) => (
        <div key={i} className="panel" style={{ marginBottom: 10, padding: 12 }}>
          <div style={{ display: "flex", gap: 12, alignItems: "end", flexWrap: "wrap" }}>
            <div><label htmlFor={`qs${i}`}>From</label><input id={`qs${i}`} type="time" style={{ width: 130 }} value={w.start} onChange={(e) => setWin(i, { start: e.target.value })} /></div>
            <div><label htmlFor={`qe${i}`}>Until</label><input id={`qe${i}`} type="time" style={{ width: 130 }} value={w.end} onChange={(e) => setWin(i, { end: e.target.value })} /></div>
            <button type="button" className="ghost" onClick={() => delWin(i)}>Remove</button>
          </div>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 10 }}>
            {DAYS.map((d) => (
              <label key={d} style={row}>
                <input type="checkbox" style={{ width: "auto" }} checked={w.days.includes(d)} onChange={() => toggleDay(i, d)} />{DAY_NAME[d]}
              </label>
            ))}
          </div>
          {w.start > w.end && <div className="hint">This window runs past midnight and belongs to the day it starts on.</div>}
        </div>
      ))}
      {!s.windows.length && <p className="hint">No quiet hours yet. Add a window, for example the lunch break or a shift change.</p>}

      <div className="right" style={{ justifyContent: "flex-start", marginTop: 8 }}>
        <button type="button" className="ghost" disabled={s.windows.length >= 10} onClick={addWin}>Add a window</button>
        <button className="hivis" disabled={busy} onClick={save}>Save</button>
      </div>
      {msg.text && <div className={msg.ok ? "ok" : "err"}>{msg.text}</div>}

      <p style={{ marginTop: 14 }}>
        <span className={`dot ${s.enabled && s.muted_now ? "error" : "online"}`} />
        {s.enabled && s.muted_now ? "Quiet hours are active now: alerts are being held back." : "Alerts are going out normally right now."}
        <span className="hint"> Server time {s.server_time}.</span>
      </p>
      <p className="hint">
        Applies to Telegram, e-mail, WhatsApp and webhook alerts. Incidents are still detected, saved and shown on the dashboard.
        Alerts held back are not sent later. Personal notifications (assignments, replies, overdue) are not affected.
        The "Send test alert" button always sends. Times are the server's clock.
      </p>
    </>
  );
}

function Reports() {
  const [st, setSt] = useState(null);
  const [msg, setMsg] = useState({ ok: true, text: "" });
  const [busy, setBusy] = useState(false);
  useEffect(() => { api("/api/reports/status").then(setSt).catch(() => {}); }, []);

  async function send(period) {
    setBusy(true); setMsg({ ok: true, text: "" });
    try {
      const r = await api("/api/reports/send-now", { method: "POST", params: { period } });
      setMsg({ ok: true, text: r.message });
    } catch (e) { setMsg({ ok: false, text: e.message }); }
    setBusy(false);
  }
  if (!st) return null;
  const sent = st.last_sent || {};
  return (
    <>
      <table style={{ maxWidth: 560 }}>
        <tbody>
          <tr><td>Daily report</td><td>{st.daily_time ? `every day at ${st.daily_time}` : "Off"}</td></tr>
          <tr><td>Weekly report</td><td>{st.weekly_day && st.daily_time ? `every ${st.weekly_day} at ${st.daily_time}` : "Off"}</td></tr>
          <tr><td>Sent to</td><td>{st.recipients.length ? st.recipients.join(", ") : "Nobody yet"}</td></tr>
          <tr><td>E-mail</td><td><span className={`dot ${st.email_configured ? "online" : ""}`} />{st.email_configured ? "Configured" : "Not configured (SMTP_EMAIL / SMTP_PASSWORD in .env)"}</td></tr>
          <tr><td>Last daily / weekly</td><td>{sent.daily || "-"} / {sent.weekly || "-"}</td></tr>
        </tbody>
      </table>
      <p className="hint">Each report is an e-mail summary with the full incident list attached as a PDF. Times and recipients are set in the .env file.</p>
      <div className="right" style={{ justifyContent: "flex-start" }}>
        <button className="hivis" disabled={busy} onClick={() => send("daily")}>Send yesterday's report now</button>
        <button className="ghost" disabled={busy} onClick={() => send("weekly")}>Send last 7 days now</button>
      </div>
      {msg.text && <div className={msg.ok ? "ok" : "err"}>{msg.text}</div>}
    </>
  );
}

function Training() {
  const [st, setSt] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api("/api/training/status").then(setSt).catch(() => {}); }, []);
  async function get() {
    setErr("");
    try { await download("/api/training/export", {}, "training_pack.zip"); } catch (e) { setErr(e.message); }
  }
  if (!st) return null;
  return (
    <>
      <p>
        <b>{st.false_alarms}</b> incidents are marked "False alarm", and <b>{st.with_frames}</b> of them have a saved camera frame.
      </p>
      <p className="hint">
        Mark wrong detections as "False alarm" in Incidents. Download them here, correct the labels in a labelling tool,
        add them to your dataset and retrain. This is the quickest way to make the model more accurate on your own site.
        Frames are saved for new incidents only (not for ones created before this feature).
      </p>
      <button className="hivis" disabled={!st.with_frames} onClick={get}>Download training pack (.zip)</button>
      {err && <div className="err">{err}</div>}
    </>
  );
}

export default function Admin() {
  const [tab, setTab] = useState("users");
  return (
    <>
      <div className="pagehead"><div><h1>Admin</h1><p>People, activity history and alert channels.</p></div></div>
      <div className="tabs">
        {[["users", "Users"], ["audit", "Activity log"], ["alerts", "Alerts"], ["quiet", "Quiet hours"], ["reports", "Reports"], ["training", "Retraining"]].map(([k, l]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      <section className="panel">
        {tab === "users" && <Users />}
        {tab === "audit" && <Audit />}
        {tab === "alerts" && <Alerts />}
        {tab === "quiet" && <QuietHours />}
        {tab === "reports" && <Reports />}
        {tab === "training" && <Training />}
      </section>
    </>
  );
}