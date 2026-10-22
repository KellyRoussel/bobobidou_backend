"""Google sign-in. Google is only used to identify the user: its tokens are not stored."""
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Dict
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException
from sqlalchemy.orm import Session
from starlette import status

from config import settings
from models.database_models import User, AuthState, AuthProvider
from models.schemas import GoogleUserInfo


class GoogleAuthService:

    async def get_auth_url(self, app: str, db: Session, redirect_uri: str = None) -> str:
        # State stored in database for CSRF protection
        state = secrets.token_urlsafe(32)
        db.add(AuthState(
            state=state,
            app_name=app,
            provider=AuthProvider.GOOGLE,
            expires_at=datetime.utcnow() + timedelta(minutes=10),
        ))
        db.commit()

        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri or f"{settings.auth_redirect_uri}/{app}",
            "response_type": "code",
            "scope": "email profile",
            "access_type": "offline",
            "include_granted_scopes": "true",
            "state": state,
        }
        return f"{settings.google_auth_url}?{urlencode(params)}"

    async def exchange_code_for_token(self, code: str, app: str, state: str, db: Session, redirect_uri: str = None) -> Dict:
        auth_state = db.query(AuthState).filter(
            AuthState.state == state,
            AuthState.app_name == app,
            AuthState.provider == AuthProvider.GOOGLE,
            AuthState.expires_at > datetime.utcnow(),
        ).first() if state else None

        if not auth_state:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid or expired state parameter"
            )

        # A state is only used once
        db.delete(auth_state)
        db.commit()

        token_request_data = {
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri or f"{settings.auth_redirect_uri}/{app}",
            "grant_type": "authorization_code",
        }

        async with httpx.AsyncClient() as client:
            token_response = await client.post(settings.google_token_url, data=token_request_data)
            token_response_data = token_response.json()

        if "error" in token_response_data:
            print(f"🔴 Google token exchange error: {token_response_data}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Google authentication error: {token_response_data['error']}"
            )

        return token_response_data

    async def get_user_info(self, token_info: Dict) -> GoogleUserInfo:
        async with httpx.AsyncClient() as client:
            headers = {"Authorization": f"Bearer {token_info['access_token']}"}
            user_info_response = await client.get(settings.google_user_info_url, headers=headers)
            user_info = user_info_response.json()

        if "error" in user_info:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Error getting user info: {user_info['error']}"
            )

        return GoogleUserInfo(
            email=user_info["email"],
            name=user_info.get("name"),
            picture=user_info.get("picture"),
            id=user_info["sub"]
        )

    async def save_user(self, user_info: GoogleUserInfo, db: Session) -> User:
        """Create or update the user. users.id is our own UUID: users are matched by email."""
        user_record = db.query(User).filter(User.email == user_info.email).first()

        if user_record:
            user_record.display_name = user_info.name
            user_record.profile_picture_url = user_info.picture
            user_record.updated_at = datetime.utcnow()
        else:
            user_record = User(
                id=str(uuid.uuid4()),
                email=user_info.email,
                username=user_info.email.split('@')[0],
                display_name=user_info.name,
                profile_picture_url=user_info.picture,
                primary_provider=AuthProvider.GOOGLE,
            )
            db.add(user_record)

        db.commit()
        db.refresh(user_record)
        return user_record


google_auth_service = GoogleAuthService()
