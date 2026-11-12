"""Daily / weekly summary report by e-mail, with the PDF attached."""
import json
import os
import threading
import time
from datetime import datetime, timedelta

from backend import alerts, config, notify
from backend.db import get_db, now_str
from backend.services import report_service
from backend.services.incident_service import pretty

STATE_FILE = os.path.join(config.BACKEND_DIR, ".report_state.json")
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
_started = False


def _load():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError as e:
        print("could not save report state:", e)


def recipients():
    if config.REPORT_EMAILS:
        return config.REPORT_EMAILS
    if config.ALERT_EMAILS:
        return config.ALERT_EMAILS
    return [u["email"] for u in notify.users_with_role("SUPERVISOR")]


def _period(period, today=None):
    today = (today or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=1 if period == "daily" else 7)
    return start, today


def _rows(sql, params):
    conn = get_db()
    out = [dict(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return out


def build(period, today=None):
    start, end = _period(period, today)
    a, b = start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")
    inc = _rows("SELECT * FROM incidents WHERE timestamp >= ? AND timestamp < ? ORDER BY id DESC", (a, b))

    def top(key, n=5):
        counts = {}
        for r in inc:
            k = r.get(key) or "Unknown"
            counts[k] = counts.get(k, 0) + 1
        return sorted(counts.items(), key=lambda x: -x[1])[:n]

    resolved = [r for r in inc if r.get("status") == "RESOLVED"]
    open_now = _rows("SELECT COUNT(*) c FROM incidents WHERE status IN ('OPEN','ASSIGNED')", ())[0]["c"]
    overdue = _rows("SELECT COUNT(*) c FROM incidents WHERE status IN ('OPEN','ASSIGNED') AND due_at IS NOT NULL AND due_at < ?",
                    (now_str(),))[0]["c"]
    avg = _rows("SELECT AVG((julianday(resolved_at)-julianday(timestamp))*1440) m FROM incidents "
                "WHERE timestamp >= ? AND timestamp < ? AND status='RESOLVED' AND resolved_at IS NOT NULL", (a, b))[0]["m"]

    label = "Daily" if period == "daily" else "Weekly"
    span = start.strftime("%d %b %Y") if period == "daily" else f"{start:%d %b} - {(end - timedelta(days=1)):%d %b %Y}"
    lines = [f"AI Sentinel - {label} safety report", f"Period: {span}", "",
             f"Violations in the period: {len(inc)}",
             f"Resolved: {len(resolved)}" + (f" (average {avg:.0f} min to resolve)" if avg is not None else ""),
             f"Still open / assigned right now: {open_now}",
             f"Overdue right now: {overdue}", ""]
    if inc:
        lines.append("By severity: " + ", ".join(f"{k.title()} {v}" for k, v in top("severity")))
        lines.append("By violation: " + ", ".join(f"{pretty(k)} {v}" for k, v in top("violation")))
        lines.append("By camera: " + ", ".join(f"{k} {v}" for k, v in top("camera_name")))
    else:
        lines.append("No violations were recorded in this period.")
    lines += ["", f"Dashboard: {config.FRONTEND_URL}", "The full list is in the attached PDF."]
    return f"[AI Sentinel] {label} report - {span}", "\n".join(lines), inc


def send_report(period):
    """Build and e-mail the report. Returns the number of recipients; raises if it cannot be sent."""
    to = recipients()
    if not to:
        raise RuntimeError("No recipients: set REPORT_EMAILS / ALERT_EMAILS in .env, or create a supervisor account")
    subject, body, rows = build(period)
    pdf = report_service.generate_pdf_report(rows, title=subject.replace("[AI Sentinel] ", "AI Sentinel - "))
    with open(pdf, "rb") as f:
        data = f.read()
    alerts.send_email_with_attachment(to, subject, body, os.path.basename(pdf), data)
    return len(to)


def _keys(now):
    keys = {"daily": now.date().isoformat()}
    if config.REPORT_WEEKLY_DAY and DAYS[now.weekday()] == config.REPORT_WEEKLY_DAY:
        y, w, _ = now.isocalendar()
        keys["weekly"] = f"{y}-W{w:02d}"
    return keys


def check_once(now=None):
    """Send whatever is due. The first ever run only records today, so nothing is sent by surprise."""
    now = now or datetime.now()
    state, sent = _load(), []
    keys = _keys(now)
    if not state:
        _save({"daily": keys["daily"], **({"weekly": keys["weekly"]} if "weekly" in keys else {})})
        return sent
    try:
        h, m = map(int, config.REPORT_DAILY_TIME.split(":"))
    except ValueError:
        return sent                     # empty / invalid time = scheduled reports are off
    if now < now.replace(hour=h, minute=m, second=0, microsecond=0):
        return sent
    for period, key in keys.items():
        if period == "weekly" and not config.REPORT_WEEKLY_DAY:
            continue
        if state.get(period) == key:
            continue
        state[period] = key             # mark first: a failure is not retried every minute
        _save(state)
        try:
            n = send_report(period)
            sent.append(period)
            print(f"{period} report sent to {n} recipient(s)")
        except Exception as e:
            print(f"{period} report failed: {e}")
    return sent


def status():
    return {"daily_time": config.REPORT_DAILY_TIME or None, "weekly_day": config.REPORT_WEEKLY_DAY or None,
            "recipients": recipients(), "email_configured": bool(config.SMTP_EMAIL and config.SMTP_PASSWORD),
            "last_sent": _load()}


def _loop():
    time.sleep(45)
    while True:
        try:
            check_once()
        except Exception as e:
            print("report scheduler failed:", e)
        time.sleep(60)


def start():
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="report-scheduler").start()