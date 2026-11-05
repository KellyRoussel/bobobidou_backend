"""RevenueCat webhook: keeps `subscriptions` up to date.

RevenueCat sends the Authorization header configured in its dashboard
(REVENUECAT_WEBHOOK_AUTH). Whatever the event, the customer is read again from
the RevenueCat API: the events may arrive late, twice or out of order. A
non-2xx answer makes RevenueCat retry.
"""
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from dependencies.subscription_service import RevenueCatError, is_subscriber_id, sync_subscriber

logger = logging.getLogger(__name__)

revenuecat_router = APIRouter(prefix="/bobobidou/revenuecat", tags=["RevenueCat"], include_in_schema=False)


@revenuecat_router.post("/webhook")
async def revenuecat_webhook(request: Request, db: Session = Depends(get_db)):
    expected = settings.revenuecat_webhook_auth
    received = request.headers.get("Authorization", "")
    if not expected or not hmac.compare_digest(received.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid authorization")

    try:
        event = (await request.json())["event"]
        event_type = event.get("type")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid event")
    if event_type == "TEST":
        return {"status": "ok"}

    # A transfer moves the purchases from some customers to others: sync them all
    ids = {event.get("app_user_id"), event.get("original_app_user_id"),
           *(event.get("transferred_from") or []), *(event.get("transferred_to") or [])}
    ids = sorted(i for i in ids if isinstance(i, str) and is_subscriber_id(i))
    try:
        for app_user_id in ids:
            await sync_subscriber(db, app_user_id)
    except RevenueCatError as e:
        logger.error("RevenueCat webhook %s: sync failed: %s", event_type, e)
        raise HTTPException(status_code=503, detail="Sync failed, retry later")
    logger.info("RevenueCat webhook %s: %d subscriber(s) synced", event_type, len(ids))
    return {"status": "ok"}
