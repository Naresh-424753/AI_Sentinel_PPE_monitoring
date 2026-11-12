"""SQLite access, schema creation and non-destructive migrations.

Existing databases keep their data. New columns/tables are added by migrations,
nothing is deleted.
"""
import sqlite3
import threading
from datetime import datetime, timedelta

from backend import config
from backend.services import incident_service

_insert_lock = threading.Lock()


# ============================================================
# BASICS
# ============================================================

def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_db():
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# SCHEMA
# ============================================================

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS incidents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_id TEXT UNIQUE,
        violation TEXT NOT NULL,
        confidence REAL,
        timestamp TEXT,
        screenshot TEXT,
        status TEXT DEFAULT 'OPEN')""",

    """CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'USER',
        created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ACTIVE',
        phone TEXT DEFAULT '')""",

    # Current schema = e-mail OTP only. Old phone columns are migrated away automatically.
    """CREATE TABLE IF NOT EXISTS password_reset_otps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        email_otp_hash TEXT NOT NULL,
        email_verified INTEGER DEFAULT 0,
        attempts INTEGER DEFAULT 0,
        expires_at TEXT NOT NULL,
        reset_token_hash TEXT,
        reset_token_expires_at TEXT,
        used INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id))""",

    """CREATE TABLE IF NOT EXISTS cameras (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        source TEXT NOT NULL,
        location TEXT DEFAULT '',
        zone TEXT DEFAULT '',
        required_ppe TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1,
        created_at TEXT,
        restricted_zone TEXT DEFAULT '')""",

    """CREATE TABLE IF NOT EXISTS incident_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        incident_id TEXT NOT NULL,
        author TEXT NOT NULL,
        author_email TEXT,
        message TEXT NOT NULL,
        attachment TEXT,
        created_at TEXT NOT NULL)""",

    """CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_email TEXT NOT NULL,
        title TEXT NOT NULL,
        message TEXT,
        incident_id TEXT,
        created_at TEXT NOT NULL,
        is_read INTEGER DEFAULT 0)""",

    """CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        actor TEXT,
        action TEXT NOT NULL,
        target_type TEXT,
        target_id TEXT,
        detail TEXT)""",

    # Small key/value store for settings that are edited from the dashboard (quiet hours, ...)
    """CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT)""",
]

# Columns added to an existing "incidents" table
NEW_INCIDENT_COLUMNS = {
    "severity": "TEXT DEFAULT 'MEDIUM'",
    "camera_id": "INTEGER",
    "camera_name": "TEXT",
    "zone": "TEXT",
    "track_id": "INTEGER",
    "duration_sec": "REAL DEFAULT 0",
    "description": "TEXT",
    "assigned_to": "TEXT",
    "resolution_notes": "TEXT",
    "resolved_at": "TEXT",
    "clip": "TEXT",
    "updated_at": "TEXT",
    "due_at": "TEXT",
    "overdue_alerted": "INTEGER DEFAULT 0",
    "assigned_by": "TEXT",
    "ai_review": "TEXT",      # AUTO_RESOLVED | NEEDS_REVIEW | LOW_CONFIDENCE | REOPENED
    "ai_note": "TEXT",
}

# Only these columns may be changed through update_incident_fields()
ALLOWED_INCIDENT_FIELDS = {
    "status", "severity", "camera_id", "camera_name", "zone", "track_id", "duration_sec",
    "description", "assigned_to", "resolution_notes", "resolved_at", "clip", "screenshot",
    "due_at", "overdue_alerted", "assigned_by", "ai_review", "ai_note",
}


# ============================================================
# MIGRATIONS
# ============================================================

def migrate_password_reset_otps(conn):
    """Old table had phone_otp_hash / phone_verified. Copy the useful columns into the e-mail-only table."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(password_reset_otps)").fetchall()}
    if not cols or "phone_otp_hash" not in cols:
        return

    print("PASSWORD RESET TABLE MIGRATION: old phone OTP columns found, moving to e-mail OTP only...")
    conn.execute("ALTER TABLE password_reset_otps RENAME TO password_reset_otps_old")
    conn.execute("""CREATE TABLE password_reset_otps (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        email_otp_hash TEXT NOT NULL,
        email_verified INTEGER DEFAULT 0,
        attempts INTEGER DEFAULT 0,
        expires_at TEXT NOT NULL,
        reset_token_hash TEXT,
        reset_token_expires_at TEXT,
        used INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY(user_id) REFERENCES users(id))""")
    conn.execute("""INSERT INTO password_reset_otps
        (id, user_id, email_otp_hash, email_verified, attempts, expires_at,
         reset_token_hash, reset_token_expires_at, used, created_at)
        SELECT id, user_id, email_otp_hash, email_verified, attempts, expires_at,
               reset_token_hash, reset_token_expires_at, used, created_at
        FROM password_reset_otps_old""")
    conn.execute("DROP TABLE password_reset_otps_old")
    print("PASSWORD RESET MIGRATION OK")


def init_db():
    conn = get_db()
    try:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass

        for stmt in SCHEMA:
            conn.execute(stmt)

        migrate_password_reset_otps(conn)

        # ---- incidents: new columns ----
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(incidents)").fetchall()}
        severity_is_new = "severity" not in existing
        for col, ddl in NEW_INCIDENT_COLUMNS.items():
            if col not in existing:
                conn.execute(f"ALTER TABLE incidents ADD COLUMN {col} {ddl}")

        # ---- cameras: restricted zone (JSON list of [x, y] points, 0..1 of the frame) ----
        if "restricted_zone" not in {r["name"] for r in conn.execute("PRAGMA table_info(cameras)").fetchall()}:
            conn.execute("ALTER TABLE cameras ADD COLUMN restricted_zone TEXT DEFAULT ''")

        # ---- users / comments: new columns ----
        if "phone" not in {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}:
            conn.execute("ALTER TABLE users ADD COLUMN phone TEXT DEFAULT ''")
        if "attachment" not in {r["name"] for r in conn.execute("PRAGMA table_info(incident_comments)").fetchall()}:
            conn.execute("ALTER TABLE incident_comments ADD COLUMN attachment TEXT")

        # ---- old timestamps 20260927_141537 -> 2026-09-27 14:15:37 ----
        conn.execute("""
            UPDATE incidents
            SET timestamp = substr(timestamp,1,4)||'-'||substr(timestamp,5,2)||'-'||substr(timestamp,7,2)||' '||
                            substr(timestamp,10,2)||':'||substr(timestamp,12,2)||':'||substr(timestamp,14,2)
            WHERE length(timestamp)=15 AND timestamp GLOB '[0-9]*_[0-9]*'""")

        # ---- severity for incidents created before severity existed ----
        if severity_is_new:
            for row in conn.execute("SELECT DISTINCT violation FROM incidents WHERE violation IS NOT NULL").fetchall():
                conn.execute("UPDATE incidents SET severity=? WHERE violation=?",
                             (incident_service.severity_for(row["violation"]), row["violation"]))

        # ---- deadline for old incidents; marked as alerted so no flood of old escalations ----
        sla = getattr(config, "SLA_MINUTES", {"CRITICAL": 60, "HIGH": 120, "MEDIUM": 240, "LOW": 480})
        conn.execute(f"""
            UPDATE incidents
            SET due_at = datetime(timestamp, '+' || (CASE severity
                    WHEN 'CRITICAL' THEN {int(sla.get('CRITICAL', 60))}
                    WHEN 'HIGH' THEN {int(sla.get('HIGH', 120))}
                    WHEN 'LOW' THEN {int(sla.get('LOW', 480))}
                    ELSE {int(sla.get('MEDIUM', 240))} END) || ' minutes'),
                overdue_alerted = 1
            WHERE due_at IS NULL AND timestamp IS NOT NULL
              AND timestamp GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] *'""")

        # ---- indexes ----
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_incidents_ts ON incidents(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status)",
            "CREATE INDEX IF NOT EXISTS idx_incidents_camera ON incidents(camera_id)",
            "CREATE INDEX IF NOT EXISTS idx_incidents_due_at ON incidents(due_at)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_email ON notifications(user_email)",
            "CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(is_read)",
            "CREATE INDEX IF NOT EXISTS idx_comments_incident ON incident_comments(incident_id)",
        ):
            conn.execute(stmt)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def seed_default_camera():
    conn = get_db()
    try:
        if conn.execute("SELECT COUNT(*) FROM cameras").fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO cameras (name, source, location, zone, required_ppe, enabled, created_at) VALUES (?,?,?,?,?,?,?)",
                ("Main Camera", config.DEFAULT_CAMERA_SOURCE, "Site entrance", "General", "", 1, now_str()))
            conn.commit()
    finally:
        conn.close()


# ============================================================
# AUDIT LOG
# ============================================================

def log_audit(actor, action, target_type="", target_id="", detail=""):
    """Audit failures must never break the main request."""
    conn = None
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO audit_log (ts, actor, action, target_type, target_id, detail) VALUES (?,?,?,?,?,?)",
            (now_str(), actor, action, target_type, str(target_id), detail))
        conn.commit()
    except Exception as e:
        print("audit error:", e)
    finally:
        if conn is not None:
            conn.close()


# ============================================================
# HELPERS
# ============================================================

def row_dict(row):
    return dict(row) if row is not None else None


def list_cameras(only_enabled=False):
    conn = get_db()
    try:
        q = "SELECT * FROM cameras" + (" WHERE enabled=1" if only_enabled else "") + " ORDER BY id"
        return [dict(r) for r in conn.execute(q).fetchall()]
    finally:
        conn.close()


def get_camera(cam_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM cameras WHERE id=?", (cam_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def recent_count(camera_id, violation, minutes=60):
    since = (datetime.now() - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM incidents WHERE camera_id IS ? AND violation=? AND timestamp>=?",
            (camera_id, violation, since)).fetchone()[0]
    finally:
        conn.close()


def _next_incident_code(conn):
    """INC-0001, INC-0002 ... one more than the highest existing number."""
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(incident_id, 5) AS INTEGER)) AS m FROM incidents WHERE incident_id LIKE 'INC-%'"
    ).fetchone()
    return f"INC-{(row['m'] or 0) + 1:04d}"


def insert_incident(violation, confidence, screenshot, camera=None, track_id=None,
                    duration_sec=0.0, ongoing=False):
    """Create an incident row. Severity goes up for repeated / ongoing violations. Returns (row, code)."""
    cam_id = camera["id"] if camera else None
    severity = incident_service.severity_for(violation)
    escalated = False
    if ongoing or recent_count(cam_id, violation) >= 3:
        severity = incident_service.bump(severity)
        escalated = True

    description = incident_service.enrich_incident_data(violation, confidence)["description"]
    if ongoing and duration_sec:
        description += f" Violation has continued for {int(duration_sec)} seconds."
    elif escalated:
        description += " Repeated violation in the last hour."

    sla = getattr(config, "SLA_MINUTES", {"CRITICAL": 60, "HIGH": 120, "MEDIUM": 240, "LOW": 480})
    due_at = (datetime.now() + timedelta(minutes=int(sla.get(severity, sla.get("MEDIUM", 240))))
              ).strftime("%Y-%m-%d %H:%M:%S")

    with _insert_lock:
        conn = get_db()
        try:
            code = _next_incident_code(conn)
            ts = now_str()
            conn.execute(
                """INSERT INTO incidents (incident_id, violation, confidence, timestamp, screenshot, status,
                   severity, camera_id, camera_name, zone, track_id, duration_sec, description, updated_at,
                   due_at, overdue_alerted)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (code, violation, float(confidence), ts, screenshot, "OPEN", severity, cam_id,
                 camera["name"] if camera else None, camera.get("zone") if camera else None,
                 track_id, float(duration_sec or 0), description, ts, due_at, 0))
            conn.commit()
            row = conn.execute("SELECT * FROM incidents WHERE incident_id=?", (code,)).fetchone()
            return dict(row), code
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def reserve_code():
    """Peek at the next code. Only insert_incident() finalises it (under the lock)."""
    with _insert_lock:
        conn = get_db()
        try:
            return _next_incident_code(conn)
        finally:
            conn.close()


def update_incident_fields(code, **fields):
    """Update selected incident columns. Unknown column names are rejected."""
    if not fields:
        return
    invalid = set(fields) - ALLOWED_INCIDENT_FIELDS
    if invalid:
        raise ValueError("Invalid incident field(s): " + ", ".join(sorted(invalid)))

    fields["updated_at"] = now_str()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn = get_db()
    try:
        conn.execute(f"UPDATE incidents SET {cols} WHERE incident_id=?", (*fields.values(), code))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()