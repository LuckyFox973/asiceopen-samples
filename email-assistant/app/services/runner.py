"""Wiring that turns a stored mailbox into a running sync.

Kept out of the API layer so the same entry point serves HTTP endpoints,
scheduled jobs and the CLI.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.crypto import get_cipher
from app.core.logging import get_logger
from app.db.models import MailboxAccount, SyncRun
from app.gmail.client import GmailClient
from app.gmail.oauth import credentials_from_stored, refresh_if_needed
from app.services.accounts import refresh_send_as_addresses
from app.services.storage import build_storage
from app.services.sync import SyncEngine

log = get_logger(__name__)


def build_client(
    session: Session, account: MailboxAccount, settings: Settings | None = None
) -> GmailClient:
    """Build an authenticated Gmail client, refreshing the token if needed."""
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
    return GmailClient(credentials)


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
