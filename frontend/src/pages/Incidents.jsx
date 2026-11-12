import React, { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, asset, download, tokenUrl } from "../api.js";
import { useAuth } from "../auth.jsx";
import { useLive } from "../live.jsx";
import { Empty, Modal, STATUS_LABEL, Sev, Status, fmtTime, pretty } from "../ui.jsx";

const PAGE = 25;
const EMPTY = { q: "", status: "", severity: "", violation: "", camera_id: "", date_from: "", date_to: "", assigned_to: "", overdue: "", review: "" };

function AiTag({ value }) {
  if (value === "AUTO_RESOLVED") return <span className="tag ai-ok">AI solved</span>;
  if (value === "NEEDS_REVIEW" || value === "LOW_CONFIDENCE") return <span className="tag ai-warn">AI unsure</span>;
  return null;
}

// Shrink a photo in the browser before upload (phone photos are big).
function readImage(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const s = Math.min(1, 1280 / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * s);
      c.height = Math.round(img.height * s);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("Could not read this image")); };
    img.src = url;
  });
}

function Detail({ inc, onClose, onSaved }) {
  const { user, can } = useAuth();
  const staff = can("SUPERVISOR");
  const mine = [user.name, user.email].some((v) => v && (inc.assigned_to || "").trim().toLowerCase() === v.trim().toLowerCase());
  const [users, setUsers] = useState([]);
  const [status, setStatus] = useState(inc.status || "OPEN");
  const [assigned, setAssigned] = useState(inc.assigned_to || "");
  const [notes, setNotes] = useState(inc.resolution_notes || "");
  const [comments, setComments] = useState([]);
  const [reply, setReply] = useState("");
  const [photo, setPhoto] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (staff) api("/api/users/lite").then(setUsers).catch(() => {});
  }, [staff]);

  const loadComments = useCallback(
    () => api(`/incidents/${inc.incident_id}/comments`).then(setComments).catch(() => {}),
    [inc.incident_id]
  );
  useEffect(() => { loadComments(); }, [loadComments]);

  async function save(next) {
    setBusy(true); setErr("");
    try {
      const body = { status: next || status, assigned_to: assigned, resolution_notes: notes };
      await api(`/incidents/${inc.incident_id}`, { method: "PATCH", body });
      onSaved();
    } catch (e) { setErr(e.message); setBusy(false); }
  }
  async function remove() {
    if (!window.confirm(`Delete ${inc.incident_id}? This removes its screenshot, clip and replies.`)) return;
    try { await api(`/incidents/${inc.incident_id}`, { method: "DELETE" }); onSaved(); } catch (e) { setErr(e.message); }
  }
  async function pickPhoto(e) {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try { setPhoto(await readImage(file)); setErr(""); } catch (ex) { setErr(ex.message); }
  }
  async function sendReply(thenResolve) {
    setBusy(true); setErr("");
    try {
      if (reply.trim() || photo) {
        await api(`/incidents/${inc.incident_id}/comments`, { method: "POST", body: { message: reply.trim(), image: photo } });
        setReply(""); setPhoto(null);
      }
      if (thenResolve) {
        await api(`/incidents/${inc.incident_id}`, { method: "PATCH", body: { status: "RESOLVED" } });
        onSaved();
        return;
      }
      loadComments();
    } catch (e) { setErr(e.message); }
    setBusy(false);
  }

  const shot = asset("images", inc.screenshot);
  const clip = asset("clips", inc.clip);
  const canClose = inc.status === "OPEN" || inc.status === "ASSIGNED";
  const hasProof = !!(reply.trim() || photo);
  const aiUnsure = inc.ai_review === "NEEDS_REVIEW" || inc.ai_review === "LOW_CONFIDENCE";
  return (
    <Modal title={`${inc.incident_id} - ${pretty(inc.violation)}`} onClose={onClose} wide>
      <div className="split">
        <div className="media">
          {shot ? <img src={shot} alt="Incident screenshot" /> : <Empty>No screenshot saved.</Empty>}
          {clip ? <video src={clip} controls preload="metadata" /> : <div className="hint">Video clip appears about 10 seconds after the incident.</div>}
        </div>
        <div>
          <dl className="meta">
            <dt>Severity</dt><dd><Sev value={inc.severity} /></dd>
            <dt>Status</dt><dd><Status value={inc.status} /></dd>
            <dt>Deadline</dt><dd className={inc.overdue ? "due-over" : ""}>{fmtTime(inc.due_at)} {inc.overdue && <span className="tag over">Overdue</span>}</dd>
            <dt>Time</dt><dd>{fmtTime(inc.timestamp)}</dd>
            <dt>Camera</dt><dd>{inc.camera_name || "-"}{inc.zone ? ` (${inc.zone})` : ""}</dd>
            <dt>Confidence</dt><dd>{Math.round((inc.confidence || 0) * 100)}%</dd>
            <dt>Worker track</dt><dd>{inc.track_id != null ? `#${inc.track_id}` : "-"}</dd>
            <dt>Duration</dt><dd>{inc.duration_sec ? `${Math.round(inc.duration_sec)} s` : "-"}</dd>
            <dt>Details</dt><dd>{inc.description || "-"}</dd>
            {!staff && (<><dt>Assigned to</dt><dd>{inc.assigned_to || "-"}</dd></>)}
            {inc.resolved_at && (<><dt>Closed</dt><dd>{fmtTime(inc.resolved_at)}</dd></>)}
          </dl>

          <div className="right" style={{ justifyContent: "flex-start", margin: "0 0 14px" }}>
            <a className="btnlink" href={tokenUrl(`/api/incidents/${encodeURIComponent(inc.incident_id)}/report.html`)} target="_blank" rel="noreferrer">View / print report</a>
            <a className="btnlink" href={tokenUrl(`/api/incidents/${encodeURIComponent(inc.incident_id)}/report.md`)}>Download report (.md)</a>
          </div>

          {inc.ai_review === "AUTO_RESOLVED" && (
            <div className="aibox ok">
              <b>Solved by AI</b>
              <div>{inc.ai_note}</div>
              {staff && <button className="ghost" disabled={busy} onClick={() => save("OPEN")}>Reopen - the AI was wrong</button>}
            </div>
          )}
          {aiUnsure && canClose && (
            <div className="aibox warn">
              <b>The AI could not decide</b>
              <div>{inc.ai_note}</div>
              {staff ? (
                <>
                  <div className="hint">What to do:</div>
                  <ol>
                    <li>Watch the screenshot and the clip.</li>
                    <li>The worker is wearing the PPE: press <b>False alarm</b>.</li>
                    <li>It is a real violation: assign it to someone (they get a notification), or fix it on site and press <b>Mark resolved</b>.</li>
                    <li>You cannot tell: ask the site supervisor to check on the spot, then write the result in Replies.</li>
                  </ol>
                </>
              ) : (
                <div className="hint">Check on site and reply with what you found (a photo helps). A supervisor will close it.</div>
              )}
            </div>
          )}

          {staff && (
            <>
              <div className="field">
                <label htmlFor="as">Assigned to</label>
                <select id="as" value={assigned} onChange={(e) => setAssigned(e.target.value)}>
                  <option value="">Nobody</option>
                  {users.map((u) => <option key={u.id} value={u.name}>{u.name}</option>)}
                  {assigned && !users.some((u) => u.name === assigned) && <option value={assigned}>{assigned}</option>}
                </select>
                <div className="hint">The person gets a notification and an e-mail when you save.</div>
              </div>
              <div className="field">
                <label htmlFor="st">Status</label>
                <select id="st" value={status} onChange={(e) => setStatus(e.target.value)}>
                  {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="nt">Resolution notes</label>
                <textarea id="nt" rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What was done about it?" />
              </div>
              <div className="right" style={{ justifyContent: "flex-start" }}>
                <button className="hivis" disabled={busy} onClick={() => save()}>Save changes</button>
                {inc.status !== "RESOLVED" && <button disabled={busy} onClick={() => save("RESOLVED")}>Mark resolved</button>}
                {inc.status !== "FALSE_POSITIVE" && <button className="ghost" disabled={busy} onClick={() => save("FALSE_POSITIVE")}>False alarm</button>}
                {can("ADMIN") && <button className="danger" onClick={remove}>Delete</button>}
              </div>
            </>
          )}

          <h3 style={{ marginTop: 18 }}>Replies</h3>
          {comments.length ? comments.map((c) => (
            <div key={c.id} className="comment">
              <b>{c.author}</b> <span className="hint">{fmtTime(c.created_at)}</span>
              {c.message && <div>{c.message}</div>}
              {c.attachment && (
                <a href={asset("proofs", c.attachment)} target="_blank" rel="noreferrer">
                  <img className="proof" src={asset("proofs", c.attachment)} alt="Proof photo" />
                </a>
              )}
            </div>
          )) : <div className="hint">No replies yet.</div>}

          {(staff || mine) ? (
            <div style={{ marginTop: 10 }}>
              <label htmlFor="rp">{mine && !staff ? "Your reply to the supervisor" : "Write a reply"}</label>
              <textarea id="rp" rows={3} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="What did you do? Any problem?" />
              <div className="field" style={{ marginTop: 8 }}>
                <label htmlFor="ph">Proof photo (optional)</label>
                <input id="ph" type="file" accept="image/*" capture="environment" onChange={pickPhoto} />
                {photo && (
                  <div className="preview">
                    <img src={photo} alt="Selected proof preview" />
                    <button type="button" className="ghost" onClick={() => setPhoto(null)}>Remove photo</button>
                  </div>
                )}
              </div>
              {err && <div className="err">{err}</div>}
              <div className="right" style={{ justifyContent: "flex-start", marginTop: 8 }}>
                <button className="hivis" disabled={busy || !hasProof} onClick={() => sendReply(false)}>Send reply</button>
                {mine && !staff && canClose && (
                  <button disabled={busy || !hasProof} onClick={() => sendReply(true)}>Reply and mark resolved</button>
                )}
              </div>
              {mine && !staff && canClose && !hasProof && <div className="hint">Add a note or a photo to mark it resolved.</div>}
            </div>
          ) : (
            <div className="hint" style={{ marginTop: 8 }}>Only the assigned person or a supervisor can reply.</div>
          )}
        </div>
      </div>
    </Modal>
  );
}

export default function Incidents() {
  const { user, can } = useAuth();
  const staff = can("SUPERVISOR");
  const { tick } = useLive();
  const [params, setParams] = useSearchParams();
  const [f, setF] = useState(EMPTY);
  const [applied, setApplied] = useState(EMPTY);
  const [page, setPage] = useState(0);
  const [data, setData] = useState({ items: [], total: 0 });
  const [cams, setCams] = useState([]);
  const [open, setOpen] = useState(null);
  const [error, setError] = useState("");
  const [showFilters, setShowFilters] = useState(false);   // phones: filters start folded away so the list is visible
  const [sel, setSel] = useState([]);            // incident ids ticked for a bulk action
  const [bulkNote, setBulkNote] = useState("");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [bulkMsg, setBulkMsg] = useState({ ok: true, text: "" });

  const load = useCallback(() => {
    api("/api/incidents", { params: { ...applied, limit: PAGE, offset: page * PAGE } })
      .then((d) => { setData(d); setError(""); })
      .catch((e) => setError(e.message));
  }, [applied, page]);

  useEffect(() => { load(); }, [load, tick]);
  useEffect(() => { api("/api/cameras").then(setCams).catch(() => {}); }, []);
  useEffect(() => { setSel([]); }, [applied]);   // a new filter starts a new selection

  // Links from notifications / toasts / the dashboard: /incidents?open=INC-0001 or ?overdue=1
  useEffect(() => {
    const id = params.get("open");
    const overdue = params.get("overdue");
    if (id) api(`/incidents/${encodeURIComponent(id)}`).then(setOpen).catch(() => {});
    const camera = params.get("camera_id");
    const review = params.get("review");
    if (review) { const n = { ...EMPTY, review }; setF(n); setApplied(n); setPage(0); }
    if (overdue) { const n = { ...EMPTY, overdue: "1" }; setF(n); setApplied(n); setPage(0); }
    if (camera) { const n = { ...EMPTY, camera_id: camera }; setF(n); setApplied(n); setPage(0); }
    if (id || overdue || camera || review) setParams({}, { replace: true });
  }, [params, setParams]);

  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const apply = (e) => { e && e.preventDefault(); setPage(0); setApplied(f); };
  const reset = () => { setF(EMPTY); setApplied(EMPTY); setPage(0); };
  const myTasks = () => { const n = { ...EMPTY, assigned_to: user.name }; setF(n); setApplied(n); setPage(0); };
  const pages = Math.max(1, Math.ceil(data.total / PAGE));

  // ---- bulk close ----
  const isOpenInc = (i) => i.status === "OPEN" || i.status === "ASSIGNED";
  const pageOpen = data.items.filter(isOpenInc).map((i) => i.incident_id);
  const allPageSelected = pageOpen.length > 0 && pageOpen.every((id) => sel.includes(id));
  const toggle = (id) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const togglePage = () => setSel((s) => (allPageSelected ? s.filter((id) => !pageOpen.includes(id)) : [...new Set([...s, ...pageOpen])]));

  async function selectAllMatching() {
    try {
      const d = await api("/api/incidents", { params: { ...applied, limit: 500, offset: 0 } });
      const ids = d.items.filter(isOpenInc).map((i) => i.incident_id);
      setSel(ids);
      setBulkMsg({ ok: true, text: d.total > 500 ? `Selected the first ${ids.length} open incidents (500 at a time).` : `Selected ${ids.length} open incident${ids.length === 1 ? "" : "s"}.` });
    } catch (e) { setBulkMsg({ ok: false, text: e.message }); }
  }

  async function bulkClose(status) {
    const what = status === "RESOLVED" ? "resolved" : "false alarms";
    if (!window.confirm(`Mark ${sel.length} incident${sel.length === 1 ? "" : "s"} as ${what}? The history of each one will say who did it.`)) return;
    setBulkBusy(true); setBulkMsg({ ok: true, text: "" });
    try {
      const r = await api("/api/incidents/bulk", { method: "POST", body: { ids: sel, status, note: bulkNote } });
      const extra = [r.skipped_already_closed ? `${r.skipped_already_closed} already closed` : "", r.not_found ? `${r.not_found} not found` : ""].filter(Boolean).join(", ");
      setBulkMsg({ ok: true, text: `${r.updated} closed as ${what}${extra ? ` (${extra})` : ""}.` });
      setSel([]); setBulkNote(""); load();
    } catch (e) { setBulkMsg({ ok: false, text: e.message }); }
    setBulkBusy(false);
  }

  async function exportAs(fmt) {
    try { await download("/api/incidents/export", { ...applied, format: fmt }, fmt === "pdf" ? "Safety_Audit_Report.pdf" : `incidents.${fmt}`); }
    catch (e) { setError(e.message); }
  }

  return (
    <>
      <div className="pagehead">
        <div><h1>Incidents</h1><p>{data.total} matching. Click a row to review, assign or close it.</p></div>
        <div className="right">
          <button className="ghost" onClick={() => exportAs("csv")}>Export CSV</button>
          <button className="ghost" onClick={() => exportAs("xlsx")}>Export Excel</button>
          <button className="ghost" onClick={() => exportAs("pdf")}>Export PDF</button>
        </div>
      </div>

      <button type="button" className="ghost mo filtbtn" aria-expanded={showFilters} onClick={() => setShowFilters((v) => !v)}>
        {showFilters ? "Hide filters" : "Filters and search"}
      </button>
      <form className={`panel ${showFilters ? "" : "fold"}`} onSubmit={apply} style={{ marginBottom: 16 }}>
        <div className="filters">
          <input placeholder="Search ID, camera, person" aria-label="Search" value={f.q} onChange={set("q")} />
          <select aria-label="Status" value={f.status} onChange={set("status")}>
            <option value="">Any status</option>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <select aria-label="Severity" value={f.severity} onChange={set("severity")}>
            <option value="">Any severity</option>
            {["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((s) => <option key={s} value={s}>{pretty(s.toLowerCase())}</option>)}
          </select>
          <select aria-label="Violation" value={f.violation} onChange={set("violation")}>
            <option value="">Any violation</option>
            {["no_helmet", "no_goggle", "no_gloves", "no_boots", "no_vest", "no_mask", "no_harness", "restricted_zone"].map((v) => <option key={v} value={v}>{pretty(v)}</option>)}
          </select>
          <select aria-label="Camera" value={f.camera_id} onChange={set("camera_id")}>
            <option value="">Any camera</option>
            {cams.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <button>Apply</button>
        </div>
        <div className="filters dates">
          <div><label htmlFor="df">From</label><input id="df" type="date" value={f.date_from} onChange={set("date_from")} /></div>
          <div><label htmlFor="dt">To</label><input id="dt" type="date" value={f.date_to} onChange={set("date_to")} /></div>
          <div>
            <label htmlFor="ov">Deadline</label>
            <select id="ov" value={f.overdue} onChange={set("overdue")}>
              <option value="">Any</option>
              <option value="1">Overdue only</option>
            </select>
          </div>
          <div>
            <label htmlFor="ai">AI result</label>
            <select id="ai" value={f.review} onChange={set("review")}>
              <option value="">Any</option>
              <option value="needs">Needs my decision</option>
              <option value="auto">Solved by AI</option>
            </select>
          </div>
          <div className="right" style={{ justifyContent: "flex-start" }}>
            <button type="button" className="ghost" onClick={myTasks}>My tasks</button>
            <button type="button" className="ghost" onClick={reset}>Clear filters</button>
          </div>
        </div>
      </form>

      {error && <div className="err">{error}</div>}
      <section className="panel">
        {staff && (
          <div className="bulkbar" role="region" aria-label="Close several incidents at once">
            {sel.length > 0 ? (
              <>
                <b>{sel.length} selected</b>
                <input className="bulknote" placeholder="Note for all of them (optional)" aria-label="Note for all selected incidents"
                  maxLength={1000} value={bulkNote} onChange={(e) => setBulkNote(e.target.value)} />
                <button className="hivis" disabled={bulkBusy} onClick={() => bulkClose("RESOLVED")}>Mark resolved</button>
                <button disabled={bulkBusy} onClick={() => bulkClose("FALSE_POSITIVE")}>False alarm</button>
                <button className="ghost" disabled={bulkBusy} onClick={() => { setSel([]); setBulkMsg({ ok: true, text: "" }); }}>Clear selection</button>
              </>
            ) : (
              <span className="hint">Tick the boxes to close several open incidents together.</span>
            )}
            {data.total > 0 && <button className="ghost" disabled={bulkBusy} onClick={selectAllMatching}>Select all open that match the filters</button>}
            {bulkMsg.text && <span className={bulkMsg.ok ? "ok" : "err"} style={{ margin: 0 }}>{bulkMsg.text}</span>}
          </div>
        )}
        {data.items.length ? (
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  {staff && (
                    <th style={{ width: 34 }}>
                      <input type="checkbox" className="chk" aria-label="Select all open incidents on this page"
                        checked={allPageSelected} disabled={!pageOpen.length} onChange={togglePage} />
                    </th>
                  )}
                  <th>ID</th><th className="hm">Time</th><th>Violation</th><th>Severity</th><th className="hm">Camera</th><th>Status</th><th className="hm">Due</th><th className="hm">Assigned</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((i) => (
                  <tr key={i.incident_id} className="click" tabIndex={0} onClick={() => setOpen(i)} onKeyDown={(e) => e.key === "Enter" && e.target === e.currentTarget && setOpen(i)}>
                    {staff && (
                      <td onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" className="chk" aria-label={`Select ${i.incident_id}`}
                          checked={sel.includes(i.incident_id)} disabled={!isOpenInc(i)} onChange={() => toggle(i.incident_id)} />
                      </td>
                    )}
                    <td><b>{i.incident_id}</b></td><td className="hm">{fmtTime(i.timestamp)}</td>
                    <td>{pretty(i.violation)}<div className="hint mo">{i.camera_name || "-"} - {fmtTime(i.timestamp)}</div></td>
                    <td><Sev value={i.severity} /></td><td className="hm">{i.camera_name || "-"}{i.zone ? <div className="hint">{i.zone}</div> : null}</td>
                    <td><Status value={i.status} /><AiTag value={i.ai_review} />{i.overdue ? <div className="due-over mo">Overdue</div> : null}</td>
                    <td className={`hm ${i.overdue ? "due-over" : ""}`}>{fmtTime(i.due_at)}{i.overdue ? " (overdue)" : ""}</td>
                    <td className="hm">{i.assigned_to || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <Empty>No incidents match these filters.</Empty>}
        <div className="pager">
          <span>Page {page + 1} of {pages}</span>
          <div className="right">
            <button className="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
            <button className="ghost" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next</button>
          </div>
        </div>
      </section>

      {open && <Detail inc={open} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); load(); }} />}
    </>
  );
}