"""baseline

State of the `bobobidou` schema when the backend left my-backend (its
migrations b0b1d0a1c2d3 and c2d3e4f5a6b7): schema, `bobobidou_app` role,
tables users, refresh_tokens, auth_states and photo_usage.

On the existing database, which already has all this, don't run it: mark it as
applied with `alembic stamp 0001_baseline`. On a new database, `alembic upgrade head`.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001_baseline'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "bobobidou"
ROLE = "bobobidou_app"

auth_provider = postgresql.ENUM("GOOGLE", name="authprovider", schema=SCHEMA, create_type=False)


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.execute(f"CREATE TYPE {SCHEMA}.authprovider AS ENUM ('GOOGLE')")

    op.create_table(
        'users',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('email', sa.String(), nullable=True),
        sa.Column('username', sa.String(), nullable=True),
        sa.Column('display_name', sa.String(), nullable=True),
        sa.Column('profile_picture_url', sa.String(), nullable=True),
        sa.Column('primary_provider', auth_provider, nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA,
    )
    op.create_index('ix_bobobidou_users_email', 'users', ['email'], schema=SCHEMA)

    op.create_table(
        'refresh_tokens',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('access_token', sa.Text(), nullable=False),
        sa.Column('token_type', sa.String(), nullable=True),
        sa.Column('expires_in', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.Column('scope', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('is_active', sa.String(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], [f'{SCHEMA}.users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        schema=SCHEMA,
    )
    op.create_index('ix_bobobidou_refresh_tokens_user_id', 'refresh_tokens', ['user_id'], schema=SCHEMA)

    op.create_table(
        'auth_states',
        sa.Column('state', sa.String(), nullable=False),
        sa.Column('app_name', sa.String(), nullable=False),
        sa.Column('provider', auth_provider, nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('state'),
        schema=SCHEMA,
    )

    op.create_table(
        'photo_usage',
        sa.Column('subject', sa.String(length=64), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('count', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('subject', 'day'),
        schema=SCHEMA,
    )

    # Dedicated role: read/write on the bobobidou tables only, no DDL (the
    # migrations run with the admin user). LOGIN and password are set by hand.
    op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} NOLOGIN;
            END IF;
        END
        $$
    """)
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {SCHEMA} TO {ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {SCHEMA} TO {ROLE}")
    # Same rights on the tables the next migrations will create
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {ROLE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} GRANT USAGE, SELECT ON SEQUENCES TO {ROLE}")
    op.execute(f"ALTER ROLE {ROLE} SET search_path = {SCHEMA}")


def downgrade() -> None:
    # The role is kept (it may hold a password set by hand): DROP ROLE by hand if needed.
    # The schema too: it holds alembic_version
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} REVOKE ALL ON TABLES FROM {ROLE}")
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} REVOKE ALL ON SEQUENCES FROM {ROLE}")
    op.drop_table('photo_usage', schema=SCHEMA)
    op.drop_table('auth_states', schema=SCHEMA)
    op.drop_table('refresh_tokens', schema=SCHEMA)
    op.drop_table('users', schema=SCHEMA)
    op.execute(f"DROP TYPE {SCHEMA}.authprovider")
