"""Wiring that turns a stored mailbox into a running sync.

Kept out of the API layer so the same entry point serves HTTP endpoints,
scheduled jobs and the CLI.
"""

from __future__ import annotations

from google.oauth2.credentials import Credentials
from sqlalchemy.orm import Session

from app.core.config import GMAIL_FULL_SCOPE, GMAIL_MODIFY_SCOPE, Settings, get_settings
from app.core.crypto import get_cipher
from app.core.logging import get_logger
from app.db.models import MailboxAccount, SyncRun
from app.gmail.actions import GmailActions
from app.gmail.client import GmailClient
from app.gmail.oauth import credentials_from_stored, refresh_if_needed
from app.services.accounts import refresh_send_as_addresses
from app.services.storage import build_storage
from app.services.sync import SyncEngine

log = get_logger(__name__)


def build_credentials(
    session: Session, account: MailboxAccount, settings: Settings | None = None
) -> Credentials:
    """Usable Google credentials for one mailbox, refreshed if expired.

    Shared by every Google API this system talks to — Gmail, and Drive for
    backups — so a refreshed token is persisted once, in one place.
    """
    settings = settings or get_settings()
    credentials = credentials_from_stored(
        account.oauth_refresh_token_enc,
        account.oauth_access_token_enc,
        account.oauth_scopes,
        settings=settings,
    )
    if refresh_if_needed(credentials):
        cipher = get_cipher()
        account.oauth_access_token_enc = cipher.encrypt(credentials.token)
        account.oauth_token_expiry = credentials.expiry
        session.flush()
    return credentials


def build_client(
    session: Session, account: MailboxAccount, settings: Settings | None = None
) -> GmailClient:
    """Build an authenticated Gmail client, refreshing the token if needed."""
    return GmailClient(build_credentials(session, account, settings))


def build_actions(
    session: Session, account: MailboxAccount, settings: Settings | None = None
) -> GmailActions:
    """Write operations for one mailbox.

    Fails loudly when the stored grant carries no write scope, rather than
    letting Gmail refuse each call with an opaque 403 later.
    """
    from googleapiclient.discovery import build as build_service

    settings = settings or get_settings()
    scopes = account.oauth_scopes or []
    if not any(s in scopes for s in (GMAIL_MODIFY_SCOPE, GMAIL_FULL_SCOPE)):
        raise PermissionError(
            f"Mailbox {account.email} was authorised without write permission. "
            "Set GMAIL_WRITE_ENABLED=true and re-run 'python -m app.cli auth-url'."
        )
    credentials = build_credentials(session, account, settings)
    service = build_service("gmail", "v1", credentials=credentials, cache_discovery=False)
    return GmailActions(service)


def run_sync(
    session: Session,
    account: MailboxAccount,
    mode: str = "auto",
    download_attachments: bool = True,
    settings: Settings | None = None,
) -> SyncRun:
    """Synchronise one mailbox. *mode* is ``auto``, ``initial`` or ``incremental``."""
    settings = settings or get_settings()
    client = build_client(session, account, settings)

    # Aliases can change (a new domain, a new send-as); refresh them each run
    # so direction detection stays correct.
    refresh_send_as_addresses(session, account, client)
    session.refresh(account)

    engine = SyncEngine(
        session=session,
        account=account,
        client=client,
        storage=build_storage(settings) if download_attachments else None,
        default_start_date=settings.sync_start_date,
        page_size=settings.sync_page_size,
        max_messages_per_run=settings.sync_max_messages_per_run,
        download_attachments=download_attachments,
        max_attachment_bytes=settings.attachment_max_bytes,
    )

    log.info("sync.start", account=account.email, mode=mode)
    if mode == "initial":
        return engine.initial_sync()
    if mode == "incremental":
        return engine.incremental_sync()
    return engine.sync()
