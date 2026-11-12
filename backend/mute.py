"""Quiet hours: while a mute window is active, outgoing alerts (Telegram, e-mail, WhatsApp, webhook) are not sent.

Incidents are still detected, saved and shown on the dashboard. Muted alerts are dropped, not sent later.
Settings live in the app_settings table (key "alert_mute") and are edited in Admin > Quiet hours.
Times are the server's local time.
"""
import json
from datetime import datetime, timedelta

from backend.db import get_db, now_str

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
KEY = "alert_mute"
MAX_WINDOWS = 10


def default_settings():
    return {"enabled": False, "allow_critical": True, "windows": []}


def _minutes(text):
    """'13:30' -> 810. Raises ValueError for anything else."""
    parts = str(text).strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"Time must look like 13:30 (got '{text}')")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Time must be between 00:00 and 23:59 (got '{text}')")
    return h * 60 + m


def sanitize(data):
    """Validate and normalise settings. Raises ValueError with a message the user can read."""
    windows = data.get("windows") or []
    if len(windows) > MAX_WINDOWS:
        raise ValueError(f"At most {MAX_WINDOWS} quiet-hour windows are allowed")
    clean = []
    for w in windows:
        start, end = _minutes(w.get("start")), _minutes(w.get("end"))
        if start == end:
            raise ValueError("A window cannot start and end at the same time")
        days = [str(d).upper()[:3] for d in (w.get("days") or [])]
        bad = [d for d in days if d not in DAYS]
        if bad:
            raise ValueError("Unknown day: " + ", ".join(bad))
        if not days:
            raise ValueError("Pick at least one day for each window")
        clean.append({
            "start": f"{start // 60:02d}:{start % 60:02d}",
            "end": f"{end // 60:02d}:{end % 60:02d}",
            "days": [d for d in DAYS if d in days],
        })
    return {"enabled": bool(data.get("enabled")), "allow_critical": bool(data.get("allow_critical", True)),
            "windows": clean}


def get_settings():
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (KEY,)).fetchone()
        if not row:
            return default_settings()
        return {**default_settings(), **json.loads(row["value"])}
    except Exception as e:
        print("could not read quiet-hour settings:", e)
        return default_settings()
    finally:
        conn.close()


def save_settings(data):
    clean = sanitize(data)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (KEY, json.dumps(clean), now_str()))
        conn.commit()
    finally:
        conn.close()
    return clean


def in_window(window, at):
    """Is `at` inside one window? A window like 22:00-06:00 runs overnight and belongs to the day it starts."""
    start, end = _minutes(window["start"]), _minutes(window["end"])
    t = at.hour * 60 + at.minute
    today = DAYS[at.weekday()]
    yesterday = DAYS[(at.weekday() - 1) % 7]
    if start < end:
        return today in window["days"] and start <= t < end
    return (today in window["days"] and t >= start) or (yesterday in window["days"] and t < end)


def is_muted(severity=None, at=None, settings=None):
    """True when an outgoing alert for this severity should be held back right now."""
    s = settings if settings is not None else get_settings()
    if not s.get("enabled") or not s.get("windows"):
        return False
    if s.get("allow_critical", True) and str(severity or "").upper() == "CRITICAL":
        return False
    at = at or datetime.now()
    try:
        return any(in_window(w, at) for w in s["windows"])
    except Exception as e:
        print("quiet-hour check failed:", e)
        return False


def next_change(at=None, settings=None):
    """Minutes until the mute state flips (for the Admin page), or None when it never will."""
    s = settings if settings is not None else get_settings()
    if not s.get("enabled") or not s.get("windows"):
        return None
    at = at or datetime.now()
    now_state = any(in_window(w, at) for w in s["windows"])
    probe = at.replace(second=0, microsecond=0)
    for i in range(1, 7 * 24 * 60 + 1):
        probe += timedelta(minutes=1)
        if any(in_window(w, probe) for w in s["windows"]) != now_state:
            return i
    return None