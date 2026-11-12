# AI Sentinel v2 - Real-time PPE monitoring

YOLO camera detection + FastAPI backend + React dashboard.

## What is new in v2

**Detection**
- Violations are confirmed over several checks before an incident is created (`REQUIRED_FRAMES`), so one bad frame does not raise a false alarm.
- Worker tracking (ByteTrack): one incident per person per violation. A violation that keeps going is re-reported every `INCIDENT_COOLDOWN` seconds with its duration, and its severity goes up.
- Zone rules: each camera has its own list of required PPE (for example goggles + gloves in the welding bay).
- Any class that starts with `no_` is treated as a violation automatically, so new PPE classes work without code changes.
- Many cameras (webcam, RTSP/IP, or a video file). Each camera runs in its own thread and any number of browser tabs can watch it (the old "two tabs break the camera" problem is gone).
- Each incident saves a screenshot and a 10 second video clip (5 s before, 5 s after).

**Alerts**: live dashboard pop-up + sound, Telegram, email (with photo), WhatsApp (Twilio) and a generic webhook. Set only the channels you want in `.env`.

**Dashboard**: live camera view, safety score, trend, violations by type / hour / shift / camera, incident table with filters, assignment workflow (Open > Assigned > Resolved / False alarm), resolution notes, CSV / Excel / PDF export.

**Users and security**: login now returns a JWT token and every data endpoint requires it. Roles: Viewer, Supervisor, Admin, Super admin. Admin page has user management, an activity (audit) log and alert status.

Your existing login, signup and email-OTP password reset are kept (same URLs), and your existing database is upgraded in place.

## Setup

Requirements: Python 3.10+, Node.js 18+.

1. **Back up** your current `ai_sentinel.db`, then copy it to `backend/ai_sentinel.db` (optional - a fresh one is created otherwise). New columns are added automatically; nothing is deleted.
2. Put your trained model at `runs/detect/train-2/weights/best.pt` (project root or `backend/`), or set `MODEL_PATH` in `.env`.
3. Copy `.env.example` to `.env` and fill in SMTP etc. (copy the values from your old `_env` file; do not share that file).
4. Start the backend: `run_backend.bat` (Windows) or `./run_backend.sh`. API docs: http://127.0.0.1:8000/docs
5. Start the dashboard in a second window: `run_frontend.bat` or `./run_frontend.sh`, then open http://localhost:5173
6. Sign up in the dashboard, then make yourself the owner:
   `python make_super_admin.py you@example.com`
   (run it inside the activated `.venv`, from the project root).

No camera or model yet? Set `DISABLE_DETECTION=true` in `.env`, then run `python -m backend.seed_demo` to fill the dashboard with demo incidents (`--clear` removes them).

## Roles

| Role | Can do |
|---|---|
| Viewer (and old "USER") | See dashboard, cameras, incidents, exports |
| Supervisor | + assign, resolve, add notes |
| Admin | + cameras, users (up to supervisor), delete incidents, audit log, alert test |
| Super admin | + create admins |

New signups are always Viewers.

## Tuning

| Setting | Meaning |
|---|---|
| `CONFIDENCE_THRESHOLD` | Higher = fewer false alarms, may miss some |
| `REQUIRED_FRAMES` | Consecutive checks needed before an incident |
| `FRAME_SKIP` | Run YOLO on every Nth frame; raise it if the stream is slow |
| `INCIDENT_COOLDOWN` | Seconds before an ongoing violation is reported again |
| `VIOLATION_CLASSES` | Optional fixed list instead of the `no_` rule |

Severity: no_helmet / no_harness = critical, no_goggle = high, others = medium. A repeat (3+ in an hour on the same camera) or an ongoing violation goes up one level. Edit `backend/services/incident_service.py` to change this.

## Model classes

Your old `data.yaml` listed 5 classes but the API looked for `no_gloves`, `no_goggle`, `no_boots`. A class that is not in the trained model can never be detected. Check what your model really knows:
`python -c "from ultralytics import YOLO; print(YOLO('path/to/best.pt').names)"`
`training/data.yaml` has a corrected 11-class file (the stray `k` line is removed). Its class order must match your label files. Mark wrong detections as "False alarm" in the dashboard and use those screenshots to improve the next training round (`python training/train.py`).

## Notes

- Clips are converted to H.264 with ffmpeg (installed through `imageio-ffmpeg`) so they play in the browser. Without ffmpeg the clip is saved but some browsers cannot play it.
- Each camera loads its own YOLO copy (needed for correct tracking), so RAM use grows with the number of cameras.
- Screenshots and clips are served from `/images` and `/clips` without login (file names are hard to guess but not secret). Put the API behind HTTPS and a reverse proxy before exposing it to the internet.
- Not included: face recognition / per-worker scores (needs worker consent and legal review), Docker files, PostgreSQL/MongoDB migration, Jetson/ONNX export.
