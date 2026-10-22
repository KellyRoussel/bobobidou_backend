"""Alembic environment configuration.

The migrations run with an admin connection (MIGRATION_DATABASE_URL), never with
the `bobobidou_app` role of the backend, which has no DDL rights.

Only the `bobobidou` schema is managed, and the version table lives in it
(`bobobidou.alembic_version`): the database is shared with my-backend, whose own
version table is `public.alembic_version`.
"""
import os
import sys
from logging.config import fileConfig

from sqlalchemy import create_engine, pool, text
from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Not through `config.settings`: the backend settings (SECRET_KEY…) are not needed to migrate
from database_url import sanitize_db_url  # noqa: E402
from models.database_models import Base, SCHEMA  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name == SCHEMA
    return True


def get_url() -> str:
    url = os.getenv("MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("MIGRATION_DATABASE_URL is not set (admin connection to the database)")
    return sanitize_db_url(url)


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_name=include_name,
        version_table_schema=SCHEMA,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(get_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        # The version table is created in the schema before the first migration runs
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=True,
            include_name=include_name,
            version_table_schema=SCHEMA,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
