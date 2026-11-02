from datetime import datetime, timedelta, timezone

from config import settings
from dependencies.photo_quota_service import _subject_hash
from dependencies.subscription_service import is_subscriber_id, parse_entitlement, subscriber_id
from models.database_models import User


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat().replace("+00:00", "Z")


def _subscriber(expires: str | None, **subscription) -> dict:
    product = "bobobidou_premium_monthly:monthly"
    return {
        "entitlements": {"premium": {"expires_date": expires, "grace_period_expires_date": None,
                                     "product_identifier": product}},
        "subscriptions": {product: {"expires_date": expires, "store": "play_store",
                                    "unsubscribe_detected_at": None, "refunded_at": None,
                                    "store_transaction_id": "GPA.1", **subscription}},
    }


def test_subscriber_id_follows_the_email_not_the_account():
    a = User(id="uuid-1", email="Kelly@Example.com ")
    b = User(id="uuid-2", email="kelly@example.com")
    assert subscriber_id(a) == subscriber_id(b)
    assert is_subscriber_id(subscriber_id(a))
    # Not the key of the photo counters
    assert subscriber_id(a) != _subject_hash(a)


def test_is_subscriber_id_refuses_anonymous_and_junk_ids():
    assert not is_subscriber_id("$RCAnonymousID:abc")
    assert not is_subscriber_id("")
    assert not is_subscriber_id("A" * 64)


def test_active_entitlement():
    entitlement = parse_entitlement(_subscriber(_iso(timedelta(days=10))))
    assert entitlement.expires_at > datetime.now(timezone.utc)
    assert entitlement.will_renew
    assert entitlement.store == "play_store"


def test_cancelled_entitlement_is_kept_until_it_expires():
    entitlement = parse_entitlement(_subscriber(_iso(timedelta(days=10)),
                                                unsubscribe_detected_at=_iso(timedelta(days=-1))))
    assert entitlement.expires_at > datetime.now(timezone.utc)
    assert not entitlement.will_renew


def test_grace_period_extends_the_entitlement():
    subscriber = _subscriber(_iso(timedelta(days=-1)))
    subscriber["entitlements"]["premium"]["grace_period_expires_date"] = _iso(timedelta(days=5))
    assert parse_entitlement(subscriber).expires_at > datetime.now(timezone.utc)


def test_no_entitlement():
    entitlement = parse_entitlement({"entitlements": {}, "subscriptions": {}})
    assert entitlement.expires_at is None and not entitlement.will_renew


def test_webhook_requires_the_configured_authorization(client, monkeypatch):
    event = {"event": {"type": "INITIAL_PURCHASE", "app_user_id": "x"}}
    monkeypatch.setattr(settings, "revenuecat_webhook_auth", "")
    assert client.post("/bobobidou/revenuecat/webhook", json=event,
                       headers={"Authorization": ""}).status_code == 401
    monkeypatch.setattr(settings, "revenuecat_webhook_auth", "Bearer good")
    assert client.post("/bobobidou/revenuecat/webhook", json=event,
                       headers={"Authorization": "Bearer bad"}).status_code == 401
    # A test event from the dashboard is accepted without touching anything
    response = client.post("/bobobidou/revenuecat/webhook", json={"event": {"type": "TEST"}},
                           headers={"Authorization": "Bearer good"})
    assert response.status_code == 200


def test_subscription_routes_require_a_token(client):
    assert client.get("/bobobidou/subscription").status_code == 401
    assert client.post("/bobobidou/subscription/refresh").status_code == 401
