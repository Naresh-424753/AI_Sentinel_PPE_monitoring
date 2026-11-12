import React, { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, tokenUrl } from "../api.js";
import { useAuth } from "../auth.jsx";
import { Empty, Modal, fmtTime, pretty } from "../ui.jsx";

const BLANK = { name: "", source: "0", location: "", zone: "", required_ppe: "", enabled: true };

function parseZone(text) {
  try {
    const p = JSON.parse(text || "[]");
    return Array.isArray(p) ? p.filter((q) => Array.isArray(q) && q.length === 2) : [];
  } catch { return []; }
}

// Click on the camera picture to mark the corners of a restricted area. Points are saved as 0..1 of the picture.
function ZoneEditor({ camera, points, setPoints }) {
  const [imgOk, setImgOk] = useState(false);
  const [stamp] = useState(() => Date.now());
  const src = camera.id && camera.status === "online" ? tokenUrl(`/api/cameras/${camera.id}/frame?t=${stamp}`) : null;

  function add(e) {
    const r = e.currentTarget.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    const y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    if (points.length < 40) setPoints([...points, [Math.round(x * 1000) / 1000, Math.round(y * 1000) / 1000]]);
  }
  const poly = points.map((p) => p.join(",")).join(" ");

  return (
    <div className="field">
      <label>Restricted zone (optional)</label>
      <div className={`zonebox ${imgOk ? "" : "empty"}`} onClick={add} role="img" aria-label="Camera picture. Click to add corner points of the restricted zone.">
        {src && <img src={src} alt="" draggable={false} onLoad={() => setImgOk(true)} onError={() => setImgOk(false)} />}
        {!imgOk && <div className="zonehint">{src ? "Loading the camera picture..." : "No picture: the camera is offline. Bring it online to see where you are drawing."}</div>}
        <svg viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
          {points.length >= 3 && <polygon points={poly} className="zpoly" />}
          {points.length === 2 && <polyline points={poly} className="zline" />}
        </svg>
        {points.map((p, i) => <span key={i} className="zdot" style={{ left: `${p[0] * 100}%`, top: `${p[1] * 100}%` }} />)}
      </div>
      <div className="right" style={{ justifyContent: "flex-start", marginTop: 8 }}>
        <button type="button" className="ghost" disabled={!points.length} onClick={() => setPoints(points.slice(0, -1))}>Undo last point</button>
        <button type="button" className="ghost" disabled={!points.length} onClick={() => setPoints([])}>Clear zone</button>
      </div>
      <div className="hint">
        {points.length >= 3 ? `Zone with ${points.length} points.` : points.length ? "Add at least 3 points, or press Clear zone." : "No zone set."}{" "}
        Click the corners of the area people must not enter. A person whose feet are inside it raises a "Restricted zone" incident.
        This needs the model to detect "person".
      </div>
    </div>
  );
}

export default function Cameras() {
  const { can } = useAuth();
  const nav = useNavigate();
  const [cams, setCams] = useState([]);
  const [edit, setEdit] = useState(null);   // camera object or BLANK
  const [zone, setZone] = useState([]);     // points of the zone being drawn
  const [live, setLive] = useState(null);
  const [err, setErr] = useState("");

  const openEdit = (c) => { setZone(parseZone(c.restricted_zone)); setErr(""); setEdit({ ...c, enabled: !!c.enabled }); };

  const load = useCallback(() => api("/api/cameras").then(setCams).catch((e) => setErr(e.message)), []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);

  async function save(e) {
    e.preventDefault(); setErr("");
    if (edit.id && zone.length > 0 && zone.length < 3) { setErr("A restricted zone needs at least 3 points. Add more points or press Clear zone."); return; }
    try {
      const body = { name: edit.name, source: edit.source, location: edit.location, zone: edit.zone, required_ppe: edit.required_ppe, enabled: !!edit.enabled };
      if (edit.id) body.restricted_zone = zone.length >= 3 ? JSON.stringify(zone) : "";
      if (edit.id) await api(`/api/cameras/${edit.id}`, { method: "PUT", body });
      else await api("/api/cameras", { method: "POST", body });
      setEdit(null); load();
    } catch (ex) { setErr(ex.message); }
  }
  async function remove(c) {
    if (!window.confirm(`Remove camera "${c.name}"? Past incidents are kept.`)) return;
    try { await api(`/api/cameras/${c.id}`, { method: "DELETE" }); load(); } catch (ex) { setErr(ex.message); }
  }
  const set = (k) => (e) => setEdit({ ...edit, [k]: e.target.value });

  return (
    <>
      <div className="pagehead">
        <div><h1>Cameras</h1><p>Each camera can have its own PPE rules, for example goggles only in the welding bay.</p></div>
        {can("ADMIN") && <button className="hivis" onClick={() => { setZone([]); setErr(""); setEdit({ ...BLANK }); }}>Add camera</button>}
      </div>
      {err && <div className="err">{err}</div>}
      {cams.length ? (
        <div className="grid g3">
          {cams.map((c) => (
            <section key={c.id} className="panel">
              <h3><span className={`dot ${c.status}`} />{c.name}</h3>
              <dl className="meta" style={{ marginTop: 10 }}>
                <dt>Status</dt><dd>{pretty(c.status)}{c.status === "online" ? ` (${c.fps} fps)` : ""}</dd>
                <dt>Source</dt><dd style={{ wordBreak: "break-all" }}>{String(c.source).replace(/\/\/[^@/]+@/, "//***@")}</dd>
                <dt>Location</dt><dd>{c.location || "-"}</dd>
                <dt>Zone</dt><dd>{c.zone || "-"}</dd>
                <dt>Open incidents</dt><dd>{c.open_count || 0} (today: {c.today_count || 0})</dd>
                <dt>Last incident</dt><dd>{c.last_incident ? fmtTime(c.last_incident) : "-"}</dd>
                <dt>PPE rules</dt><dd>{c.required_ppe ? c.required_ppe.split(",").map((x) => pretty(x.trim())).join(", ") : "All violation types"}</dd>
                <dt>Restricted zone</dt><dd>{parseZone(c.restricted_zone).length >= 3 ? "Drawn" : "-"}</dd>
              </dl>
              {c.error && <div className="err" style={{ fontSize: 13 }}>{c.error}</div>}
              <div className="right" style={{ justifyContent: "flex-start" }}>
                <button disabled={c.status !== "online"} onClick={() => setLive(c)}>Live view</button>
                <button className="ghost" onClick={() => nav(`/incidents?camera_id=${c.id}`)}>Incidents</button>
                {can("ADMIN") && <button className="ghost" onClick={() => openEdit(c)}>Edit</button>}
                {can("ADMIN") && <button className="ghost" onClick={() => remove(c)}>Remove</button>}
              </div>
            </section>
          ))}
        </div>
      ) : <Empty>No cameras configured.</Empty>}

      {live && (
        <Modal title={live.name} onClose={() => setLive(null)} wide>
          <div className="feed"><img src={tokenUrl(`/api/cameras/${live.id}/stream`)} alt={`Live feed from ${live.name}`} /></div>
        </Modal>
      )}

      {edit && (
        <Modal title={edit.id ? "Edit camera" : "Add camera"} onClose={() => setEdit(null)}>
          <form onSubmit={save}>
            <div className="field"><label htmlFor="cn">Name</label><input id="cn" value={edit.name} onChange={set("name")} required /></div>
            <div className="field">
              <label htmlFor="cs">Source</label>
              <input id="cs" value={edit.source} onChange={set("source")} required />
              <div className="hint">0 = laptop webcam, 1 = second USB camera, or an RTSP address like rtsp://user:pass@192.168.1.20:554/stream, or a video file path.</div>
            </div>
            <div className="field"><label htmlFor="cl">Location</label><input id="cl" value={edit.location} onChange={set("location")} /></div>
            <div className="field"><label htmlFor="cz">Zone</label><input id="cz" value={edit.zone} onChange={set("zone")} placeholder="Welding, Loading dock..." /></div>
            <div className="field">
              <label htmlFor="cp">Required PPE for this zone</label>
              <input id="cp" value={edit.required_ppe} onChange={set("required_ppe")} placeholder="no_goggle, no_gloves" />
              <div className="hint">Violation classes to enforce here, separated by commas. Leave empty to enforce all.</div>
            </div>
            {edit.id
              ? <ZoneEditor camera={edit} points={zone} setPoints={setZone} />
              : <div className="hint" style={{ marginBottom: 12 }}>To mark a restricted zone, save the camera first, then press Edit on it.</div>}
            <label style={{ display: "flex", gap: 8, alignItems: "center", color: "inherit" }}>
              <input type="checkbox" style={{ width: "auto" }} checked={!!edit.enabled} onChange={(e) => setEdit({ ...edit, enabled: e.target.checked })} />
              Camera enabled
            </label>
            {err && <div className="err">{err}</div>}
            <div className="right" style={{ marginTop: 14 }}>
              <button type="button" className="ghost" onClick={() => setEdit(null)}>Cancel</button>
              <button className="hivis">Save camera</button>
            </div>
          </form>
        </Modal>
      )}
    </>
  );
}