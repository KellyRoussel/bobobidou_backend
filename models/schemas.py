from typing import Optional

from pydantic import BaseModel


class GoogleUserInfo(BaseModel):
    email: Optional[str] = None
    name: Optional[str] = None
    picture: Optional[str] = None
    id: str  # Google `sub`
