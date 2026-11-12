"""Users, audit log, alert settings and the live event stream."""
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend import alerts
from backend.db import get_db, log_audit
from backend.events import bus
from backend.security import ASSIGNABLE_ROLES, get_current_user, rank, require_role

router = APIRouter(tags=["admin"])


@router.get("/api/users")
def list_users(user=Depends(require_role("ADMIN"))):
    conn = get_db()
    rows = conn.execute("SELECT id, name, email, role, status, created_at FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/api/users/lite")
def users_lite(user=Depends(require_role("SUPERVISOR"))):
    """Names for the 'assign to' dropdown."""
    conn = get_db()
    rows = conn.execute("SELECT id, name, email FROM users WHERE status='ACTIVE' ORDER BY name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


class UserPatch(BaseModel):
    role: Optional[str] = None
    status: Optional[str] = None


@router.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UserPatch, actor=Depends(require_role("ADMIN"))):
    if user_id == actor["id"]:
        raise HTTPException(400, "You cannot change your own role or status")
    conn = get_db()
    target = conn.execute("SELECT id, email, role FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        conn.close()
        raise HTTPException(404, "User not found")

    is_super = str(actor["role"]).upper() == "SUPER_ADMIN"
    if not is_super and rank(target["role"]) >= rank(actor["role"]):
        conn.close()
        raise HTTPException(403, "You cannot modify a user with the same or higher role")

    fields = {}
    if body.role:
        role = body.role.upper()
        if role not in ASSIGNABLE_ROLES:
            conn.close()
            raise HTTPException(400, f"Role must be one of {', '.join(ASSIGNABLE_ROLES)}")
        if not is_super and rank(role) >= rank(actor["role"]):
            conn.close()
            raise HTTPException(403, "You cannot grant a role equal to or above your own")
        fields["role"] = role
    if body.status:
        st = body.status.upper()
        if st not in ("ACTIVE", "INACTIVE"):
            conn.close()
            raise HTTPException(400, "Status must be ACTIVE or INACTIVE")
        fields["status"] = st
    if not fields:
        conn.close()
        raise HTTPException(400, "Nothing to update")

    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE users SET {cols} WHERE id=?", (*fields.values(), user_id))
    conn.commit()
    conn.close()
    log_audit(actor["email"], "USER_UPDATE", "user", user_id, json.dumps(fields))
    return {"success": True}


@router.get("/api/audit")
def audit(limit: int = 200, user=Depends(require_role("ADMIN"))):
    conn = get_db()
    rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/api/alerts/status")
def alert_status(user=Depends(require_role("ADMIN"))):
    return alerts.channels_status()


@router.post("/api/alerts/test")
def alert_test(user=Depends(require_role("ADMIN"))):
    alerts.send_test()
    log_audit(user["email"], "ALERT_TEST")
    return {"success": True, "message": "Test alert sent to all configured channels"}


@router.get("/api/events")
async def events(user=Depends(get_current_user)):
    """Server-Sent Events: new incidents appear on every open dashboard instantly."""
    q = bus.subscribe()

    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    target = ev.get("to")
                    if target and target != str(user["email"]).lower():
                        continue          # a personal notification for someone else
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})