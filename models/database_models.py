"""SQLAlchemy models, all in the `bobobidou` Postgres schema.

The `bobobidou_app` Postgres role the backend connects with only has access to
this schema.
"""
import enum
from datetime import datetime

from sqlalchemy import Boolean, Column, String, DateTime, Date, ForeignKey, Integer, Text, Enum, MetaData
from sqlalchemy.orm import declarative_base, relationship


SCHEMA = "bobobidou"

Base = declarative_base(metadata=MetaData(schema=SCHEMA))


class AuthProvider(enum.Enum):
    """Login providers: only Google for now."""
    GOOGLE = "GOOGLE"


class User(Base):
    __tablename__ = "users"

    id = Column(String, primary_key=True)
    email = Column(String, nullable=True, index=True)
    username = Column(String, nullable=True)
    display_name = Column(String, nullable=True)
    profile_picture_url = Column(String, nullable=True)
    primary_provider = Column(Enum(AuthProvider, name="authprovider", schema=SCHEMA), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RefreshToken(Base):
    """Refresh tokens issued by the backend."""
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    access_token = Column(Text, nullable=False)
    token_type = Column(String, default="refresh")
    expires_in = Column(Integer, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    scope = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Kept as "true"/"false" strings: the table was created by my-backend this way
    is_active = Column(String, default="true")

    user = relationship("User")


class AuthState(Base):
    """OAuth state parameters (CSRF protection) of the Google login flows."""
    __tablename__ = "auth_states"

    state = Column(String, primary_key=True)
    app_name = Column(String, nullable=False)
    provider = Column(Enum(AuthProvider, name="authprovider", schema=SCHEMA), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)


class PhotoUsage(Base):
    """Photos analysed per day, by user (HMAC of the email) and globally.

    No foreign key to `users` on purpose: the counters must survive an account
    deletion, otherwise deleting and recreating one's account resets the limit.
    """
    __tablename__ = "photo_usage"

    subject = Column(String(64), primary_key=True)  # HMAC-SHA256 hex of the email, or "global"
    day = Column(Date, primary_key=True)  # UTC
    count = Column(Integer, nullable=False, default=0, server_default="0")


class PhotoTrial(Base):
    """Free photos used by each person (HMAC of the email), once in a lifetime.

    No foreign key to `users`, like PhotoUsage: deleting and recreating one's
    account does not give a new trial.
    """
    __tablename__ = "photo_trials"

    subject = Column(String(64), primary_key=True)  # Same HMAC as PhotoUsage.subject
    used = Column(Integer, nullable=False, default=0, server_default="0")
    last_used_at = Column(DateTime(timezone=True), nullable=False)


class Subscription(Base):
    """Subscription state of a RevenueCat customer, kept up to date by its webhooks.

    Keyed by the RevenueCat app user id (an HMAC of the email, see
    subscription_service), with no foreign key to `users`: a paid period
    survives an account deletion.
    """
    __tablename__ = "subscriptions"

    subscriber = Column(String(64), primary_key=True)
    # End of the paid period (grace period included); None: no entitlement
    expires_at = Column(DateTime(timezone=True), nullable=True)
    will_renew = Column(Boolean, nullable=False, default=False, server_default="false")
    product_id = Column(String, nullable=True)
    store = Column(String, nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False)
