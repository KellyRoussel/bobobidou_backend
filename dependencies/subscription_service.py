"""Subscriptions, sold through Google Play and managed by RevenueCat.

The app identifies the customer to RevenueCat with an id computed here, an HMAC
of the user's email: like the photo counters, the subscription follows the
person and not the account (users.id is a new UUID at each account creation),
and RevenueCat never sees the email.

RevenueCat calls our webhook on each change; we then read the customer from its
API (the source of truth, whatever the order the events arrive in) and keep the
entitlement's end date in `subscriptions`. Access decisions only read this table.
"""
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from config import settings
from models.database_models import Subscription, User

logger = logging.getLogger(__name__)

# Rows of subscriptions ended for longer than this are purged
SUBSCRIPTION_RETENTION = timedelta(days=30)
_SUBSCRIBER_ID = re.compile(r"^[0-9a-f]{64}$")


class RevenueCatError(Exception):
    pass


def subscriber_id(user: User) -> str:
    """RevenueCat app user id of a user: HMAC of the lowercased email.

    A prefix distinct from the photo counters' one: the two keys can't be linked.
    """
    identity = user.email.strip().lower() if user.email else f"id:{user.id}"
    message = f"bobobidou-subscriber:{identity}".encode()
    return hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()


def is_subscriber_id(value: str) -> bool:
    """Only our ids are synced (not RevenueCat's anonymous ids, nor junk)."""
    return bool(_SUBSCRIBER_ID.match(value or ""))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Entitlement:
    expires_at: datetime | None
    will_renew: bool
    product_id: str | None
    store: str | None


def parse_entitlement(subscriber: dict) -> Entitlement:
    """The premium entitlement of a RevenueCat customer (API v1 `subscriber` object)."""
    entitlement = (subscriber.get("entitlements") or {}).get(settings.revenuecat_entitlement)
    if not entitlement:
        return Entitlement(None, False, None, None)
    product_id = entitlement.get("product_identifier")
    expires = [_parse_date(entitlement.get("expires_date")),
               _parse_date(entitlement.get("grace_period_expires_date"))]
    expires_at = max((d for d in expires if d), default=None)
    subscription = (subscriber.get("subscriptions") or {}).get(product_id) or {}
    will_renew = (subscription.get("unsubscribe_detected_at") is None
                  and subscription.get("refunded_at") is None
                  and expires_at is not None and expires_at > _now())
    return Entitlement(expires_at, will_renew, product_id, subscription.get("store"))


def _headers() -> dict:
    if not settings.revenuecat_secret_key:
        raise RevenueCatError("REVENUECAT_SECRET_KEY is not set")
    return {"Authorization": f"Bearer {settings.revenuecat_secret_key}", "Accept": "application/json"}


def _subscriber_url(app_user_id: str) -> str:
    return f"{settings.revenuecat_api_url.rstrip('/')}/subscribers/{quote(app_user_id, safe='')}"


async def fetch_subscriber(app_user_id: str) -> dict:
    """The RevenueCat customer (created by RevenueCat if it does not exist)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(_subscriber_url(app_user_id), headers=_headers())
    except httpx.HTTPError as e:
        raise RevenueCatError(f"RevenueCat unreachable: {e}") from e
    if response.status_code != 200:
        raise RevenueCatError(f"RevenueCat returned {response.status_code}")
    return response.json()["subscriber"]


def save_entitlement(db: Session, app_user_id: str, entitlement: Entitlement) -> None:
    values = {
        "expires_at": entitlement.expires_at,
        "will_renew": entitlement.will_renew,
        "product_id": entitlement.product_id,
        "store": entitlement.store,
        "updated_at": _now(),
    }
    stmt = insert(Subscription).values(subscriber=app_user_id, **values)
    stmt = stmt.on_conflict_do_update(index_elements=[Subscription.subscriber], set_=values)
    try:
        db.execute(stmt)
        db.commit()
    except Exception:
        db.rollback()
        raise


async def sync_subscriber(db: Session, app_user_id: str) -> Entitlement:
    """Read the customer from RevenueCat and store its entitlement."""
    entitlement = parse_entitlement(await fetch_subscriber(app_user_id))
    save_entitlement(db, app_user_id, entitlement)
    return entitlement


def get_subscription(db: Session, app_user_id: str) -> Subscription | None:
    return db.query(Subscription).filter(Subscription.subscriber == app_user_id).first()


def is_active(subscription: Subscription | None) -> bool:
    return bool(subscription and subscription.expires_at and subscription.expires_at > _now())


def is_premium(db: Session, user: User) -> bool:
    return is_active(get_subscription(db, subscriber_id(user)))


def purge_ended_subscriptions(db: Session) -> None:
    """Delete the rows of subscriptions ended for more than SUBSCRIPTION_RETENTION (no commit)."""
    limit = _now() - SUBSCRIPTION_RETENTION
    db.query(Subscription).filter(
        (Subscription.expires_at.is_(None) & (Subscription.updated_at < limit))
        | (Subscription.expires_at < limit)
    ).delete(synchronize_session=False)


# Google keeps an unpaid subscription in grace period then account hold for up
# to ~60 days after its expiry date, and recovers it if the payment goes through
RECOVERABLE_AFTER_EXPIRY = timedelta(days=60)


def _renewing_play_subscriptions(subscriber: dict) -> list[dict]:
    """The customer's Google Play subscriptions that may still be charged.

    Running ones, and expired ones in grace period / account hold (billing
    issue, the payment can still be recovered). Not those already cancelled
    by the user or refunded.
    """
    now = _now()
    renewing = []
    for sub in (subscriber.get("subscriptions") or {}).values():
        if (sub.get("store") != "play_store" or sub.get("unsubscribe_detected_at") is not None
                or sub.get("refunded_at") is not None):
            continue
        expires = _parse_date(sub.get("expires_date"))
        if expires is None:
            continue  # Not a subscription that renews
        on_hold = (sub.get("billing_issues_detected_at") is not None
                   and expires + RECOVERABLE_AFTER_EXPIRY > now)
        if expires > now or on_hold:
            renewing.append(sub)
    return renewing


async def cancel_renewal(db: Session, user: User) -> str:
    """Stop the renewal of the user's Google Play subscriptions (account deletion).

    The paid period is not refunded and stays usable until it ends. Never
    raises: returns "none" (nothing to cancel), "cancelled" or "failed"
    (the app then asks the user to cancel in Google Play).
    """
    app_user_id = subscriber_id(user)
    if not settings.revenuecat_secret_key and get_subscription(db, app_user_id) is None:
        # Subscriptions not set up (local development): nobody can have one
        return "none"
    try:
        subscriber = await fetch_subscriber(app_user_id)
        to_cancel = _renewing_play_subscriptions(subscriber)
        if not to_cancel:
            return "none"
        transaction_ids = [sub.get("store_transaction_id") for sub in to_cancel]
        if not all(transaction_ids):
            # Can't be cancelled without it: never report "none" for a subscription still charged
            raise RevenueCatError("Google Play subscription without store_transaction_id")
        async with httpx.AsyncClient(timeout=10) as client:
            for transaction_id in transaction_ids:
                url = f"{_subscriber_url(app_user_id)}/subscriptions/{quote(transaction_id, safe='')}/cancel"
                response = await client.post(url, headers=_headers())
                if response.status_code not in (200, 201):
                    raise RevenueCatError(f"RevenueCat cancel returned {response.status_code}")
    except Exception as e:
        logger.error("Subscription cancellation failed for subscriber %s: %s", app_user_id[:8], e)
        return "failed"

    logger.info("Cancelled the renewal of subscriber %s", app_user_id[:8])
    # The webhook will follow; store the new state right away. A failure here
    # doesn't change the outcome: the renewal is cancelled
    try:
        await sync_subscriber(db, app_user_id)
    except Exception as e:
        logger.warning("Sync after cancellation failed for subscriber %s: %s", app_user_id[:8], e)
    return "cancelled"
