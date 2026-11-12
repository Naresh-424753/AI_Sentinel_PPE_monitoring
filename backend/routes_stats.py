"""Dashboard statistics and analytics."""
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends

from backend.db import get_db
from backend.security import get_current_user
from backend.services import incident_service

router = APIRouter(tags=["stats"])

VALID_TS = "timestamp GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] *'"
SHIFTS = [("Morning (06-14)", range(6, 14)), ("Evening (14-22)", range(14, 22)), ("Night (22-06)", None)]


def _count(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


@router.get("/stats")
def stats(user=Depends(get_current_user)):
    """Original keys kept, plus a few new ones."""
    conn = get_db()
    today = date.today().isoformat()
    out = {
        "total_incidents": _count(conn, "SELECT COUNT(*) FROM incidents"),
        "open_incidents": _count(conn, "SELECT COUNT(*) FROM incidents WHERE status='OPEN'"),
        "assigned_incidents": _count(conn, "SELECT COUNT(*) FROM incidents WHERE status='ASSIGNED'"),
        "resolved_incidents": _count(conn, "SELECT COUNT(*) FROM incidents WHERE status='RESOLVED'"),
        "critical_open": _count(conn, "SELECT COUNT(*) FROM incidents WHERE severity='CRITICAL' AND status IN ('OPEN','ASSIGNED')"),
        "today": _count(conn, "SELECT COUNT(*) FROM incidents WHERE timestamp >= ?", (today,)),
        "overdue": _count(conn, "SELECT COUNT(*) FROM incidents WHERE status IN ('OPEN','ASSIGNED') "
                                "AND due_at IS NOT NULL AND due_at < ?", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),)),
        "needs_review": _count(conn, "SELECT COUNT(*) FROM incidents WHERE status IN ('OPEN','ASSIGNED') "
                                     "AND ai_review IN ('NEEDS_REVIEW','LOW_CONFIDENCE')"),
        "auto_resolved": _count(conn, "SELECT COUNT(*) FROM incidents WHERE ai_review='AUTO_RESOLVED'"),
    }
    for v in ("no_helmet", "no_gloves", "no_goggle", "no_boots"):
        out[v] = _count(conn, "SELECT COUNT(*) FROM incidents WHERE violation=?", (v,))
    conn.close()
    return out


@router.get("/api/analytics")
def analytics(days: int = 30, camera_id: Optional[int] = None, user=Depends(get_current_user)):
    days = max(1, min(days, 365))
    since = (date.today() - timedelta(days=days - 1)).isoformat()
    cond, params = f"{VALID_TS} AND timestamp >= ?", [since]
    if camera_id:
        cond += " AND camera_id = ?"
        params.append(camera_id)

    conn = get_db()

    def rows(sql):
        return conn.execute(sql, params).fetchall()

    per_day = {r["d"]: r["c"] for r in rows(
        f"SELECT substr(timestamp,1,10) d, COUNT(*) c FROM incidents WHERE {cond} GROUP BY d")}
    trend = []
    for i in range(days):
        d = (date.today() - timedelta(days=days - 1 - i)).isoformat()
        trend.append({"date": d, "count": per_day.get(d, 0)})

    by_hour_map = {r["h"]: r["c"] for r in rows(
        f"SELECT CAST(substr(timestamp,12,2) AS INTEGER) h, COUNT(*) c FROM incidents WHERE {cond} GROUP BY h")}
    by_hour = [{"hour": h, "count": by_hour_map.get(h, 0)} for h in range(24)]

    shift_counts = []
    for name, rng in SHIFTS:
        hrs = rng if rng is not None else [h for h in range(24) if h >= 22 or h < 6]
        shift_counts.append({"shift": name, "count": sum(by_hour_map.get(h, 0) for h in hrs)})

    avg_res = conn.execute(
        f"SELECT AVG((julianday(resolved_at)-julianday(timestamp))*1440) FROM incidents "
        f"WHERE {cond} AND status='RESOLVED' AND resolved_at IS NOT NULL", params).fetchone()[0]

    result = {
        "days": days,
        "total": sum(per_day.values()),
        "trend": trend,
        "by_type": [{"name": r["k"], "count": r["c"]} for r in rows(
            f"SELECT violation k, COUNT(*) c FROM incidents WHERE {cond} GROUP BY k ORDER BY c DESC")],
        "by_severity": [{"name": r["k"] or "MEDIUM", "count": r["c"]} for r in rows(
            f"SELECT severity k, COUNT(*) c FROM incidents WHERE {cond} GROUP BY k")],
        "by_status": [{"name": r["k"] or "OPEN", "count": r["c"]} for r in rows(
            f"SELECT status k, COUNT(*) c FROM incidents WHERE {cond} GROUP BY k")],
        "by_camera": [{"name": r["k"] or "Unknown", "count": r["c"]} for r in rows(
            f"SELECT camera_name k, COUNT(*) c FROM incidents WHERE {cond} GROUP BY k ORDER BY c DESC")],
        "by_hour": by_hour,
        "by_shift": shift_counts,
        "avg_resolution_minutes": round(avg_res, 1) if avg_res is not None else None,
    }
    conn.close()
    return result


@router.get("/api/safety-score")
def safety_score(hours: int = 24, user=Depends(get_current_user)):
    hours = max(1, min(hours, 24 * 90))
    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    sev = {r["k"] or "MEDIUM": r["c"] for r in conn.execute(
        f"SELECT severity k, COUNT(*) c FROM incidents WHERE {VALID_TS} AND timestamp >= ? "
        "AND status != 'FALSE_POSITIVE' GROUP BY k", (since,)).fetchall()}
    total = sum(sev.values())
    resolved = _count(conn, f"SELECT COUNT(*) FROM incidents WHERE {VALID_TS} AND timestamp >= ? AND status='RESOLVED'", (since,))
    conn.close()
    score = incident_service.weighted_safety_score(sev, resolved / total if total else 0)
    label = incident_service.score_label(score)
    return {"safety_score": score, "status": label, "label": label, "window_hours": hours,
            "total_violations": total, "by_severity": sev}