"""JWTs issued by the backend.

They carry an `app` claim set to `bobobidou`: the tokens issued by my-backend
for its other apps (same claim layout, other secret) are refused anyway.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import uuid

import jwt

from config import settings

APP = "bobobidou"


def create_token(data: dict, type: str = "access", expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expires_delta = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    now = datetime.now(timezone.utc)
    # jti: `exp` is truncated to the second, two tokens issued in the same second
    # would otherwise be identical (and the refresh rotation would not rotate)
    to_encode.update({"iat": now, "exp": now + expires_delta, "type": type, "app": APP, "jti": uuid.uuid4().hex})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict:
    """Check the signature and the expiration; raises jwt.PyJWTError."""
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
