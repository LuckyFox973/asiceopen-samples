"""Mailbox account lifecycle: authorisation, aliases, lookup."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import get_cipher
from app.core.logging import get_logger
from app.db.models import AuditLog, MailboxAccount, MailboxAddress
from app.gmail.addresses import normalize_address
from app.gmail.oauth import AuthorisedIdentity

log = get_logger(__name__)


def get_account(session: Session, account_id: uuid.UUID) -> MailboxAccount | None:
    return session.get(MailboxAccount, account_id)


def get_account_by_email(session: Session, email: str) -> MailboxAccount | None:
    return session.scalar(
        select(MailboxAccount).where(MailboxAccount.email == normalize_address(email))
    )


def list_accounts(session: Session, active_only: bool = False) -> list[MailboxAccount]:
    stmt = select(MailboxAccount).order_by(MailboxAccount.email)
    if active_only:
        stmt = stmt.where(MailboxAccount.is_active.is_(True))
    return list(session.scalars(stmt).all())


def upsert_account_from_identity(session: Session, identity: AuthorisedIdentity) -> MailboxAccount:
    """Create or refresh a mailbox from a completed OAuth consent.

    Re-authorising an existing mailbox updates its tokens in place; it never
    creates a duplicate and never discards already-synced mail.
    """
    email = normalize_address(identity.email)
    if not email:
        raise ValueError("OAuth identity carried no e-mail address")

    cipher = get_cipher()
    account = get_account_by_email(session, email)
    created = account is None

    if account is None:
        account = MailboxAccount(email=email)
        session.add(account)

    account.display_name = identity.display_name or account.display_name
    account.google_sub = identity.google_sub or account.google_sub
    # Google only returns a refresh token on first consent (or with
    # prompt=consent); never overwrite a good one with None.
    if identity.refresh_token:
        account.oauth_refresh_token_enc = cipher.encrypt(identity.refresh_token)
    if identity.access_token:
        account.oauth_access_token_enc = cipher.encrypt(identity.access_token)
    account.oauth_token_expiry = identity.token_expiry
    account.oauth_scopes = identity.scopes or account.oauth_scopes
    account.authorised_at = datetime.now(UTC)
    account.is_active = True
    session.flush()

    add_address(session, account, email, is_primary=True, source="primary")

    session.add(
        AuditLog(
            occurred_at=datetime.now(UTC),
            actor="user",
            action="account.authorised" if created else "account.reauthorised",
            entity_type="mailbox_account",
            entity_id=str(account.id),
            account_id=account.id,
            summary=f"OAuth consent completed for {email}",
            details={"scopes": identity.scopes},
            automatic=False,
        )
    )
    session.flush()
    return account


def add_address(
    session: Session,
    account: MailboxAccount,
    address: str,
    is_primary: bool = False,
    source: str = "manual",
    display_name: str | None = None,
) -> MailboxAddress | None:
    """Register an address as mine. Idempotent."""
    normalized = normalize_address(address)
    if not normalized or "@" not in normalized:
        return None

    existing = session.scalar(
        select(MailboxAddress).where(
            MailboxAddress.account_id == account.id,
            MailboxAddress.address == normalized,
        )
    )
    if existing is not None:
        if is_primary and not existing.is_primary:
            existing.is_primary = True
        if display_name and not existing.display_name:
            existing.display_name = display_name
        session.flush()
        return existing

    record = MailboxAddress(
        account_id=account.id,
        address=normalized,
        is_primary=is_primary,
        source=source,
        display_name=display_name,
    )
    session.add(record)
    session.flush()
    return record


def refresh_send_as_addresses(session: Session, account: MailboxAccount, client) -> int:
    """Import the mailbox's send-as aliases so all my domains are recognised."""
    try:
        entries = client.list_send_as()
    except Exception as exc:  # noqa: BLE001 - aliases are useful, not essential
        log.warning("account.send_as_failed", account=account.email, error=str(exc))
        return 0

    added = 0
    for entry in entries:
        address = entry.get("sendAsEmail")
        if not address:
            continue
        before = len(account.addresses)
        add_address(
            session,
            account,
            address,
            is_primary=bool(entry.get("isPrimary")),
            source="send_as",
            display_name=entry.get("displayName") or None,
        )
        session.refresh(account)
        if len(account.addresses) > before:
            added += 1
    return added


def owned_addresses(account: MailboxAccount) -> list[str]:
    return [a.address for a in account.addresses]
