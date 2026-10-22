from datetime import timedelta

import jwt

from config import settings
from dependencies.tokens import create_token


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_protected_route_requires_a_token(client):
    assert client.delete("/bobobidou/account").status_code == 401


def test_refresh_token_is_refused_as_access_token(client):
    token = create_token({"sub": "u1"}, type="refresh")
    assert client.delete("/bobobidou/account", headers=_bearer(token)).status_code == 401


def test_expired_token_is_refused(client):
    token = create_token({"sub": "u1"}, expires_delta=timedelta(minutes=-1))
    assert client.delete("/bobobidou/account", headers=_bearer(token)).status_code == 401


def test_token_of_another_app_is_refused(client):
    token = jwt.encode({"sub": "u1", "type": "access", "app": "other"}, settings.secret_key,
                       algorithm=settings.algorithm)
    response = client.delete("/bobobidou/account", headers=_bearer(token))
    assert response.status_code == 401


def test_unsupported_language_is_refused_before_anything_else(client):
    token = create_token({"sub": "u1"})
    response = client.post("/bobobidou/ingredients?language=de", headers=_bearer(token),
                           files={"file": ("meal.jpg", b"jpeg", "image/jpeg")})
    assert response.status_code == 400


def test_only_the_bobobidou_app_exists(client):
    assert client.get("/login/google/other").status_code == 404
    assert client.get("/auth/callback/other?code=c").status_code == 404


def test_callback_redirects_to_the_app(client):
    response = client.get("/auth/callback/bobobidou?code=abc&state=xyz", follow_redirects=False)
    assert response.headers["location"] == "bobobidou://auth?code=abc&state=xyz"


def test_refresh_requires_a_refresh_token(client):
    access = create_token({"sub": "u1"}, type="access")
    response = client.post("/auth/refresh-token", headers={"X-Refresh-Token": access})
    assert response.status_code == 401


def test_photo_counter_follows_the_email_not_the_account():
    from dependencies.photo_quota_service import _subject_hash
    from models.database_models import User

    first = User(id="uuid-1", email="Kelly@Example.com")
    recreated = User(id="uuid-2", email="kelly@example.com ")
    other = User(id="uuid-3", email="other@example.com")
    assert _subject_hash(first) == _subject_hash(recreated)
    assert _subject_hash(first) != _subject_hash(other)


def test_tokens_issued_in_the_same_second_differ():
    assert create_token({"sub": "u1"}, type="refresh") != create_token({"sub": "u1"}, type="refresh")


def test_callback_values_cannot_add_parameters_to_the_deep_link(client):
    response = client.get("/auth/callback/bobobidou?error=x%26code%3DATTACKER", follow_redirects=False)
    assert response.headers["location"] == "bobobidou://auth?error=x%26code%3DATTACKER"
