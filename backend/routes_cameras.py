"""Camera management and live MJPEG streaming."""
import json
import time
from datetime import date
from typing import Optional

import cv2
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend import db
from backend.detector import manager
from backend.security import get_current_user, require_role

router = APIRouter(tags=["cameras"])


class CameraBody(BaseModel):
    name: str
    source: str = "0"
    location: str = ""
    zone: str = ""
    required_ppe: str = ""     # comma separated violation classes for this zone; empty = all
    enabled: bool = True
    # JSON list of [x, y] points (0..1 of the frame width / height). "" = no restricted zone.
    # None (field not sent) = leave the saved zone as it is.
    restricted_zone: Optional[str] = None


def clean_zone(text):
    """Validate a restricted zone and return it as compact JSON ('' when empty). Raises HTTP 400 when invalid."""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        pts = json.loads(text)
        if not isinstance(pts, list) or not 3 <= len(pts) <= 40:
            raise ValueError
        out = []
        for p in pts:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                raise ValueError
            x, y = float(p[0]), float(p[1])
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError
            out.append([round(x, 4), round(y, 4)])
    except (ValueError, TypeError):
        raise HTTPException(400, "Restricted zone must be 3 to 40 points, each [x, y] between 0 and 1")
    return json.dumps(out, separators=(",", ":"))


def _public(cam):
    cam = dict(cam)
    cam.update(manager.info(cam))
    return cam


def _counts():
    """Open incidents, today's incidents and last incident time for every camera."""
    conn = db.get_db()
    rows = conn.execute(
        """SELECT camera_id,
                  SUM(CASE WHEN status IN ('OPEN','ASSIGNED') THEN 1 ELSE 0 END) AS open_count,
                  SUM(CASE WHEN timestamp >= ? THEN 1 ELSE 0 END) AS today_count,
                  MAX(timestamp) AS last_incident
           FROM incidents WHERE camera_id IS NOT NULL GROUP BY camera_id""",
        (date.today().isoformat(),)).fetchall()
    conn.close()
    return {r["camera_id"]: dict(r) for r in rows}


@router.get("/api/cameras")
def list_cameras(user=Depends(get_current_user)):
    counts = _counts()
    out = []
    for c in db.list_cameras():
        c = _public(c)
        c.update(counts.get(c["id"], {"open_count": 0, "today_count": 0, "last_incident": None}))
        out.append(c)
    return out


@router.post("/api/cameras")
def add_camera(body: CameraBody, user=Depends(require_role("ADMIN"))):
    if not body.name.strip() or not body.source.strip():
        raise HTTPException(400, "Name and source are required")
    zone = clean_zone(body.restricted_zone)
    conn = db.get_db()
    cur = conn.execute(
        "INSERT INTO cameras (name, source, location, zone, required_ppe, enabled, created_at, restricted_zone) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (body.name.strip(), body.source.strip(), body.location.strip(), body.zone.strip(),
         body.required_ppe.strip(), int(body.enabled), db.now_str(), zone))
    conn.commit()
    cam_id = cur.lastrowid
    conn.close()
    manager.start_camera(db.get_camera(cam_id))
    db.log_audit(user["email"], "CAMERA_ADD", "camera", cam_id, body.name)
    return _public(db.get_camera(cam_id))


@router.put("/api/cameras/{cam_id}")
def update_camera(cam_id: int, body: CameraBody, user=Depends(require_role("ADMIN"))):
    if not db.get_camera(cam_id):
        raise HTTPException(404, "Camera not found")
    zone = None if body.restricted_zone is None else clean_zone(body.restricted_zone)
    conn = db.get_db()
    conn.execute(
        "UPDATE cameras SET name=?, source=?, location=?, zone=?, required_ppe=?, enabled=? WHERE id=?",
        (body.name.strip(), body.source.strip(), body.location.strip(), body.zone.strip(),
         body.required_ppe.strip(), int(body.enabled), cam_id))
    if zone is not None:
        conn.execute("UPDATE cameras SET restricted_zone=? WHERE id=?", (zone, cam_id))
    conn.commit()
    conn.close()
    manager.restart_camera(cam_id)
    detail = body.name + (" (restricted zone " + ("set" if zone else "cleared") + ")" if zone is not None else "")
    db.log_audit(user["email"], "CAMERA_UPDATE", "camera", cam_id, detail)
    return _public(db.get_camera(cam_id))


@router.delete("/api/cameras/{cam_id}")
def delete_camera(cam_id: int, user=Depends(require_role("ADMIN"))):
    cam = db.get_camera(cam_id)
    if not cam:
        raise HTTPException(404, "Camera not found")
    manager.stop_camera(cam_id)
    conn = db.get_db()
    conn.execute("DELETE FROM cameras WHERE id=?", (cam_id,))
    conn.commit()
    conn.close()
    db.log_audit(user["email"], "CAMERA_DELETE", "camera", cam_id, cam["name"])
    return {"success": True}


def _mjpeg(worker):
    last, last_change = -1, time.time()
    while not worker.stop_event.is_set():
        jpg = worker.latest_jpeg
        if jpg is None or worker.frame_id == last:
            if time.time() - last_change > 20:
                break
            time.sleep(0.03)
            continue
        last, last_change = worker.frame_id, time.time()
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n"
        time.sleep(0.04)


def _stream_for(cam_id):
    w = manager.get(cam_id)
    if not w:
        raise HTTPException(404, "Camera is not running")
    return StreamingResponse(_mjpeg(w), media_type="multipart/x-mixed-replace; boundary=frame")


@router.get("/api/cameras/{cam_id}/stream")
def stream(cam_id: int, user=Depends(get_current_user)):
    return _stream_for(cam_id)


@router.get("/api/cameras/{cam_id}/snapshot")
def snapshot(cam_id: int, user=Depends(get_current_user)):
    w = manager.get(cam_id)
    if not w or w.latest_jpeg is None:
        raise HTTPException(404, "No frame available")
    return Response(w.latest_jpeg, media_type="image/jpeg")


@router.get("/api/cameras/{cam_id}/frame")
def clean_frame(cam_id: int, user=Depends(require_role("ADMIN"))):
    """Latest frame WITHOUT boxes or the zone outline: the picture the restricted-zone editor draws on."""
    w = manager.get(cam_id)
    raw = getattr(w, "last_raw", None) if w else None
    if raw is None:
        raise HTTPException(404, "No frame yet. The camera must be online to draw a zone.")
    h, wd = raw.shape[:2]
    if wd > 960:
        raw = cv2.resize(raw, (960, int(h * 960 / wd)))
    ok, buf = cv2.imencode(".jpg", raw, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise HTTPException(500, "Could not encode the frame")
    return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/camera")
def legacy_camera(user=Depends(get_current_user)):
    """Original endpoint: first enabled camera."""
    cams = db.list_cameras(only_enabled=True)
    if not cams:
        raise HTTPException(404, "No camera configured")
    return _stream_for(cams[0]["id"])