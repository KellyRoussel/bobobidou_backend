from fastapi import HTTPException, Depends, Request
from fastapi.security import OAuth2PasswordBearer
from starlette import status

from dependencies.tokens import APP, decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def auth_handler(request: Request, token: str = Depends(oauth2_scheme)):
    """Accept only our access tokens; sets request.state.user_id."""
    if token is None:
        raise _unauthorized("Not authenticated")

    try:
        payload = decode_token(token)
    except Exception:
        raise _unauthorized("Invalid token")

    # Refresh tokens (valid 30 days) must only be used on /auth/refresh-token
    if payload.get("sub") is None or payload.get("type") != "access":
        raise _unauthorized("Invalid authentication credentials")
    if payload.get("app") != APP:
        raise _unauthorized("Token not valid for this app")

    request.state.user_id = payload["sub"]
    return True
