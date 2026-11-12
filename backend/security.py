"""Password hashing, JWT tokens and role checks."""
from datetime import datetime, timedelta
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from backend import config
from backend.db import get_db

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

ROLE_RANK = {"USER": 1, "VIEWER": 1, "SUPERVISOR": 2, "ADMIN": 3, "SUPER_ADMIN": 4}
ASSIGNABLE_ROLES = ["VIEWER", "SUPERVISOR", "ADMIN", "SUPER_ADMIN"]


def create_token(user):
    payload = {
        "sub": str(user["id"]),
        "email": user["email"],
        "role": user["role"],
        "exp": datetime.utcnow() + timedelta(hours=config.JWT_EXPIRE_HOURS),
    }
    token = jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")
    return token.decode() if isinstance(token, bytes) else token


def decode_token(token):
    return jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer),
    token: Optional[str] = Query(None),
):
    """Bearer header, or ?token= (needed for <img>/<video>/EventSource which cannot send headers)."""
    raw = creds.credentials if creds else token
    if not raw:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        data = decode_token(raw)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired, please log in again")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    conn = get_db()
    row = conn.execute("SELECT id, name, email, role, status FROM users WHERE id=?",
                       (int(data["sub"]),)).fetchone()
    conn.close()
    if not row or str(row["status"]).upper() != "ACTIVE":
        raise HTTPException(status_code=401, detail="Account not available")
    return dict(row)


def require_role(minimum):
    need = ROLE_RANK[minimum]

    def checker(user=Depends(get_current_user)):
        if ROLE_RANK.get(str(user["role"]).upper(), 1) < need:
            raise HTTPException(status_code=403, detail=f"{minimum.title()} access required")
        return user

    return checker


def rank(role):
    return ROLE_RANK.get(str(role).upper(), 1)