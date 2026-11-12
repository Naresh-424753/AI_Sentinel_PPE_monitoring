"""What the AI decides on its own: close an incident when the worker is compliant again,
or hand it to a human with the reason ("I could not tell") and what to do next."""
from backend import notify
from backend.db import get_db, log_audit, now_str
from backend.events import bus

OPEN_STATES = ("OPEN", "ASSIGNED")


def counterparts(violation):
    """Class names that mean 'this person IS wearing it' for a violation class."""
    base = violation[3:] if violation.startswith("no_") else violation
    names = {base, base + "s", base.rstrip("s"), "safety_" + base}
    if base == "vest":
        names |= {"safety_vest", "hi_vis_vest", "vest"}
    if base in ("goggle", "goggles"):
        names |= {"goggle", "goggles", "glasses", "safety_glasses"}
    return names


def near(violation_box, other_box, margin=0.6):
    """Is the centre of other_box inside violation_box, enlarged by `margin` of its size on every side?"""
    x1, y1, x2, y2 = violation_box
    w, h = x2 - x1, y2 - y1
    cx, cy = (other_box[0] + other_box[2]) / 2, (other_box[1] + other_box[3]) / 2
    return x1 - w * margin <= cx <= x2 + w * margin and y1 - h * margin <= cy <= y2 + h * margin


def _comment(conn, code, text):
    conn.execute(
        "INSERT INTO incident_comments (incident_id, author, author_email, message, created_at) VALUES (?,?,?,?,?)",
        (code, "AI", "", text, now_str()))


def _people(row):
    emails = [u["email"] for u in notify.find_users(row["assigned_to"])]
    if row["assigned_by"]:
        emails.append(row["assigned_by"])
    return emails


def _open_row(conn, code):
    row = conn.execute("SELECT * FROM incidents WHERE incident_id=?", (code,)).fetchone()
    return row if row and str(row["status"]).upper() in OPEN_STATES else None


def auto_resolve(code, note):
    """Mark solved. Never touches an incident a human has already closed."""
    conn = get_db()
    row = _open_row(conn, code)
    if not row:
        conn.close()
        return False
    ts = now_str()
    conn.execute(
        "UPDATE incidents SET status='RESOLVED', resolved_at=?, ai_review='AUTO_RESOLVED', ai_note=?, "
        "resolution_notes=COALESCE(NULLIF(resolution_notes,''), ?), updated_at=? WHERE incident_id=?",
        (ts, note, "Solved automatically by AI: " + note, ts, code))
    _comment(conn, code, "Solved automatically: " + note)
    conn.commit()
    conn.close()
    log_audit("AI", "INCIDENT_AUTO_RESOLVED", "incident", code, note)
    notify.notify(_people(row), f"Solved by AI: {code}",
                  f"{code} was marked solved automatically: {note} Reopen it if this is wrong.", code)
    bus.publish({"type": "incident_updated", "incident_id": code})
    return True


def needs_review(code, reason):
    """The violation stopped but the AI cannot prove it is fixed: a human must decide."""
    conn = get_db()
    row = _open_row(conn, code)
    if not row:
        conn.close()
        return False
    conn.execute("UPDATE incidents SET ai_review='NEEDS_REVIEW', ai_note=?, updated_at=? WHERE incident_id=?",
                 (reason, now_str(), code))
    _comment(conn, code, "I could not decide: " + reason)
    conn.commit()
    conn.close()
    notify.notify([u["email"] for u in notify.find_users(row["assigned_to"])], f"AI needs your decision: {code}",
                  f"{code}: {reason}", code)
    bus.publish({"type": "incident_updated", "incident_id": code})
    return True


def flag_low_confidence(code, conf):
    conn = get_db()
    conn.execute("UPDATE incidents SET ai_review='LOW_CONFIDENCE', ai_note=?, updated_at=? WHERE incident_id=?",
                 (f"The AI is only {conf * 100:.0f}% sure about this detection. Check the screenshot before acting.",
                  now_str(), code))
    conn.commit()
    conn.close()