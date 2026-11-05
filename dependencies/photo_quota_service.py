"""Which photos a user may have analysed by OpenAI: free trial, then subscription.

- Not a subscriber: FREE_TRIAL_PHOTOS photos once in a lifetime (photo_trials)
- Subscriber: DAILY_PHOTO_LIMIT photos per day (UTC), against abuse (photo_usage)
- Everyone: GLOBAL_DAILY_PHOTO_LIMIT per day, protects the OpenAI bill

The counters are
keyed by an HMAC of the user's email, not by the user id (a new random UUID
each time an account is created), and have no link to `bobobidou.users`: they
survive an account deletion, so deleting and recreating one's account with the
same Google account does not reset the limit, and nothing ties them back to a
person once the account is gone. Daily rows older than USAGE_RETENTION_DAYS and
trial rows unused for TRIAL_RETENTION are purged.
"""
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from config import settings
from dependencies.subscription_service import is_premium, purge_ended_subscriptions
from models.database_models import PhotoTrial, PhotoUsage, User

logger = logging.getLogger(__name__)

# Key of the row counting all the users' photos
GLOBAL_SUBJECT = "global"
USAGE_RETENTION_DAYS = 7
TRIAL_RETENTION = timedelta(days=3 * 365)


class PhotoLimitReached(Exception):
    def __init__(self, scope: str, limit: int):
        super().__init__(f"{scope} photo limit reached ({limit})")
        self.scope = scope  # "trial" | "user" | "global"
        self.limit = limit


@dataclass(frozen=True)
class PhotoTicket:
    """A counted photo: what refund_photo needs to give it back."""
    subject: str
    day: date
    trial: bool  # Counted on the free trial, not on the subscriber's day


def _subject_hash(user: User) -> str:
    # The email is what identifies a user across accounts (see save_user)
    identity = user.email.strip().lower() if user.email else f"id:{user.id}"
    message = f"bobobidou-photo-usage:{identity}".encode()
    return hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()


def _today() -> date:
    return datetime.now(timezone.utc).date()


def next_reset() -> datetime:
    """When the counters of the day restart (next midnight UTC)."""
    return datetime.combine(_today() + timedelta(days=1), time.min, tzinfo=timezone.utc)


def daily_photo_limit(user_id: str) -> int:
    """Photos a subscriber may analyse per day."""
    return settings.daily_photo_limit


def trial_photos_left(user: User, db: Session) -> int:
    trial = db.query(PhotoTrial).filter(PhotoTrial.subject == _subject_hash(user)).first()
    return max(settings.free_trial_photos - (trial.used if trial else 0), 0)


def _increment_trial(db: Session, subject: str, limit: int) -> int | None:
    """Atomically use one free photo if some are left; None otherwise."""
    now = datetime.now(timezone.utc)
    stmt = insert(PhotoTrial).values(subject=subject, used=1, last_used_at=now)
    stmt = stmt.on_conflict_do_update(
        index_elements=[PhotoTrial.subject],
        set_={"used": PhotoTrial.used + 1, "last_used_at": now},
        where=PhotoTrial.used < limit,
    ).returning(PhotoTrial.used)
    return db.execute(stmt).scalar_one_or_none()


def _increment(db: Session, subject: str, day: date, limit: int) -> int | None:
    """Atomically add one photo to the subject's counter if it is under the limit.

    Returns the new count, or None if the limit is already reached.
    """
    stmt = insert(PhotoUsage).values(subject=subject, day=day, count=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=[PhotoUsage.subject, PhotoUsage.day],
        set_={"count": PhotoUsage.count + 1},
        where=PhotoUsage.count < limit,
    ).returning(PhotoUsage.count)
    return db.execute(stmt).scalar_one_or_none()


def consume_photo(user: User, db: Session) -> PhotoTicket:
    """Count one photo for the user and globally, or raise PhotoLimitReached.

    On the subscriber's day, or on the free trial. Nothing is counted when a
    limit is reached.
    """
    day = _today()
    subject = _subject_hash(user)
    global_limit = settings.global_daily_photo_limit
    try:
        trial = not is_premium(db, user)
        if trial:
            trial_limit = settings.free_trial_photos
            if trial_limit <= 0 or _increment_trial(db, subject, trial_limit) is None:
                db.rollback()
                raise PhotoLimitReached("trial", trial_limit)
        else:
            user_limit = daily_photo_limit(user.id)
            if user_limit <= 0 or _increment(db, subject, day, user_limit) is None:
                db.rollback()
                raise PhotoLimitReached("user", user_limit)
        global_count = _increment(db, GLOBAL_SUBJECT, day, global_limit) if global_limit > 0 else None
        if global_count is None:
            db.rollback()
            logger.warning("Bobobidou global daily photo limit reached (%s)", global_limit)
            raise PhotoLimitReached("global", global_limit)
        if global_count == 1:
            # First photo of the day: purge the old counters
            db.query(PhotoUsage).filter(
                PhotoUsage.day < day - timedelta(days=USAGE_RETENTION_DAYS)
            ).delete(synchronize_session=False)
            db.query(PhotoTrial).filter(
                PhotoTrial.last_used_at < datetime.now(timezone.utc) - TRIAL_RETENTION
            ).delete(synchronize_session=False)
            purge_ended_subscriptions(db)
        db.commit()
        return PhotoTicket(subject=subject, day=day, trial=trial)
    except PhotoLimitReached:
        raise
    except Exception:
        db.rollback()
        raise


def refund_photo(ticket: PhotoTicket, db: Session) -> None:
    """Give back a photo counted by consume_photo whose analysis failed (best effort).

    On the day it was counted, even if midnight passed during the analysis.
    """
    try:
        # The global counter, and the user's daily one if it was a subscriber's photo
        subjects = [GLOBAL_SUBJECT] if ticket.trial else [ticket.subject, GLOBAL_SUBJECT]
        db.query(PhotoUsage).filter(
            PhotoUsage.subject.in_(subjects),
            PhotoUsage.day == ticket.day,
            PhotoUsage.count > 0,
        ).update({"count": PhotoUsage.count - 1}, synchronize_session=False)
        if ticket.trial:
            db.query(PhotoTrial).filter(
                PhotoTrial.subject == ticket.subject,
                PhotoTrial.used > 0,
            ).update({"used": PhotoTrial.used - 1}, synchronize_session=False)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("Bobobidou photo refund failed: %s", e)
