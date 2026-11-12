"""Phone / browser push alerts: key, subscribe, unsubscribe, test."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend import push
from backend.db import log_audit
from backend.security import get_current_user

router = APIRouter(tags=["push"])


class Keys(BaseModel):
    p256dh: str = Field(..., min_length=10, max_length=200)
    auth: str = Field(..., min_length=10, max_length=100)


class SubscribeBody(BaseModel):
    endpoint: str = Field(..., max_length=push.MAX_ENDPOINT)
    keys: Keys


class UnsubscribeBody(BaseModel):
    endpoint: str = Field(..., max_length=push.MAX_ENDPOINT)


@router.get("/api/push/key")
def push_key(user=Depends(get_current_user)):
    """available=false when pywebpush is not installed on the server."""
    if not push.AVAILABLE:
        return {"available": False, "public_key": None, "devices": 0}
    _, public = push.get_keys()
    return {"available": True, "public_key": public, "devices": push.count_for(user["email"])}


@router.post("/api/push/subscribe")
def push_subscribe(body: SubscribeBody, request: Request, user=Depends(get_current_user)):
    if not push.AVAILABLE:
        raise HTTPException(status_code=503, detail="Push is not set up on the server (pip install pywebpush)")
    if not push.valid_endpoint(body.endpoint):
        raise HTTPException(status_code=400, detail="This is not a browser push address")
    push.save_subscription(user["email"], body.endpoint, body.keys.p256dh, body.keys.auth,
                           request.headers.get("user-agent", ""))
    log_audit(user["email"], "PUSH_SUBSCRIBE", "push", "device", "")
    return {"success": True, "devices": push.count_for(user["email"])}


@router.post("/api/push/unsubscribe")
def push_unsubscribe(body: UnsubscribeBody, user=Depends(get_current_user)):
    push.remove_subscription(body.endpoint, email=user["email"])
    return {"success": True, "devices": push.count_for(user["email"])}


@router.post("/api/push/test")
def push_test(user=Depends(get_current_user)):
    if not push.AVAILABLE:
        raise HTTPException(status_code=503, detail="Push is not set up on the server (pip install pywebpush)")
    sent = push.send_to_emails([user["email"]], "AI Sentinel test", "Phone alerts are working.", url="/")
    if not sent:
        raise HTTPException(status_code=404, detail="No device of yours is subscribed, or the push service refused it")
    return {"success": True, "sent": sent}