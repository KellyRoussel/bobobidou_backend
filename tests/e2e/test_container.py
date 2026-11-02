"""End-to-end tests of the production image, run by tests.yml against the container.

Real Postgres (migrated with alembic, backend connected as bobobidou_app), Google
and OpenAI replaced by stubs.py. Each test signs in its own user: the tests
don't depend on each other, and can be run again on the same database.
"""
import uuid
from datetime import datetime, timedelta, timezone

from tests.e2e.conftest import container_env, login, query_of

RUN = uuid.uuid4().hex[:8]
PHOTO = {"file": ("meal.png", b"\x89PNG\r\n\x1a\nnot really an image", "image/png")}


def code(name: str) -> str:
    return f"{name}-{RUN}"


def bearer(tokens: dict) -> dict:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


WEBHOOK_AUTH = {"Authorization": container_env()["REVENUECAT_WEBHOOK_AUTH"]}


def subscribe(api, stub, tokens: dict, webhook: bool = True) -> str:
    """Make the user a subscriber in the RevenueCat double, then send the webhook."""
    app_user_id = api.get("/bobobidou/subscription", headers=bearer(tokens)).json()["app_user_id"]
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat().replace("+00:00", "Z")
    product = "bobobidou_premium_monthly:monthly"
    stub.subscribers[app_user_id] = {
        "original_app_user_id": app_user_id,
        "entitlements": {"premium": {"expires_date": expires, "grace_period_expires_date": None,
                                     "product_identifier": product}},
        "subscriptions": {product: {"expires_date": expires, "store": "play_store",
                                    "unsubscribe_detected_at": None, "refunded_at": None,
                                    "store_transaction_id": f"GPA.{app_user_id[:8]}"}},
    }
    if webhook:
        event = {"event": {"type": "INITIAL_PURCHASE", "app_user_id": app_user_id}}
        response = api.post("/bobobidou/revenuecat/webhook", json=event, headers=WEBHOOK_AUTH)
        assert response.status_code == 200, response.text
    return app_user_id


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


def test_ingredients_and_free_trial(api, stub):
    tokens = login(api, code("carol"))
    trial = int(container_env()["FREE_TRIAL_PHOTOS"])
    assert trial == 2

    assert api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO).status_code == 401

    status = api.get("/bobobidou/subscription", headers=bearer(tokens)).json()
    assert status["premium"] is False
    assert status["free_photos_left"] == status["free_photos_total"] == trial

    response = api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO, headers=bearer(tokens))
    assert response.status_code == 200, response.text
    assert response.json() == {"ingredients": ["pâte", "farine", "tomate"]}
    assert b"data:image/png;base64," in stub.received("/openai/v1/responses")[-1][1]

    # The stub fails the English analyses: 502, and the free photo is given back
    response = api.post("/bobobidou/ingredients", params={"language": "en"}, files=PHOTO, headers=bearer(tokens))
    assert response.status_code == 502
    assert api.get("/bobobidou/subscription", headers=bearer(tokens)).json()["free_photos_left"] == 1

    response = api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO, headers=bearer(tokens))
    assert response.status_code == 200
    response = api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO, headers=bearer(tokens))
    assert response.status_code == 402
    assert response.json()["code"] == "subscription_required"

    assert api.post("/bobobidou/ingredients", params={"language": "de"}, files=PHOTO,
                    headers=bearer(tokens)).status_code == 400


def test_subscriber_daily_limit(api, stub):
    tokens = login(api, code("frank"))
    limit = int(container_env()["DAILY_PHOTO_LIMIT"])
    assert limit == 3

    event = {"event": {"type": "RENEWAL", "app_user_id": "x"}}
    assert api.post("/bobobidou/revenuecat/webhook", json=event).status_code == 401
    assert api.post("/bobobidou/revenuecat/webhook", json=event,
                    headers={"Authorization": "Bearer wrong"}).status_code == 401

    subscribe(api, stub, tokens)
    status = api.get("/bobobidou/subscription", headers=bearer(tokens)).json()
    assert status["premium"] is True and status["will_renew"] is True
    assert status["expires_at"]

    for _ in range(limit):
        response = api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO,
                            headers=bearer(tokens))
        assert response.status_code == 200, response.text
    response = api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO, headers=bearer(tokens))
    assert response.status_code == 429
    assert response.json()["code"] == "daily_photo_limit"
    assert int(response.headers["Retry-After"]) > 0
    # The free trial was not used
    status = api.get("/bobobidou/subscription", headers=bearer(tokens)).json()
    assert status["free_photos_left"] == status["free_photos_total"]


def test_subscription_refresh_after_a_purchase(api, stub):
    tokens = login(api, code("gina"))
    # Bought in the app, the webhook has not arrived yet
    subscribe(api, stub, tokens, webhook=False)
    assert api.get("/bobobidou/subscription", headers=bearer(tokens)).json()["premium"] is False
    response = api.post("/bobobidou/subscription/refresh", headers=bearer(tokens))
    assert response.status_code == 200
    assert response.json()["premium"] is True


def test_delete_account_from_the_app(api, stub):
    tokens = login(api, code("dave"))
    app_user_id = subscribe(api, stub, tokens)
    response = api.delete("/bobobidou/account", headers=bearer(tokens))
    assert response.status_code == 200
    assert response.json()["user_id"] == tokens["user"]["id"]
    # The renewal is stopped in RevenueCat (Google Play)...
    assert response.json()["subscription"] == "cancelled"
    assert stub.received(f"/revenuecat/v1/subscribers/{app_user_id}/subscriptions/GPA.{app_user_id[:8]}/cancel")

    assert api.delete("/bobobidou/account", headers=bearer(tokens)).status_code == 404
    response = api.post("/auth/refresh-token", headers={"X-Refresh-Token": tokens["refresh_token"]})
    assert response.status_code == 401

    # ...and the paid period is still there for a new account with the same Google account
    tokens = login(api, code("dave"))
    status = api.get("/bobobidou/subscription", headers=bearer(tokens)).json()
    assert status["premium"] is True and status["will_renew"] is False


def test_delete_account_without_subscription(api):
    tokens = login(api, code("hugo"))
    response = api.delete("/bobobidou/account", headers=bearer(tokens))
    assert response.status_code == 200
    assert response.json()["subscription"] == "none"


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
    # ...and a new account with the same Google account gets no new free photos
    tokens = login(api, code("erin"))
    response = api.post("/bobobidou/ingredients", params={"language": "fr"}, files=PHOTO, headers=bearer(tokens))
    assert response.status_code == 402
