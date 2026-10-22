"""Daily limit of the photos analysed by OpenAI, to protect the bill.

Two counters per day (UTC): one per user and a global one. The usage rows are
keyed by an HMAC of the user's email, not by the user id (a new random UUID
each time an account is created), and have no link to `bobobidou.users`: they
survive an account deletion, so deleting and recreating one's account with the
same Google account does not reset the limit, and nothing ties them back to a
person once the account is gone. Rows older than USAGE_RETENTION_DAYS are purged.
"""
import hashlib
import hmac
import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from config import settings
from models.database_models import PhotoUsage, User

logger = logging.getLogger(__name__)

# Key of the row counting all the users' photos
GLOBAL_SUBJECT = "global"
USAGE_RETENTION_DAYS = 7


class PhotoLimitReached(Exception):
    def __init__(self, scope: str, limit: int):
        super().__init__(f"{scope} daily photo limit reached ({limit})")
        self.scope = scope  # "user" | "global"
        self.limit = limit


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
    """Photos a user may analyse per day. The place to give subscribers more."""
    return settings.daily_photo_limit


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


def consume_photo(user: User, db: Session) -> None:
    """Count one photo for the user and globally, or raise PhotoLimitReached.

    Nothing is counted when a limit is reached.
    """
    day = _today()
    user_limit = daily_photo_limit(user.id)
    global_limit = settings.global_daily_photo_limit
    try:
        if user_limit <= 0 or _increment(db, _subject_hash(user), day, user_limit) is None:
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
        db.commit()
    except PhotoLimitReached:
        raise
    except Exception:
        db.rollback()
        raise


def refund_photo(user: User, db: Session) -> None:
    """Give back a photo counted by consume_photo whose analysis failed (best effort)."""
    day = _today()
    try:
        db.query(PhotoUsage).filter(
            PhotoUsage.subject.in_([_subject_hash(user), GLOBAL_SUBJECT]),
            PhotoUsage.day == day,
            PhotoUsage.count > 0,
        ).update({"count": PhotoUsage.count - 1}, synchronize_session=False)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("Bobobidou photo refund failed: %s", e)
