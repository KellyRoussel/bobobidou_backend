# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

FastAPI backend of the Bobobidou mobile app (repo `KellyRoussel/Bobobidou`): Google sign-in with our own JWTs, ingredients extraction from meal photos via OpenAI vision (limited per day), and account deletion (API and public HTML pages). Deployed on Google Cloud Run. It was extracted from `KellyRoussel/my-backend` and keeps its routes, so the app only switches `BACKEND_URL`.

## Commands

```bash
poetry install                          # deps (dev included) in .venv
poetry run uvicorn main:app --reload    # dev server, needs a .env (see .env.example)
poetry run pytest                       # tests, no database needed
poetry run alembic upgrade head         # migrations, need MIGRATION_DATABASE_URL (admin)
poetry run alembic check                # models vs database
```

Dependencies are managed with Poetry (`pyproject.toml` + `poetry.lock`, both committed; `package-mode = false`). SQLAlchemy is kept on 2.0.x: 2.1 makes `postgresql://` default to psycopg 3, we use psycopg2. Deployment: `docs/deploiement-gcp.md`. The GCP infrastructure (service account, secrets, Artifact Registry, Cloud Run service config) is Terraform in `infra/` (state in the `bobobidou-tfstate` bucket); Terraform creates the secrets but never their values, and ignores the image. The code is deployed on each push to `main` by `.github/workflows/deploy.yml` (tests, then `gcloud run deploy --source .`, built from the `Dockerfile`; auth by Workload Identity Federation restricted to that workflow on main in the `production` environment, `infra/github.tf`; built as `bobobidou-builder`, never as the default Compute account, which is Editor; actions pinned by SHA), which only changes the image: config changes go through `infra/` + `terraform apply`. Migrations are not run by the workflow. `.github/workflows/tests.yml` runs the tests on each PR (no secrets). `.github/dependabot.yml` opens weekly PRs for pip/Poetry (minor + patch grouped, SQLAlchemy held on 2.0.x), Terraform providers and actions; the base image is not watched (floating `python:3.13-slim` tag).

## Architecture

- `main.py`: app and routers, `/health`. No CORS (mobile app and server-rendered pages only)
- `endpoints/`: `authentication.py` (Google login, code exchange, refresh), `bobobidou.py` (`/bobobidou/ingredients`, `DELETE /bobobidou/account`), `account_deletion.py` (HTML pages at `/account/delete`, Google sign-in → confirmation, the fresh Google token is revoked)
- `dependencies/`: `tokens.py` (JWT create/decode), `auth_handler.py` (Bearer dependency, sets `request.state.user_id`), `google_auth_service.py`, `photo_quota_service.py`, `account_service.py`
- `models/database_models.py`: SQLAlchemy models, all in the `bobobidou` Postgres schema
- `config.py`: pydantic `BaseSettings` from env / `.env`. `database_url.py` is kept apart so Alembic does not need the backend settings

## Key conventions

- The `{app}` path parameter of the auth routes is kept for compatibility with the app: only `bobobidou` is accepted (404 otherwise). The JWTs carry `app=bobobidou` and a `type` (`access` / `refresh`); only access tokens are accepted by `auth_handler`
- Users are matched by email on login, `users.id` is our own UUID. Google tokens are never stored
- Refresh tokens are stored and rotated (`is_active` is the string `"true"`/`"false"`, old ones deactivated on each issue)
- Photo limits (`photo_quota_service.py`): `DAILY_PHOTO_LIMIT` per user, `GLOBAL_DAILY_PHOTO_LIMIT` for everyone, UTC days, 429 with a `code`. Counted before calling OpenAI, refunded when the analysis fails. Counters are keyed by an HMAC (`SECRET_KEY`) of the lowercased email, not the user id (a new UUID at each account creation), with no FK to `users`: deleting and recreating an account does not reset the limit. `daily_photo_limit()` is where subscribers will get more
- All endpoints are async; external HTTP calls use `httpx.AsyncClient`, OpenAI uses `AsyncOpenAI`
- Database: Supabase Postgres **shared with my-backend**, `NullPool`. The backend connects as `bobobidou_app`, a role limited to the `bobobidou` schema, without DDL rights
- Migrations: this repo owns the `bobobidou` schema (version table `bobobidou.alembic_version`, `include_name` limited to the schema), my-backend owns `public` and ignores `bobobidou`. `0001_baseline` is the schema as created by my-backend (stamped, not run, on the existing database). Migrations run from a workstation with `MIGRATION_DATABASE_URL` (admin), never at container start; new tables get the role's rights from the schema's default privileges
- User-facing docs (README, deployment) are in French, code and comments in English
