"""Incident list, filters, assignment, replies, workflow and exports."""

import base64
import os
import re
import secrets
from typing import List, Optional

import cv2
import numpy as np

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend import config, incident_report, notify
from backend.db import get_db, log_audit, now_str
from backend.events import bus
from backend.security import get_current_user, rank, require_role
from backend.services import report_service
from backend.services.incident_service import pretty


router = APIRouter(tags=["incidents"])

STATUSES = ["OPEN", "ASSIGNED", "RESOLVED", "FALSE_POSITIVE"]
CLOSED = ("RESOLVED", "FALSE_POSITIVE")


# ============================================================
# HELPERS
# ============================================================

def user_name(user):
    return user.get("name") or user.get("email") or "User"


def user_email(user):
    return str(user.get("email") or "").strip()


def is_staff(user):
    return rank(str(user.get("role") or "").upper()) >= rank("SUPERVISOR")


def with_overdue(row):
    """Add an 'overdue' flag (OPEN/ASSIGNED and past due_at)."""
    status = str(row.get("status") or "").upper()
    due_at = row.get("due_at")
    row["overdue"] = bool(status in ("OPEN", "ASSIGNED") and due_at and str(due_at) < now_str())
    return row


def is_assigned_to_user(row, user):
    """assigned_to may hold the user's name or e-mail."""
    assigned = str(row["assigned_to"] or "").strip().lower()
    if not assigned:
        return False
    email = str(user.get("email") or "").strip().lower()
    name = str(user.get("name") or "").strip().lower()
    return assigned in {email, name}


def get_row(conn, incident_id):
    row = conn.execute("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Incident not found")
    return row


def add_system_comment(conn, incident_id, message):
    conn.execute(
        "INSERT INTO incident_comments (incident_id, author, author_email, message, created_at) VALUES (?,?,?,?,?)",
        (incident_id, "System", "", message, now_str()),
    )


def remove_file(folder, filename):
    if not filename:
        return
    path = os.path.join(folder, os.path.basename(filename))
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError as exc:
            print("file delete error:", exc)


# ============================================================
# FILTERS
# ============================================================

def build_filters(status=None, violation=None, severity=None, camera_id=None, q=None,
                  date_from=None, date_to=None, assigned_to=None, overdue=None, review=None):
    where, params = [], []

    if status:
        where.append("status = ?")
        params.append(status.upper().strip())
    if violation:
        where.append("violation = ?")
        params.append(violation.strip())
    if severity:
        where.append("severity = ?")
        params.append(severity.upper().strip())
    if camera_id is not None:
        where.append("camera_id = ?")
        params.append(camera_id)
    if assigned_to:
        where.append("LOWER(assigned_to) = LOWER(?)")
        params.append(assigned_to.strip())
    if overdue:
        where.append("(status IN ('OPEN','ASSIGNED') AND due_at IS NOT NULL AND due_at < ?)")
        params.append(now_str())

    if review:
        r = review.lower().strip()
        if r in ("needs", "needs_review"):
            where.append("(status IN ('OPEN','ASSIGNED') AND ai_review IN ('NEEDS_REVIEW','LOW_CONFIDENCE'))")
        elif r in ("auto", "auto_resolved"):
            where.append("ai_review = 'AUTO_RESOLVED'")
        elif r == "reopened":
            where.append("ai_review = 'REOPENED'")
        elif r == "low_confidence":
            where.append("ai_review = 'LOW_CONFIDENCE'")

    if date_from:
        where.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        where.append("timestamp <= ?")
        params.append(date_to + " 23:59:59")
    if q:
        like = f"%{q.strip()}%"
        where.append("(incident_id LIKE ? OR violation LIKE ? OR camera_name LIKE ? "
                     "OR assigned_to LIKE ? OR zone LIKE ? OR description LIKE ?)")
        params.extend([like] * 6)

    if not where:
        return "", params
    return " WHERE " + " AND ".join(where), params


def fetch(where, params, limit=None, offset=0):
    conn = get_db()
    try:
        sql = f"SELECT * FROM incidents {where} ORDER BY id DESC"
        args = list(params)
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            args.extend([int(limit), int(offset)])
        rows = [with_overdue(dict(r)) for r in conn.execute(sql, args).fetchall()]
        total = conn.execute(f"SELECT COUNT(*) FROM incidents {where}", params).fetchone()[0]
        return rows, total
    finally:
        conn.close()


# ============================================================
# PROOF IMAGE
# ============================================================

def save_proof(incident_id, data):
    """Accepts raw base64 or a data URL. Saves a resized JPEG in PROOFS_DIR."""
    if not data:
        return None
    if "," in data[:200]:
        data = data.split(",", 1)[1]

    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image data")

    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image is too large (max 8 MB)")

    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Uploaded data is not a valid image")

    height, width = image.shape[:2]
    if not width or not height:
        raise HTTPException(status_code=400, detail="Invalid image dimensions")

    scale = 1280 / max(width, height)
    if scale < 1:
        image = cv2.resize(image, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)

    os.makedirs(config.PROOFS_DIR, exist_ok=True)
    filename = f"{incident_id}_{secrets.token_hex(6)}.jpg"
    path = os.path.join(config.PROOFS_DIR, filename)

    if not cv2.imwrite(path, image, [cv2.IMWRITE_JPEG_QUALITY, 85]):
        raise HTTPException(status_code=500, detail="Failed to save proof image")
    return filename


# ============================================================
# NOTIFICATIONS (never break the request)
# ============================================================

def _supervisor_emails():
    return [u.get("email") for u in notify.users_with_role("SUPERVISOR") if u.get("email")]


def notify_assignment(incident_id, row, assigned_to, by_user):
    if not assigned_to:
        return
    try:
        targets = [u.get("email") for u in notify.find_users(assigned_to) if u.get("email")]
        if not targets:
            return
        notify.notify(
            targets,
            f"Assigned to you: {incident_id}",
            f"{incident_id} ({pretty(row['violation'])} on {row.get('camera_name') or 'camera'}) "
            f"was assigned to you by {user_name(by_user)}. Deadline: {row.get('due_at') or '-'}.",
            incident_id,
            email=True,
            exclude=user_email(by_user),
        )
    except Exception as exc:
        print("assignment notification error:", exc)


def notify_resolution(incident_id, row, user):
    try:
        assigned_by = row.get("assigned_by")
        targets = [assigned_by] if assigned_by else _supervisor_emails()
        targets = [t for t in targets if t]
        if not targets:
            return
        notify.notify(
            targets,
            f"Resolved: {incident_id}",
            f"{incident_id} ({pretty(row['violation'])} on {row.get('camera_name') or 'camera'}) "
            f"was marked resolved by {user_name(user)}.",
            incident_id,
            email=True,
            exclude=user_email(user),
        )
    except Exception as exc:
        print("resolution notification error:", exc)


def notify_reply(incident_id, user, text, assigned_to, assigned_by):
    """Assignee reply -> notify the assigner. Staff reply -> notify the assignee."""
    try:
        if not assigned_to:
            return
        assignee_emails = [u.get("email") for u in notify.find_users(assigned_to) if u.get("email")]
        me = user_email(user).lower()

        if me in {e.lower() for e in assignee_emails}:
            targets = [assigned_by] if assigned_by else _supervisor_emails()
        else:
            targets = assignee_emails

        targets = [t for t in targets if t and t.lower() != me]
        if not targets:
            return
        notify.notify(
            targets,
            f"New reply on {incident_id}",
            f"{user_name(user)}: {text[:140]}",
            incident_id,
            email=True,
            exclude=user_email(user),
        )
    except Exception as exc:
        print("reply notification error:", exc)


# ============================================================
# MODELS
# ============================================================

class IncidentPatch(BaseModel):
    status: Optional[str] = None
    assigned_to: Optional[str] = None
    resolution_notes: Optional[str] = None


class UserIncidentReply(BaseModel):
    resolution_notes: str = Field(..., min_length=1, max_length=2000)


class CommentBody(BaseModel):
    message: str = ""
    image: Optional[str] = None


# ============================================================
# LIST / GET
# ============================================================

@router.get("/api/incidents")
def list_incidents(
    status: Optional[str] = None,
    violation: Optional[str] = None,
    severity: Optional[str] = None,
    camera_id: Optional[int] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    assigned_to: Optional[str] = None,
    overdue: Optional[bool] = None,
    review: Optional[str] = None,
    limit: int = Query(25, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user=Depends(get_current_user),
):
    where, params = build_filters(status, violation, severity, camera_id, q,
                                  date_from, date_to, assigned_to, overdue, review)
    items, total = fetch(where, params, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get("/incidents")
def legacy_list(user=Depends(get_current_user)):
    items, _ = fetch("", [], 1000, 0)
    return items


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: str, user=Depends(get_current_user)):
    conn = get_db()
    try:
        return with_overdue(dict(get_row(conn, incident_id)))
    finally:
        conn.close()


# ============================================================
# LEGACY REPLY (assigned user only)
# ============================================================

@router.patch("/incidents/{incident_id}/reply")
def user_reply_incident(incident_id: str, body: UserIncidentReply, user=Depends(get_current_user)):
    reply = (body.resolution_notes or "").strip()
    if not reply:
        raise HTTPException(status_code=400, detail="Reply cannot be empty")

    conn = get_db()
    try:
        row = get_row(conn, incident_id)

        if not is_assigned_to_user(row, user):
            raise HTTPException(status_code=403, detail="You are not assigned to this incident")
        if str(row["status"] or "").upper() in CLOSED:
            raise HTTPException(status_code=400, detail="This incident is already closed")

        ts = now_str()
        conn.execute("UPDATE incidents SET resolution_notes=?, updated_at=? WHERE incident_id=?",
                     (reply, ts, incident_id))
        conn.execute(
            "INSERT INTO incident_comments (incident_id, author, author_email, message, created_at) "
            "VALUES (?,?,?,?,?)",
            (incident_id, user_name(user), user_email(user), reply, ts),
        )
        conn.commit()
        out = with_overdue(dict(get_row(conn, incident_id)))
    finally:
        conn.close()

    log_audit(user_email(user), "INCIDENT_REPLY", "incident", incident_id, reply[:80])
    bus.publish({"type": "incident_updated", "incident_id": incident_id})
    bus.publish({"type": "comment", "incident_id": incident_id})
    notify_reply(incident_id, user, reply, row["assigned_to"], row["assigned_by"])

    return {"success": True, "message": "Reply submitted successfully", "incident": out}


# ============================================================
# UPDATE (status / assign / notes)
# ============================================================

@router.patch("/incidents/{incident_id}")
def update_incident(incident_id: str, body: IncidentPatch, user=Depends(get_current_user)):
    conn = get_db()
    try:
        row = get_row(conn, incident_id)
        staff = is_staff(user)
        old_status = str(row["status"] or "").upper()
        old_assigned = str(row["assigned_to"] or "").strip()

        # ---- permissions -------------------------------------------------
        if not staff:
            if not is_assigned_to_user(row, user):
                raise HTTPException(status_code=403,
                                    detail="Only the assigned person or a supervisor can update this incident")
            if old_status in CLOSED:
                raise HTTPException(status_code=400, detail="This incident is already closed")
            if body.assigned_to is not None:
                raise HTTPException(status_code=403, detail="Only a supervisor can reassign an incident")
            if body.status is not None and body.status.upper().strip() != "RESOLVED":
                raise HTTPException(status_code=403, detail="You can only mark your incident as resolved")

        # ---- build the changes (only real changes) ------------------------
        fields = {}

        if body.status is not None:
            status = body.status.upper().strip()
            if status not in STATUSES:
                raise HTTPException(status_code=400, detail="Status must be one of " + ", ".join(STATUSES))
            if status != old_status:
                fields["status"] = status

        new_assigned = old_assigned
        if body.assigned_to is not None:
            new_assigned = (body.assigned_to or "").strip()
            if new_assigned != old_assigned:
                fields["assigned_to"] = new_assigned or None
                fields["assigned_by"] = user_email(user) if new_assigned else None

                if "status" not in fields:
                    if new_assigned and old_status == "OPEN":
                        fields["status"] = "ASSIGNED"
                    elif not new_assigned and old_status == "ASSIGNED":
                        fields["status"] = "OPEN"

        if body.resolution_notes is not None:
            notes = (body.resolution_notes or "").strip()
            if notes != str(row["resolution_notes"] or "").strip():
                fields["resolution_notes"] = notes

        # ---- nothing changed: return the row as it is ---------------------
        if not fields:
            return with_overdue(dict(row))

        new_status = fields.get("status", old_status)
        status_changed = "status" in fields

        if status_changed:
            fields["resolved_at"] = now_str() if new_status in CLOSED else None

        reopened = (
            status_changed
            and str(row["ai_review"] or "").upper() == "AUTO_RESOLVED"
            and new_status in ("OPEN", "ASSIGNED")
        )
        if reopened:
            fields["ai_review"] = "REOPENED"

        fields["updated_at"] = now_str()

        # ---- write --------------------------------------------------------
        cols = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE incidents SET {cols} WHERE incident_id=?", [*fields.values(), incident_id])

        # ---- system comments (real changes only) --------------------------
        actor = user_name(user)
        assignment_changed = "assigned_to" in fields

        if reopened:
            add_system_comment(conn, incident_id, f"Reopened by {actor} (the AI had marked it solved)")
        if assignment_changed:
            add_system_comment(conn, incident_id, f"Assigned to {new_assigned or 'Nobody'} by {actor}")
        if status_changed and new_status == "RESOLVED":
            add_system_comment(conn, incident_id, f"Marked resolved by {actor}")
        if status_changed and new_status == "FALSE_POSITIVE":
            add_system_comment(conn, incident_id, f"Marked as false alarm by {actor}")

        conn.commit()
        out = with_overdue(dict(get_row(conn, incident_id)))
    finally:
        conn.close()

    detail = ", ".join(f"{k}={v}" for k, v in fields.items() if k not in ("updated_at", "resolved_at"))
    log_audit(user_email(user), "INCIDENT_UPDATE", "incident", incident_id, detail)
    bus.publish({"type": "incident_updated", "incident_id": incident_id})

    if assignment_changed and new_assigned:
        notify_assignment(incident_id, out, new_assigned, user)
    if status_changed and new_status == "RESOLVED":
        notify_resolution(incident_id, out, user)

    return out


# ============================================================
# COMMENTS
# ============================================================

@router.get("/incidents/{incident_id}/comments")
def list_comments(incident_id: str, user=Depends(get_current_user)):
    conn = get_db()
    try:
        if not conn.execute("SELECT 1 FROM incidents WHERE incident_id=?", (incident_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Incident not found")
        rows = conn.execute("SELECT * FROM incident_comments WHERE incident_id=? ORDER BY id ASC",
                            (incident_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@router.post("/incidents/{incident_id}/comments")
def add_comment(incident_id: str, body: CommentBody, user=Depends(get_current_user)):
    message = (body.message or "").strip()

    if not message and not body.image:
        raise HTTPException(status_code=400, detail="Write a message or attach a photo")
    if len(message) > 1000:
        raise HTTPException(status_code=400, detail="Message is too long (max 1000 characters)")

    attachment = None
    conn = get_db()
    try:
        row = get_row(conn, incident_id)

        if not (is_staff(user) or is_assigned_to_user(row, user)):
            raise HTTPException(status_code=403, detail="Only the assigned person or a supervisor can reply")

        if not is_staff(user) and str(row["status"] or "").upper() in CLOSED:
            raise HTTPException(status_code=400, detail="This incident is already closed")

        if body.image:
            attachment = save_proof(incident_id, body.image)

        stored = message or "[Photo]"
        ts = now_str()

        try:
            conn.execute(
                "INSERT INTO incident_comments (incident_id, author, author_email, message, attachment, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (incident_id, user_name(user), user_email(user), stored, attachment, ts),
            )
            conn.execute("UPDATE incidents SET updated_at=? WHERE incident_id=?", (ts, incident_id))
            conn.commit()
        except Exception:
            conn.rollback()
            remove_file(config.PROOFS_DIR, attachment)   # don't leave an orphan photo
            raise
    finally:
        conn.close()

    log_audit(user_email(user), "INCIDENT_REPLY", "incident", incident_id, stored[:80])
    bus.publish({"type": "comment", "incident_id": incident_id})
    bus.publish({"type": "incident_updated", "incident_id": incident_id})
    notify_reply(incident_id, user, message or "sent a photo", row["assigned_to"], row["assigned_by"])

    return {"success": True, "message": "Comment added successfully", "attachment": attachment}


# ============================================================
# DELETE (ADMIN)
# ============================================================

@router.delete("/incidents/{incident_id}")
def delete_incident(incident_id: str, user=Depends(require_role("ADMIN"))):
    conn = get_db()
    try:
        row = get_row(conn, incident_id)
        screenshot, clip = row["screenshot"], row["clip"]

        proofs = [
            r["attachment"]
            for r in conn.execute(
                "SELECT attachment FROM incident_comments WHERE incident_id=? AND attachment IS NOT NULL",
                (incident_id,),
            ).fetchall()
            if r["attachment"]
        ]

        conn.execute("DELETE FROM incident_comments WHERE incident_id=?", (incident_id,))
        conn.execute("DELETE FROM incidents WHERE incident_id=?", (incident_id,))
        conn.commit()
    finally:
        conn.close()

    remove_file(config.INCIDENTS_DIR, screenshot)
    remove_file(config.CLIPS_DIR, clip)
    for p in proofs:
        remove_file(config.PROOFS_DIR, p)
    remove_file(config.TRAINING_DIR, f"{incident_id}.jpg")
    remove_file(config.TRAINING_DIR, f"{incident_id}.txt")

    log_audit(user_email(user), "INCIDENT_DELETE", "incident", incident_id)
    bus.publish({"type": "incident_deleted", "incident_id": incident_id})

    return {"success": True, "message": f"{incident_id} deleted"}


# ============================================================
# BULK CLOSE (select several incidents, close them together)
# ============================================================

class BulkBody(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=500)
    status: str = "RESOLVED"
    note: str = Field("", max_length=1000)


@router.post("/api/incidents/bulk")
def bulk_close(body: BulkBody, user=Depends(require_role("SUPERVISOR"))):
    """Close the chosen OPEN / ASSIGNED incidents as RESOLVED or FALSE_POSITIVE.
    Incidents that are already closed or do not exist are skipped and counted."""
    status = body.status.upper().strip()
    if status not in CLOSED:
        raise HTTPException(status_code=400, detail="A bulk action can only set RESOLVED or FALSE_POSITIVE")

    ids = list(dict.fromkeys(i.strip() for i in body.ids if i and i.strip()))
    note = body.note.strip()
    actor = user_name(user)
    label = "Marked resolved" if status == "RESOLVED" else "Marked as false alarm"
    ts = now_str()
    done, skipped, missing = [], [], []

    conn = get_db()
    try:
        for iid in ids:
            row = conn.execute("SELECT status FROM incidents WHERE incident_id=?", (iid,)).fetchone()
            if not row:
                missing.append(iid)
                continue
            if str(row["status"] or "").upper() not in ("OPEN", "ASSIGNED"):
                skipped.append(iid)
                continue
            conn.execute(
                "UPDATE incidents SET status=?, resolved_at=?, updated_at=?, "
                "resolution_notes=CASE WHEN ?<>'' THEN ? ELSE resolution_notes END WHERE incident_id=?",
                (status, ts, ts, note, note, iid))
            add_system_comment(conn, iid, f"{label} by {actor} (bulk action)" + (f": {note}" if note else ""))
            done.append(iid)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    for iid in done:
        log_audit(user_email(user), "INCIDENT_BULK_UPDATE", "incident", iid, f"status={status}")
    if done:
        bus.publish({"type": "incident_updated", "incident_id": done[0]})   # makes open dashboards refresh

    return {"success": True, "status": status, "updated": len(done),
            "skipped_already_closed": len(skipped), "not_found": len(missing)}


# ============================================================
# INCIDENT REPORT (one incident)
# ============================================================

def _report_data(incident_id):
    conn = get_db()
    try:
        inc = with_overdue(dict(get_row(conn, incident_id)))
        comments = [dict(r) for r in conn.execute(
            "SELECT * FROM incident_comments WHERE incident_id=? ORDER BY id", (incident_id,)).fetchall()]
        return inc, comments
    finally:
        conn.close()


@router.get("/api/incidents/{incident_id}/report.md")
def report_markdown(incident_id: str, user=Depends(get_current_user)):
    inc, comments = _report_data(incident_id)
    log_audit(user_email(user), "REPORT_DOWNLOAD", "incident", incident_id, "md")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", incident_id)
    return Response(
        incident_report.build_markdown(inc, comments).encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe}_report.md"'},
    )


@router.get("/api/incidents/{incident_id}/report.html")
def report_html(incident_id: str, user=Depends(get_current_user)):
    """Printable page. Use the browser's Print > Save as PDF."""
    inc, comments = _report_data(incident_id)
    log_audit(user_email(user), "REPORT_VIEW", "incident", incident_id, "html")
    return Response(incident_report.build_html(inc, comments).encode("utf-8"),
                    media_type="text/html; charset=utf-8")


# ============================================================
# EXPORTS
# ============================================================

@router.get("/api/incidents/export")
def export_incidents(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    status: Optional[str] = None,
    violation: Optional[str] = None,
    severity: Optional[str] = None,
    camera_id: Optional[int] = None,
    q: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    assigned_to: Optional[str] = None,
    overdue: Optional[bool] = None,
    review: Optional[str] = None,
    user=Depends(get_current_user),
):
    where, params = build_filters(status, violation, severity, camera_id, q,
                                  date_from, date_to, assigned_to, overdue, review)
    rows, _ = fetch(where, params, 5000, 0)

    log_audit(user_email(user), "EXPORT", "incidents", format, f"{len(rows)} rows")

    if format == "csv":
        return Response(report_service.to_csv(rows), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="incidents.csv"'})

    if format == "xlsx":
        return Response(
            report_service.to_xlsx(rows),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="incidents.xlsx"'},
        )

    path = report_service.generate_pdf_report(rows)
    return FileResponse(path, media_type="application/pdf", filename="Safety_Audit_Report.pdf")


@router.get("/api/export-pdf")
def export_pdf(user=Depends(get_current_user)):
    rows, _ = fetch("", [], 200, 0)
    path = report_service.generate_pdf_report(rows)
    log_audit(user_email(user), "EXPORT", "incidents", "pdf", f"{len(rows)} rows")
    return FileResponse(path, media_type="application/pdf", filename="Safety_Audit_Report.pdf")