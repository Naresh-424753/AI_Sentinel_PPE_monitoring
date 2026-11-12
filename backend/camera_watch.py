"""Tells the admins when a camera stops working, and when it is back."""
import threading
import time

from backend import alerts, config, db, notify
from backend.db import log_audit
from backend.detector import manager

BAD = {"offline", "error", "model_error", "stopped"}
_state = {}       # camera id -> {"since": timestamp, "alerted": bool}
_started = False


def _alert(cam, info, down, minutes=0.0):
    where = cam.get("location") or "no location set"
    if down:
        title = f"Camera offline: {cam['name']}"
        text = f"Camera '{cam['name']}' ({where}) is OFFLINE. {info.get('error') or ''}".strip()
    else:
        title = f"Camera back online: {cam['name']}"
        text = f"Camera '{cam['name']}' ({where}) is back online after {minutes:.0f} min."
    admins = [u["email"] for u in notify.users_with_role("ADMIN")]
    notify.notify(admins, title, text, None, email=True)
    alerts.dispatch({"incident_id": "CAMERA", "violation": "camera_offline",
                     "severity": "CRITICAL" if down else "MEDIUM", "camera_name": cam["name"],
                     "_text": text, "_subject": f"[AI Sentinel] {title}"}, force=True)
    log_audit("system", "CAMERA_OFFLINE" if down else "CAMERA_ONLINE", "camera", cam["id"], text)


def check_once(now=None):
    now = time.time() if now is None else now
    cams = {c["id"]: c for c in db.list_cameras(only_enabled=True)}
    for cid in list(_state):
        if cid not in cams:          # removed or disabled
            _state.pop(cid)
    for cid, cam in cams.items():
        info = manager.info(cam)
        st = _state.get(cid)
        if info["status"] in BAD:
            if st is None:
                st = _state[cid] = {"since": now, "alerted": False}
            if not st["alerted"] and now - st["since"] >= config.CAMERA_OFFLINE_GRACE_SECONDS:
                st["alerted"] = True
                _alert(cam, info, down=True)
        elif info["status"] == "online" and st is not None:
            if st["alerted"]:
                _alert(cam, info, down=False, minutes=(now - st["since"]) / 60)
            _state.pop(cid)


def _loop():
    time.sleep(30)                    # let the camera workers start first
    while True:
        try:
            check_once()
        except Exception as e:
            print("camera watch failed:", e)
        time.sleep(15)


def start():
    global _started
    if _started or config.DISABLE_DETECTION:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="camera-watch").start()