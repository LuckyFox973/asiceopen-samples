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
BackupBackend = Literal["local", "gdrive"]

# Minimum Gmail scopes for MVP 1.  Read-only: the assistant cannot send,
# delete or modify anything.  Write scopes are added deliberately in a later
# phase, together with the approval workflow (see docs/SECURITY.md).
GMAIL_SCOPES_READONLY: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.settings.basic",  # send-as aliases
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
)

# Added only when Drive backups are enabled.  Grants access to files this
# application creates and to nothing else in the user's Drive.
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"


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

    # --- Backups -----------------------------------------------------------
    backup_enabled: bool = False
    backup_backend: BackupBackend = "local"
    backup_local_path: str = "./data/backups"
    backup_gdrive_folder: str = "EmailAssistantBackups"
    # Separate from TOKEN_ENCRYPTION_KEY on purpose: a backup key may have to
    # be shared with whoever performs a restore, without handing them the key
    # that unlocks live mailbox credentials.
    backup_encryption_key: str = ""
    backup_retention: int = Field(default=14, ge=1)
    backup_include_attachments: bool = False
    # Which mailbox's Google credentials to use for Drive uploads.
    # Empty means the first active mailbox.
    backup_account_email: str = ""

    # --- Local scheduler -----------------------------------------------------
    scheduler_sync_interval_minutes: int = Field(default=15, ge=1)
    scheduler_backup_hour: int = Field(default=3, ge=0, le=23)

    # --- Internal job auth -------------------------------------------------
    job_auth_token: str = ""

    # --- API authentication ------------------------------------------------
    # Unset means "off in development, on everywhere else"; see
    # require_api_auth below.  It cannot be switched off in production.
    api_auth_enabled: bool | None = None

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def require_api_auth(self) -> bool:
        """Whether callers must present an API key.

        Production always requires one: a deployment must not be able to
        expose a mailbox by forgetting a setting.
        """
        if self.app_env != "development":
            return True
        return bool(self.api_auth_enabled)

    @property
    def gmail_scopes(self) -> list[str]:
        """Scopes requested at consent.

        The Drive scope is added only when Drive backups are configured, and
        it is ``drive.file`` — access limited to files this application itself
        creates, never the rest of the Drive.
        """
        scopes = list(GMAIL_SCOPES_READONLY)
        if self.backup_enabled and self.backup_backend == "gdrive":
            scopes.append(DRIVE_FILE_SCOPE)
        return scopes

    def oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
