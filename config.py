from pydantic_settings import SettingsConfigDict, BaseSettings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Signs the JWTs and keys the photo counters (HMAC): changing it logs everyone
    # out and resets the daily limits
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    # Connection as the `bobobidou_app` role (schema `bobobidou` only).
    # The migrations use MIGRATION_DATABASE_URL instead (see alembic/env.py)
    database_url: str

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini-2024-07-18"
    max_image_mb: int = 10
    daily_photo_limit: int = 5  # Photos analysed per user and per day (UTC)
    global_daily_photo_limit: int = 500  # All users together, protects the OpenAI bill

    google_client_id: str = ""
    google_client_secret: str = ""
    google_auth_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_token_url: str = "https://oauth2.googleapis.com/token"
    google_user_info_url: str = "https://www.googleapis.com/oauth2/v3/userinfo"
    google_revoke_url: str = "https://oauth2.googleapis.com/revoke"
    # <backend>/auth/callback: the app name is appended (…/auth/callback/bobobidou)
    auth_redirect_uri: str = ""
    # Google redirect URI of the web account deletion page; defaults to
    # <backend>/account/delete/callback, derived from auth_redirect_uri
    account_deletion_redirect_uri: str = ""


settings = Settings()
