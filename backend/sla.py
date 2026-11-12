"""Deadline watcher: an open incident that passes its deadline is escalated once
(supervisors, admins and the assignee are notified, and the alert channels fire)."""
import threading
import time

from backend import alerts, config, notify
from backend.db import get_db, log_audit, now_str
from backend.events import bus
from backend.services.incident_service import pretty

_started = False


def _escalate(inc):
    who = inc.get("assigned_to") or "nobody"
    text = (f"OVERDUE: {inc['incident_id']} ({pretty(inc['violation'])}, {inc.get('camera_name') or 'camera'}) "
            f"was due at {inc.get('due_at')} and is still {str(inc['status']).lower()}. Assigned to: {who}.")
    emails = [u["email"] for u in notify.find_users(inc.get("assigned_to"))]
    emails += [u["email"] for u in notify.users_with_role("SUPERVISOR")]
    notify.notify(emails, "Incident overdue", text, inc["incident_id"], email=True)
    alerts.dispatch({**inc, "severity": "CRITICAL", "_text": text,
                     "_subject": f"[AI Sentinel] OVERDUE {inc['incident_id']}"}, force=True)
    log_audit("system", "INCIDENT_OVERDUE", "incident", inc["incident_id"], f"due {inc.get('due_at')}")
    bus.publish({"type": "incident_updated", "incident_id": inc["incident_id"]})


def check_once():
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM incidents WHERE status IN ('OPEN','ASSIGNED') AND due_at IS NOT NULL AND due_at < ? "
        "AND COALESCE(overdue_alerted,0)=0 ORDER BY due_at LIMIT 50", (now_str(),)).fetchall()]
    for r in rows:
        conn.execute("UPDATE incidents SET overdue_alerted=1 WHERE incident_id=?", (r["incident_id"],))
        conn.execute(
            "INSERT INTO incident_comments (incident_id, author, author_email, message, created_at) VALUES (?,?,?,?,?)",
            (r["incident_id"], "System", "", "Overdue: not resolved before the deadline", now_str()))
    conn.commit()
    conn.close()
    for r in rows:
        try:
            _escalate(r)
        except Exception as e:
            print("escalation failed:", e)
    return len(rows)


def _loop():
    while True:
        try:
            check_once()
        except Exception as e:
            print("deadline check failed:", e)
        time.sleep(max(10, config.SLA_CHECK_SECONDS))


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="deadline-watcher").start()