"""Google sign-in of the mobile app and JWT management.

The paths are the ones of my-backend (`/login/google/bobobidou`,
`/auth/exchange/bobobidou`…) so that the app only has to change its backend URL.
"""
from datetime import timedelta, datetime

import jwt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from starlette import status
from starlette.requests import Request
from starlette.responses import RedirectResponse

from config import settings
from database import get_db, SessionLocal
from dependencies.google_auth_service import google_auth_service
from dependencies.tokens import APP, create_token, decode_token
from models.database_models import AuthProvider, RefreshToken, User

authentication_router = APIRouter(tags=["Authentication"])


def check_app(app: str) -> str:
    """The `{app}` path parameter, kept for compatibility: only `bobobidou` exists."""
    if app != APP:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown app")
    return app


def issue_tokens(user_id: str, db: Session) -> dict:
    """Create an access and a refresh token, the refresh one replacing the previous ones in DB."""
    access_token = create_token({"sub": user_id}, type="access",
                                expires_delta=timedelta(minutes=settings.access_token_expire_minutes))
    refresh_token = create_token({"sub": user_id}, type="refresh",
                                 expires_delta=timedelta(days=settings.refresh_token_expire_days))

    expires_in = settings.refresh_token_expire_days * 86400
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id,
        RefreshToken.is_active == "true"
    ).update({"is_active": "false"})
    db.add(RefreshToken(
        user_id=user_id,
        access_token=refresh_token,
        token_type="refresh",
        expires_in=expires_in,
        expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
    ))
    db.commit()

    return {"access_token": access_token, "refresh_token": refresh_token, "token_type": "bearer"}


@authentication_router.get("/login/google/{app}")
async def get_google_auth_url(app: str = Depends(check_app), db: Session = Depends(get_db)):
    auth_url = await google_auth_service.get_auth_url(app, db)
    return {"authorization_url": auth_url}


# Google redirects here, we redirect back to the app (deep link) with the code
@authentication_router.get("/auth/callback/{app}")
async def auth_callback(request: Request, app: str = Depends(check_app)):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        return RedirectResponse(f"{app}://auth?error={error}")

    if not code:
        return RedirectResponse(f"{app}://auth?error=no_code")

    redirect_url = f"{app}://auth?code={code}"
    if state:
        redirect_url += f"&state={state}"

    return RedirectResponse(redirect_url)


@authentication_router.get("/auth/exchange/{app}")
async def exchange_code(
        code: str,
        service: str,
        state: str = None,
        app: str = Depends(check_app),
        db: Session = Depends(get_db)
):
    if service != AuthProvider.GOOGLE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid service. Must be 'GOOGLE'"
        )

    try:
        token_info = await google_auth_service.exchange_code_for_token(code, app, state, db)
        user_info = await google_auth_service.get_user_info(token_info)
        user_record = await google_auth_service.save_user(user_info, db)
        tokens = issue_tokens(user_record.id, db)

        return {
            **tokens,
            "user": {
                "id": user_record.id,
                "email": user_record.email,
                "name": user_record.display_name,
                "username": user_record.username,
                "picture": user_record.profile_picture_url,
                "provider": user_record.primary_provider.value
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"🔴 Error in token exchange: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication failed"
        )


@authentication_router.post("/auth/refresh-token")
async def get_refresh_token(request: Request):
    refresh_token = request.headers.get("X-Refresh-Token")
    if not refresh_token:
        raise HTTPException(status_code=400, detail="Missing refresh token")

    try:
        payload = decode_token(refresh_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Refresh token expired")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    user_id = payload.get("sub")
    if not user_id or payload.get("type") != "refresh" or payload.get("app") != APP:
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    with SessionLocal() as db:
        stored_token = db.query(RefreshToken).filter(
            RefreshToken.access_token == refresh_token,
            RefreshToken.token_type == "refresh"
        ).first()
        if not stored_token:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        # is_active is stored as the string "true"/"false": "false" is truthy
        if stored_token.is_active != "true":
            raise HTTPException(status_code=401, detail="Refresh token is inactive")

        if not db.query(User).filter(User.id == user_id).first():
            raise HTTPException(status_code=404, detail="User not found")

        tokens = issue_tokens(user_id, db)

    return tokens
