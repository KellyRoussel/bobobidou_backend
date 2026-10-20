"""JWTs issued by the backend.

They carry an `app` claim set to `bobobidou`: the tokens issued by my-backend
for its other apps (same claim layout, other secret) are refused anyway.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

from config import settings

APP = "bobobidou"


def create_token(data: dict, type: str = "access", expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expires_delta = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    to_encode.update({"exp": datetime.now(timezone.utc) + expires_delta, "type": type, "app": APP})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict:
    """Check the signature and the expiration; raises jwt.PyJWTError."""
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
