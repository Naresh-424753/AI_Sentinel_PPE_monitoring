"""Scheduled-report controls and the retraining pack (false alarms -> better model)."""
import io
import os
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from backend import config, report_scheduler
from backend.db import get_db, log_audit
from backend.security import require_role

router = APIRouter(tags=["reports"])


@router.get("/api/reports/status")
def report_status(user=Depends(require_role("ADMIN"))):
    return report_scheduler.status()


@router.post("/api/reports/send-now")
def send_now(period: str = Query("daily", pattern="^(daily|weekly)$"), user=Depends(require_role("ADMIN"))):
    try:
        n = report_scheduler.send_report(period)
    except Exception as e:
        raise HTTPException(502, f"Could not send the report: {e}")
    log_audit(user["email"], "REPORT_SENT", "report", period, f"{n} recipients")
    return {"success": True, "message": f"{period.title()} report sent to {n} recipient(s)"}


# ---------------- retraining pack ----------------

def _false_alarms():
    conn = get_db()
    rows = conn.execute("SELECT incident_id FROM incidents WHERE status='FALSE_POSITIVE' ORDER BY id").fetchall()
    conn.close()
    return [r["incident_id"] for r in rows]


def _frame(code, ext):
    return os.path.join(config.TRAINING_DIR, f"{code}.{ext}")


README = """AI Sentinel - retraining pack
=============================
images/   raw camera frames of incidents you marked "False alarm"
labels/   what the model PREDICTED on each frame (YOLO format) - these are the mistakes
classes.txt  class names, in the order of the class numbers

What to do
1. Open images + labels in a labelling tool (LabelImg, Roboflow, CVAT).
2. Correct the labels: delete boxes that were wrong, fix classes, add missing boxes.
   A frame where the worker IS wearing the helmet should have the "helmet" box, not "no_helmet".
3. Copy the corrected images and labels into your dataset (images/train + labels/train).
4. Retrain:  python training/train.py   and point MODEL_PATH at the new best.pt.
"""


@router.get("/api/training/status")
def training_status(user=Depends(require_role("ADMIN"))):
    codes = _false_alarms()
    return {"false_alarms": len(codes), "with_frames": sum(1 for c in codes if os.path.exists(_frame(c, "jpg")))}


@router.get("/api/training/export")
def training_export(user=Depends(require_role("ADMIN"))):
    buf, n = io.BytesIO(), 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for code in _false_alarms():
            if not os.path.exists(_frame(code, "jpg")):
                continue                       # incidents from before this feature have no raw frame
            z.write(_frame(code, "jpg"), f"images/{code}.jpg")
            if os.path.exists(_frame(code, "txt")):
                z.write(_frame(code, "txt"), f"labels/{code}.txt")
            n += 1
        if n:
            classes = os.path.join(config.TRAINING_DIR, "classes.txt")
            if os.path.exists(classes):
                z.write(classes, "classes.txt")
            z.writestr("README.txt", README)
    if not n:
        raise HTTPException(404, "No false-alarm frames yet. Frames are saved for new incidents only: "
                                 "mark some new incidents as 'False alarm' first.")
    log_audit(user["email"], "TRAINING_EXPORT", "training", "pack", f"{n} frames")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="training_pack.zip"'})