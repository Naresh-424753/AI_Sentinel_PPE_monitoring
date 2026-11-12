"""Web Push: alerts on the phone (or any browser) even when the app is closed.

Needs the `pywebpush` package. Without it the app works as before and push stays off.
The server makes its own VAPID key pair the first time it is needed and keeps it in app_settings.
A subscription is one browser or phone; an e-mail address can have several.
"""
import base64
import json
import threading
from urllib.parse import urlparse

from backend import config
from backend.db import get_db, now_str

try:
    from pywebpush import WebPushException, webpush
    AVAILABLE = True
except Exception:                      # not installed: everything below becomes a quiet no-op
    webpush = None
    WebPushException = Exception
    AVAILABLE = False

KEY = "vapid"
MAX_ENDPOINT = 1000

# Only real browser push services are accepted as endpoints, so nobody can make the server call an arbitrary address.
PUSH_HOST_SUFFIXES = (
    "fcm.googleapis.com",              # Chrome, Edge, Brave, Opera, Samsung Internet on Android
    "push.services.mozilla.com",       # Firefox
    "push.apple.com",                  # Safari / iPhone home-screen apps
    "notify.windows.com",              # Edge on Windows
)


def valid_endpoint(url):
    try:
        u = urlparse(str(url))
    except Exception:
        return False
    host = (u.hostname or "").lower()
    return (u.scheme == "https" and len(str(url)) <= MAX_ENDPOINT
            and any(host == s or host.endswith("." + s) for s in PUSH_HOST_SUFFIXES))


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# ------------------------------------------------------------------ storage
def ensure_table(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS push_subscriptions (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_email TEXT NOT NULL,
               endpoint TEXT NOT NULL UNIQUE,
               p256dh TEXT NOT NULL,
               auth TEXT NOT NULL,
               user_agent TEXT,
               created_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_push_email ON push_subscriptions(user_email)")


def get_keys():
    """(private, public) as URL-safe base64. Created once, then kept."""
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (KEY,)).fetchone()
        if row:
            try:
                data = json.loads(row["value"])
                if data.get("private") and data.get("public"):
                    return data["private"], data["public"]
            except Exception:
                pass
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        private = _b64(key.private_numbers().private_value.to_bytes(32, "big"))
        public = _b64(key.public_key().public_bytes(serialization.Encoding.X962,
                                                    serialization.PublicFormat.UncompressedPoint))
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (KEY, json.dumps({"private": private, "public": public}), now_str()))
        conn.commit()
        return private, public
    finally:
        conn.close()


def save_subscription(email, endpoint, p256dh, auth, user_agent=""):
    conn = get_db()
    try:
        ensure_table(conn)
        conn.execute(
            "INSERT INTO push_subscriptions (user_email, endpoint, p256dh, auth, user_agent, created_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET user_email=excluded.user_email, "
            "p256dh=excluded.p256dh, auth=excluded.auth, user_agent=excluded.user_agent",
            (email.strip().lower(), endpoint, p256dh, auth, (user_agent or "")[:200], now_str()))
        conn.commit()
    finally:
        conn.close()


def remove_subscription(endpoint, email=None):
    conn = get_db()
    try:
        ensure_table(conn)
        if email:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint=? AND LOWER(user_email)=?",
                         (endpoint, email.strip().lower()))
        else:
            conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        conn.commit()
    finally:
        conn.close()


def count_for(email):
    conn = get_db()
    try:
        ensure_table(conn)
        return conn.execute("SELECT COUNT(*) FROM push_subscriptions WHERE LOWER(user_email)=?",
                            (email.strip().lower(),)).fetchone()[0]
    finally:
        conn.close()


def _subscriptions(emails):
    emails = sorted({(e or "").strip().lower() for e in emails if e})
    if not emails:
        return []
    conn = get_db()
    try:
        ensure_table(conn)
        marks = ",".join("?" * len(emails))
        return [dict(r) for r in conn.execute(
            f"SELECT endpoint, p256dh, auth FROM push_subscriptions WHERE LOWER(user_email) IN ({marks})",
            emails).fetchall()]
    finally:
        conn.close()


# ------------------------------------------------------------------ sending
def send_to_emails(emails, title, body, url="/incidents", tag=None, severity=None):
    """Send now, in this thread. Returns how many devices accepted the message."""
    if not AVAILABLE:
        return 0
    subs = _subscriptions(emails)
    if not subs:
        return 0
    private, _ = get_keys()
    subject = "mailto:" + (config.SMTP_EMAIL or "admin@example.com")
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag, "severity": severity})
    sent = 0
    for s in subs:
        try:
            webpush(subscription_info={"endpoint": s["endpoint"], "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}},
                    data=payload, vapid_private_key=private, vapid_claims={"sub": subject}, ttl=3600)
            sent += 1
        except WebPushException as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (404, 410):           # the phone uninstalled the app or revoked permission
                remove_subscription(s["endpoint"])
            else:
                print("push failed:", e)
        except Exception as e:
            print("push failed:", e)
    return sent


def send_async(emails, title, body, url="/incidents", tag=None, severity=None):
    """Fire and forget. Never raises and never blocks the caller."""
    if not AVAILABLE:
        return
    threading.Thread(target=_safe_send, args=(emails, title, body, url, tag, severity), daemon=True).start()


def _safe_send(*args):
    try:
        send_to_emails(*args)
    except Exception as e:
        print("push error:", e)


def push_incident(inc):
    """A new incident goes to every active Supervisor and above who has a subscribed device."""
    if not AVAILABLE:
        return 0
    from backend import notify
    from backend.services.incident_service import pretty
    emails = [u["email"] for u in notify.users_with_role("SUPERVISOR") if u.get("email")]
    where = inc.get("camera_name") or "camera"
    if inc.get("zone"):
        where += f" ({inc['zone']})"
    return send_to_emails(
        emails,
        f"{inc.get('severity', 'MEDIUM')}: {pretty(inc.get('violation'))}",
        f"{where} - {inc.get('incident_id', '')}",
        url=f"/incidents?open={inc.get('incident_id', '')}" if inc.get("incident_id") else "/incidents",
        tag=inc.get("incident_id"), severity=inc.get("severity"))