"""Repeat offenders and quiet-hour (alert mute) settings."""
from datetime import date, datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend import mute
from backend.db import get_db, log_audit
from backend.security import get_current_user, require_role

router = APIRouter(tags=["extras"])

VALID_TS = "timestamp GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] *'"
EPISODE_GAP_SECONDS = 600   # re-reports of one ongoing violation (every ~2 min) count as ONE occurrence


def _ts(text):
    try:
        return datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _episodes(times):
    """Number of separate occurrences: incidents closer than EPISODE_GAP_SECONDS belong to the same one."""
    count, last = 0, None
    for t in sorted(times):
        if last is None or (t - last).total_seconds() > EPISODE_GAP_SECONDS:
            count += 1
        last = t
    return count


# ============================================================
# REPEAT OFFENDERS
# ============================================================

@router.get("/api/repeat-offenders")
def repeat_offenders(
    days: int = Query(30, ge=1, le=365),
    min_count: int = Query(3, ge=2, le=50),
    user=Depends(get_current_user),
):
    """
    hotspots: the same violation, on the same camera and zone, happening again and again.
    tracks:   the same tracking ID on the same camera on the same day, with several separate violations.

    The camera does not know who a person is. A tracking ID is only stable while the person stays in view,
    so "tracks" is a hint, not a name. Hotspots are the more reliable list.
    """
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    conn = get_db()
    try:
        rows = conn.execute(
            f"SELECT incident_id, timestamp, violation, camera_id, camera_name, zone, track_id, status "
            f"FROM incidents WHERE {VALID_TS} AND timestamp >= ? AND status != 'FALSE_POSITIVE' "
            f"ORDER BY timestamp", (since,)).fetchall()
    finally:
        conn.close()

    spots, tracks = {}, {}
    for r in rows:
        t = _ts(r["timestamp"])
        if not t:
            continue
        key = (r["camera_id"], r["camera_name"] or "Unknown", r["zone"] or "", r["violation"])
        s = spots.setdefault(key, {"times": [], "open": 0, "last": ""})
        s["times"].append(t)
        s["last"] = r["timestamp"]
        s["open"] += 1 if str(r["status"]).upper() in ("OPEN", "ASSIGNED") else 0

        if r["track_id"] is not None:
            tk = (r["camera_id"], r["camera_name"] or "Unknown", r["track_id"], r["timestamp"][:10])
            g = tracks.setdefault(tk, {"by_violation": {}, "ids": [], "last": ""})
            g["by_violation"].setdefault(r["violation"], []).append(t)
            g["ids"].append(r["incident_id"])
            g["last"] = r["timestamp"]

    hotspots = []
    for (cam_id, cam, zone, violation), s in spots.items():
        n = _episodes(s["times"])
        if n >= min_count:
            hotspots.append({"camera_id": cam_id, "camera": cam, "zone": zone, "violation": violation,
                             "occurrences": n, "open": s["open"], "last_seen": s["last"]})
    hotspots.sort(key=lambda x: (-x["occurrences"], x["last_seen"]))

    track_list = []
    for (cam_id, cam, track_id, day), g in tracks.items():
        n = sum(_episodes(t) for t in g["by_violation"].values())
        if n >= min_count:
            track_list.append({"camera_id": cam_id, "camera": cam, "track_id": track_id, "date": day,
                               "occurrences": n, "violations": sorted(g["by_violation"]),
                               "incident_ids": g["ids"][:5], "last_seen": g["last"]})
    track_list.sort(key=lambda x: (-x["occurrences"], x["last_seen"]))

    return {"days": days, "min_count": min_count, "hotspots": hotspots[:10], "tracks": track_list[:10]}


# ============================================================
# QUIET HOURS
# ============================================================

class MuteWindow(BaseModel):
    start: str
    end: str
    days: List[str] = []


class MuteBody(BaseModel):
    enabled: bool = False
    allow_critical: bool = True
    windows: List[MuteWindow] = []


def _mute_state(settings):
    return {**settings,
            "muted_now": mute.is_muted(None, settings=settings),
            "minutes_to_change": mute.next_change(settings=settings),
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M")}


@router.get("/api/settings/mute")
def get_mute(user=Depends(require_role("ADMIN"))):
    return _mute_state(mute.get_settings())


@router.put("/api/settings/mute")
def put_mute(body: MuteBody, user=Depends(require_role("ADMIN"))):
    try:
        clean = mute.save_settings(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    log_audit(user["email"], "SETTINGS_MUTE_UPDATE", "settings", "alert_mute",
              f"enabled={clean['enabled']}, windows={len(clean['windows'])}, allow_critical={clean['allow_critical']}")
    return _mute_state(clean)