"""In-app notifications (the bell in the dashboard) with optional e-mail to the person concerned."""
from backend import alerts, config
from backend.db import get_db, now_str
from backend.events import bus
from backend.security import ROLE_RANK


def find_users(identifier):
    """assigned_to holds a name or an e-mail; return the matching active users."""
    ident = (identifier or "").strip().lower()
    if not ident:
        return []
    conn = get_db()
    rows = conn.execute(
        "SELECT id, name, email FROM users WHERE status='ACTIVE' AND (LOWER(email)=? OR LOWER(name)=?)",
        (ident, ident)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def users_with_role(min_role):
    need = ROLE_RANK[min_role]
    conn = get_db()
    rows = conn.execute("SELECT id, name, email, role FROM users WHERE status='ACTIVE'").fetchall()
    conn.close()
    return [dict(r) for r in rows if ROLE_RANK.get(str(r["role"]).upper(), 1) >= need]


def notify(emails, title, message, incident_id=None, email=False, exclude=None):
    """Save a notification for each e-mail address, push it live, and optionally e-mail it."""
    exclude = (exclude or "").strip().lower()
    seen, targets = set(), []
    for e in emails:
        e = (e or "").strip().lower()
        if e and e not in seen and e != exclude:
            seen.add(e)
            targets.append(e)
    if not targets:
        return
    conn = get_db()
    for e in targets:
        conn.execute(
            "INSERT INTO notifications (user_email, title, message, incident_id, created_at, is_read) VALUES (?,?,?,?,?,0)",
            (e, title, message, incident_id, now_str()))
    conn.commit()
    conn.close()
    try:
        from backend import push
        push.send_async(targets, title, message,
                        url=f"/incidents?open={incident_id}" if incident_id else "/incidents",
                        tag=f"n-{incident_id}" if incident_id else None)
    except Exception as e:
        print("push notification error:", e)
    for e in targets:
        bus.publish({"type": "notification", "to": e, "title": title, "message": message, "incident_id": incident_id})
        domain = e.rsplit("@", 1)[-1]
        if email and config.NOTIFY_EMAIL and domain not in config.NOTIFY_EMAIL_EXCLUDE_DOMAINS:
            alerts.email_user(e, f"[AI Sentinel] {title}", f"{message}\n\nOpen: {config.FRONTEND_URL}/incidents")