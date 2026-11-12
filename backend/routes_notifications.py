"""The bell: a user's own notifications."""
from fastapi import APIRouter, Depends

from backend.db import get_db
from backend.security import get_current_user

router = APIRouter(tags=["notifications"])


@router.get("/api/notifications")
def my_notifications(user=Depends(get_current_user)):
    email = str(user["email"]).lower()
    conn = get_db()
    rows = conn.execute("SELECT * FROM notifications WHERE user_email=? ORDER BY id DESC LIMIT 30", (email,)).fetchall()
    unread = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_email=? AND is_read=0", (email,)).fetchone()[0]
    conn.close()
    return {"items": [dict(r) for r in rows], "unread": unread}


@router.post("/api/notifications/read")
def mark_read(user=Depends(get_current_user)):
    conn = get_db()
    conn.execute("UPDATE notifications SET is_read=1 WHERE user_email=? AND is_read=0", (str(user["email"]).lower(),))
    conn.commit()
    conn.close()
    return {"success": True}