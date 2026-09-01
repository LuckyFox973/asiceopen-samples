"""Application configuration.

All settings come from environment variables (or a local ``.env`` in
development).  Nothing secret is ever hard-coded or committed.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "staging", "production"]
AttachmentBackend = Literal["local", "gcs"]
BackupBackend = Literal["local", "gdrive"]

# Exactly one mail scope is requested, chosen by what is enabled — see
# Settings.gmail_scopes.  These three always accompany it.
GMAIL_SCOPES_COMMON: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.settings.basic",  # send-as aliases
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
)

# Read only: the assistant is not merely restrained from changing the mailbox,
# it is not authorised to.
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"

# Kept for tests and for anything that still refers to the read-only set.
GMAIL_SCOPES_READONLY: tuple[str, ...] = (GMAIL_READONLY_SCOPE, *GMAIL_SCOPES_COMMON)

# Added only when Drive backups are enabled.  Grants access to files this
# application creates and to nothing else in the user's Drive.
DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
# Restricted.  Needed only to write into folders the user already owns:
# drive.file reaches nothing the application did not itself create, so a
# pre-existing folder is invisible to it and an upload there fails 404.
DRIVE_FULL_SCOPE = "https://www.googleapis.com/auth/drive"
# Not restricted: creating a reminder for oneself is not sensitive data.
TASKS_SCOPE = "https://www.googleapis.com/auth/tasks"

# Labels, archive, trash/untrash, drafts, send.  Everything the assistant needs
# to act on a mailbox — except bypassing the trash.
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"

# The restricted scope, and the only one that permanently deletes.  Requested
# solely when permanent deletion is explicitly enabled: with gmail.modify a
# "delete" is a move to trash, which Google keeps for 30 days and which
# untrash reverses.  Holding an irreversible-delete token for a mailbox of
# privileged correspondence is a real risk, so it is never the default.
GMAIL_FULL_SCOPE = "https://mail.google.com/"


# app/core/config.py -> app/core -> app -> the project root, which is where
# .env lives beside pyproject.toml.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Both, in this order: the working directory first so a deployment can
        # override, then the project's own file.  A relative ".env" alone
        # means the settings depend on where the process happened to be
        # started — fine from a shell, useless when the Claude desktop app
        # launches the MCP server from a directory of its choosing.
        env_file=(PROJECT_ROOT / ".env", ".env"),
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

    # --- Gmail actions -------------------------------------------------------
    # Off by default: without it the OAuth grant carries no write permission at
    # all, so the assistant is not merely restrained from changing the mailbox
    # — it is not authorised to.
    gmail_write_enabled: bool = False
    # Bypassing the trash is irreversible.  Separate switch, separate scope,
    # and Google treats it as a restricted scope with stricter review.
    gmail_allow_permanent_delete: bool = False
    # Archive without asking, once you trust it.  Everything riskier always asks.
    gmail_auto_archive: bool = False
    # Labels the assistant may create and apply on its own.
    gmail_managed_label_prefix: str = "AI"

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
    # Documents parsed per cycle.  Bounded so a large backlog catches up over
    # several cycles instead of blocking one.
    extract_batch_size: int = Field(default=50, ge=1, le=1000)

    # --- OCR ---------------------------------------------------------------
    # Off by default because it needs two binaries this project does not
    # install; turning it on without them changes nothing but the message.
    ocr_enabled: bool = False
    # Slovak first, English second: a Slovak court document quoting an English
    # contract is the common case, and the order is the priority.
    ocr_languages: str = "slk+eng"
    # 300 dpi is what tesseract is tuned for; below 200 accuracy falls away
    # and above 400 the time doubles for nothing.
    ocr_dpi: int = Field(default=300, ge=72, le=1200)
    ocr_max_pages: int = Field(default=30, ge=1, le=2000)
    ocr_timeout_seconds: int = Field(default=120, ge=5, le=3600)
    ocr_batch_size: int = Field(default=10, ge=1, le=500)

    # --- Filing documents into Google Drive --------------------------------
    # Off by default, and deliberately hard to turn on by accident: it asks
    # for write access to the whole of the user's Drive, which is far wider
    # than anything else here.  Google treats it as a restricted scope; on a
    # Workspace account an Internal OAuth app may use it without review.
    drive_write_enabled: bool = False
    # Whether a resolved filing may run the moment it is proposed.  False
    # means it waits — which is the point: an invoice is filed after it has
    # been paid or booked, and only its recipient knows when that was.
    drive_auto_file: bool = False

    # --- Google Tasks ------------------------------------------------------
    # Writing a reminder to the user's own task list. Off until asked for,
    # like everything else that reaches outside the mailbox.
    tasks_enabled: bool = False
    # Which list to write into. Empty means the default one, which is what
    # the Tasks side panel shows.
    tasks_list: str = ""

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

        Drive is asked for only when something needs it, and at the narrowest
        level that works.  ``drive.file`` covers backups, because those are
        files this application creates.  Filing a document into a folder the
        user already made needs the full scope: ``drive.file`` cannot see a
        folder it did not create, so naming it as a parent fails.
        """
        # Only the narrowest scope that covers what is enabled: gmail.modify
        # already grants read, and mail.google.com already grants both, so
        # requesting them together would ask the user to approve more than the
        # application can actually use.
        if self.gmail_allow_permanent_delete:
            mail_scope = GMAIL_FULL_SCOPE
        elif self.gmail_write_enabled:
            mail_scope = GMAIL_MODIFY_SCOPE
        else:
            mail_scope = GMAIL_READONLY_SCOPE

        scopes = [mail_scope, *GMAIL_SCOPES_COMMON]
        # As with mail: one Drive scope, the narrowest that covers what is on.
        # The full scope supersedes drive.file, so asking for both would have
        # the consent screen overstate what the application can use.
        if self.drive_write_enabled:
            scopes.append(DRIVE_FULL_SCOPE)
        elif self.backup_enabled and self.backup_backend == "gdrive":
            scopes.append(DRIVE_FILE_SCOPE)
        if self.tasks_enabled:
            scopes.append(TASKS_SCOPE)
        return scopes

    def oauth_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
