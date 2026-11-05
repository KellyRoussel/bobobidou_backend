"""free trial photos and subscriptions

Tables photo_trials (free photos used, by HMAC of the email) and subscriptions
(RevenueCat state, by subscriber id). The `bobobidou_app` role gets its rights
from the schema's default privileges (0001_baseline).

Revision ID: 0002_subscriptions
Revises: 0001_baseline
Create Date: 2026-11-02 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0002_subscriptions'
down_revision: Union[str, None] = '0001_baseline'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "bobobidou"


def upgrade() -> None:
    op.create_table(
        'photo_trials',
        sa.Column('subject', sa.String(length=64), nullable=False),
        sa.Column('used', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('subject'),
        schema=SCHEMA,
    )
    op.create_table(
        'subscriptions',
        sa.Column('subscriber', sa.String(length=64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('will_renew', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('product_id', sa.String(), nullable=True),
        sa.Column('store', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('subscriber'),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table('subscriptions', schema=SCHEMA)
    op.drop_table('photo_trials', schema=SCHEMA)
