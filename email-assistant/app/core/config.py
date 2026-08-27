"""Application configuration.

All settings come from environment variables (or a local ``.env`` in
development).  Nothing secret is ever hard-coded or committed.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "staging", "production"]
AttachmentBackend = Literal["local", "gcs"]

# Minimum Gmail scopes for MVP 1.  Read-only: the assistant cannot send,
# delete or modify anything.  Write scopes are added deliberately in a later
# phase, together with the approval workflow (see docs/SECURITY.md).
GMAIL_SCOPES_READONLY: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.settings.basic",  # send-as aliases
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- General -----------------------------------------------------------
    app_env: AppEnv = "development"
    log_level: str = "INFO"
    timezone: str = "Europe/Bratislava"

    # --- Database ----------------------------------------------------------
    database_url: str = "postgresql+psycopg://eaa:devpassword@127.0.0.1:5432/email_assistant"
    test_database_url: str | None = None
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_echo: bool = False

    # --- Encryption --------------------------------------------------------
    token_encryption_key: str = ""

    # --- Google OAuth ------------------------------------------------------
    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"

    # --- Sync --------------------------------------------------------------
    sync_start_date: date = date(2026, 9, 1)
    sync_page_size: int = Field(default=100, ge=1, le=500)
    sync_max_messages_per_run: int = Field(default=2000, ge=1)

    # --- Attachments -------------------------------------------------------
    attachment_backend: AttachmentBackend = "local"
    attachment_local_path: str = "./data/attachments"
    attachment_gcs_bucket: str = ""
    attachment_max_bytes: int = 25 * 1024 * 1024

    # --- Internal job auth -------------------------------------------------
    job_auth_token: str = ""

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def gmail_scopes(self) -> list[str]:
        return list(GMAIL_SCOPES_READONLY)

    def oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
