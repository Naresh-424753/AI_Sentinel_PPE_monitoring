"""Central configuration. Everything can be overridden from the .env file in the project root."""
import os
import secrets
from dotenv import load_dotenv

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BACKEND_DIR)

load_dotenv(os.path.join(ROOT_DIR, ".env"))
load_dotenv(os.path.join(BACKEND_DIR, ".env"))


def env_str(name, default=""):
    return os.getenv(name, default).strip()


def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def env_float(name, default):
    try:
        return float(os.getenv(name, str(default)).strip())
    except ValueError:
        return default


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name):
    return [x.strip() for x in os.getenv(name, "").split(",") if x.strip()]


# ---------------- paths ----------------
DB_PATH = env_str("DB_PATH") or os.path.join(BACKEND_DIR, "ai_sentinel.db")
INCIDENTS_DIR = os.path.join(BACKEND_DIR, "incidents")
CLIPS_DIR = os.path.join(BACKEND_DIR, "clips")
PROOFS_DIR = os.path.join(BACKEND_DIR, "proofs")
TRAINING_DIR = os.path.join(BACKEND_DIR, "training_frames")
REPORTS_DIR = os.path.join(BACKEND_DIR, "reports")
for _d in (INCIDENTS_DIR, CLIPS_DIR, REPORTS_DIR, PROOFS_DIR, TRAINING_DIR):
    os.makedirs(_d, exist_ok=True)


def resolve_model_path():
    explicit = env_str("MODEL_PATH")
    if explicit and not os.path.isabs(explicit):
        explicit = os.path.join(ROOT_DIR, explicit)
    candidates = [
        explicit,
        os.path.join(BACKEND_DIR, "runs", "detect", "train-2", "weights", "best.pt"),
        os.path.join(ROOT_DIR, "runs", "detect", "train-2", "weights", "best.pt"),
        os.path.join(ROOT_DIR, "models", "best.pt"),
        os.path.join(ROOT_DIR, "best.pt"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return explicit or candidates[1]


MODEL_PATH = resolve_model_path()

# ---------------- detection ----------------
DISABLE_DETECTION = env_bool("DISABLE_DETECTION", False)   # true = run API/dashboard only
CONFIDENCE_THRESHOLD = env_float("CONFIDENCE_THRESHOLD", 0.35)
IMAGE_SIZE = env_int("IMAGE_SIZE", 640)
FRAME_SKIP = max(1, env_int("FRAME_SKIP", 2))                # run YOLO on every Nth frame
REQUIRED_FRAMES = max(1, env_int("REQUIRED_FRAMES", 3))      # consecutive detections to confirm
MIN_GAP_SECONDS = env_float("MIN_GAP_SECONDS", 3)            # min gap between incidents of one class on one camera
INCIDENT_COOLDOWN = env_float("INCIDENT_COOLDOWN", 120)      # re-report a violation that is still ongoing
TRACKER = env_str("TRACKER", "bytetrack.yaml")
CLIP_PRE_SECONDS = env_int("CLIP_PRE_SECONDS", 5)
CLIP_POST_SECONDS = env_int("CLIP_POST_SECONDS", 5)
DEFAULT_CAMERA_SOURCE = env_str("CAMERA_SOURCE", "0")

# comma separated list. Empty = auto: every class that starts with "no_" is a violation.
VIOLATION_CLASSES = {c.lower() for c in env_list("VIOLATION_CLASSES")}


def is_violation_class(name):
    if VIOLATION_CLASSES:
        return name in VIOLATION_CLASSES
    return name.startswith("no_")


# ---------------- auth ----------------
def _load_jwt_secret():
    s = env_str("JWT_SECRET")
    if s:
        return s
    path = os.path.join(BACKEND_DIR, ".jwt_secret")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    s = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(s)
    return s


JWT_SECRET = _load_jwt_secret()
JWT_EXPIRE_HOURS = env_int("JWT_EXPIRE_HOURS", 12)
CORS_ORIGINS = env_list("CORS_ORIGINS") or ["http://localhost:5173", "http://127.0.0.1:5173"]

# ---------------- email / alerts ----------------
SMTP_HOST = env_str("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = env_int("SMTP_PORT", 587)
SMTP_EMAIL = env_str("SMTP_EMAIL")
SMTP_PASSWORD = env_str("SMTP_PASSWORD")

ALERT_MIN_SEVERITY = env_str("ALERT_MIN_SEVERITY", "MEDIUM").upper()
ALERT_EMAILS = env_list("ALERT_EMAILS")
TELEGRAM_BOT_TOKEN = env_str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = env_str("TELEGRAM_CHAT_ID")
TWILIO_SID = env_str("TWILIO_SID")
TWILIO_TOKEN = env_str("TWILIO_TOKEN")
TWILIO_WHATSAPP_FROM = env_str("TWILIO_WHATSAPP_FROM")   # e.g. whatsapp:+14155238886
WHATSAPP_TO = env_list("WHATSAPP_TO")                    # e.g. whatsapp:+919999999999
ALERT_WEBHOOK_URL = env_str("ALERT_WEBHOOK_URL")
PUBLIC_API_URL = env_str("PUBLIC_API_URL", "http://127.0.0.1:8000")
FRONTEND_URL = env_str("FRONTEND_URL", "http://localhost:5173")

# ---------------- deadlines (SLA) and notifications ----------------
# minutes allowed to resolve an incident, by severity
SLA_MINUTES = {
    "CRITICAL": env_int("SLA_CRITICAL_MIN", 60),
    "HIGH": env_int("SLA_HIGH_MIN", 120),
    "MEDIUM": env_int("SLA_MEDIUM_MIN", 240),
    "LOW": env_int("SLA_LOW_MIN", 480),
}
SLA_CHECK_SECONDS = env_int("SLA_CHECK_SECONDS", 60)   # how often overdue incidents are looked for
NOTIFY_EMAIL = env_bool("NOTIFY_EMAIL", True)          # also e-mail people when something is assigned / overdue
# people whose address ends with one of these domains only get the in-app notification (no e-mail),
# e.g. demo accounts that have no real mailbox:  NOTIFY_EMAIL_EXCLUDE_DOMAINS=aisentinel.com
NOTIFY_EMAIL_EXCLUDE_DOMAINS = [d.lower().lstrip("@") for d in env_list("NOTIFY_EMAIL_EXCLUDE_DOMAINS")]

# ---------------- camera watch, scheduled reports, retraining frames ----------------
CAMERA_OFFLINE_GRACE_SECONDS = env_int("CAMERA_OFFLINE_GRACE_SECONDS", 60)   # offline this long before admins are told
REPORT_DAILY_TIME = env_str("REPORT_DAILY_TIME", "08:00")    # HH:MM, empty = no daily report
REPORT_WEEKLY_DAY = env_str("REPORT_WEEKLY_DAY", "MON").upper()  # MON..SUN, empty = no weekly report
REPORT_EMAILS = env_list("REPORT_EMAILS")                    # empty = ALERT_EMAILS, else all supervisors/admins
SAVE_TRAINING_FRAMES = env_bool("SAVE_TRAINING_FRAMES", True)  # keep the raw frame of every incident for retraining

# ---------------- AI auto-resolve / human review ----------------
AUTO_RESOLVE = env_bool("AUTO_RESOLVE", True)                       # AI may close an incident when the worker is compliant again
AUTO_RESOLVE_CONFIRM_SECONDS = env_float("AUTO_RESOLVE_CONFIRM_SECONDS", 8)  # compliance must be seen this long
AUTO_RESOLVE_MIN_CONF = env_float("AUTO_RESOLVE_MIN_CONF", 0.5)     # min confidence of the 'wearing PPE' detection
REVIEW_CONFIDENCE = env_float("REVIEW_CONFIDENCE", 0.5)             # below this the AI says "I am not sure" and a human decides