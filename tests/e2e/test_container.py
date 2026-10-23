"""End-to-end tests of the production image, run by tests.yml against the container.

Real Postgres (migrated with alembic, backend connected as bobobidou_app), Google
and OpenAI replaced by stubs.py. Each test signs in its own user: the tests
don't depend on each other, and can be run again on the same database.
"""
import uuid

from tests.e2e.conftest import container_env, login, query_of

RUN = uuid.uuid4().hex[:8]
PHOTO = {"file": ("meal.png", b"\x89PNG\r\n\x1a\nnot really an image", "image/png")}


def code(name: str) -> str:
    return f"{name}-{RUN}"


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_health(api):
    response = api.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema(api):
    response = api.get("/openapi.json")
    assert response.status_code == 200
    assert "/bobobidou/ingredients" in response.json()["paths"]


def test_google_login_url(api):
    response = api.get("/login/google/bobobidou")
    assert response.status_code == 200
    params = query_of(response.json()["authorization_url"])
    assert params["client_id"] == "e2e-client-id"
    assert params["redirect_uri"] == "http://127.0.0.1:8080/auth/callback/bobobidou"
    assert params["state"]

    assert api.get("/login/google/other").status_code == 404


def test_auth_callback_redirects_to_the_app(api):
    response = api.get("/auth/callback/bobobidou", params={"code": "abc", "state": "xyz"})
    assert response.status_code == 307
    assert response.headers["location"] == "bobobidou://auth?code=abc&state=xyz"


def test_code_exchange(api, stub):
    tokens = login(api, code("alice"))
    assert tokens["user"]["email"] == f"{code('alice')}@example.com"
    assert tokens["user"]["provider"] == "GOOGLE"
    assert tokens["access_token"] and tokens["refresh_token"]

    sent = stub.received("/google/token")[-1][1].decode()
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A8080%2Fauth%2Fcallback%2Fbobobidou" in sent

    # Same user on the next sign-in
    assert login(api, code("alice"))["user"]["id"] == tokens["user"]["id"]


def test_code_exchange_refuses_a_used_state_and_a_bad_code(api):
    state = query_of(api.get("/login/google/bobobidou").json()["authorization_url"])["state"]
    params = {"code": "bad-code", "service": "GOOGLE", "state": state}
    assert api.get("/auth/exchange/bobobidou", params=params).status_code == 400
    # The state was consumed by the first attempt
    params["code"] = code("eve")
    assert api.get("/auth/exchange/bobobidou", params=params).status_code == 400


def test_refresh_token_rotation(api):
    tokens = login(api, code("bob"))
    response = api.post("/auth/refresh-token", headers={"X-Refresh-Token": tokens["refresh_token"]})
    assert response.status_code == 200
    new_tokens = response.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # The old one was deactivated
    response = api.post("/auth/refresh-token", headers={"X-Refresh-Token": tokens["refresh_token"]})
    assert response.status_code == 401
    # A refresh token is not an access token
    response = api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO,
                        headers={"Authorization": f"Bearer {new_tokens['refresh_token']}"})
    assert response.status_code == 401


def test_ingredients_and_daily_limit(api, stub):
    tokens = login(api, code("carol"))
    limit = int(container_env()["DAILY_PHOTO_LIMIT"])
    assert limit == 2

    assert api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO).status_code == 401

    response = api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO, headers=bearer(tokens))
    assert response.status_code == 200, response.text
    assert response.json() == {"ingredients": ["pâte", "farine", "tomate"]}
    assert b"data:image/png;base64," in stub.received("/openai/v1/responses")[-1][1]

    # The stub fails the English analyses: 502, and the photo is given back
    response = api.post("/bobobidou/ingredients", params={"language": "en"}, files=PHOTO, headers=bearer(tokens))
    assert response.status_code == 502

    response = api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO, headers=bearer(tokens))
    assert response.status_code == 200
    response = api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO, headers=bearer(tokens))
    assert response.status_code == 429
    assert response.json()["code"] == "daily_photo_limit"
    assert int(response.headers["Retry-After"]) > 0

    assert api.post("/bobobidou/ingredients", params={"language": "de"}, files=PHOTO,
                    headers=bearer(tokens)).status_code == 400


def test_delete_account_from_the_app(api):
    tokens = login(api, code("dave"))
    response = api.delete("/bobobidou/account", headers=bearer(tokens))
    assert response.status_code == 200
    assert response.json()["user_id"] == tokens["user"]["id"]

    assert api.delete("/bobobidou/account", headers=bearer(tokens)).status_code == 404
    response = api.post("/auth/refresh-token", headers={"X-Refresh-Token": tokens["refresh_token"]})
    assert response.status_code == 401


def test_delete_account_from_the_web_page(api, stub):
    tokens = login(api, code("erin"))
    for _ in range(2):
        assert api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO,
                        headers=bearer(tokens)).status_code == 200

    response = api.get("/account/delete")
    assert response.status_code == 200
    assert "Supprimer mon compte" in response.text

    response = api.get("/account/delete/start")
    assert response.status_code == 307
    params = query_of(response.headers["location"])
    assert params["redirect_uri"] == "http://127.0.0.1:8080/account/delete/callback"

    response = api.get("/account/delete/callback", params={"code": code("erin"), "state": params["state"]})
    assert response.status_code == 200, response.text
    assert "Confirmer la suppression" in response.text
    # The fresh Google token is revoked
    assert stub.received("/google/revoke")[-1][1] == f"token=google-{code('erin')}".encode()

    confirm_token = response.text.split('name="token" value="')[1].split('"')[0]
    response = api.post("/account/delete/confirm", data={"token": confirm_token})
    assert response.status_code == 200
    assert "Compte supprimé" in response.text

    # Deleted: the access token is refused...
    response = api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO, headers=bearer(tokens))
    assert response.status_code == 401
    # ...and a new account with the same Google account keeps the day's count
    tokens = login(api, code("erin"))
    response = api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO, headers=bearer(tokens))
    assert response.status_code == 429
