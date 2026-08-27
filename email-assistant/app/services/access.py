"""API keys and OAuth state — issuing, verifying, revoking."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.security import (
    display_prefix,
    generate_api_key,
    generate_oauth_state,
    hash_api_key,
    keys_match,
)
from app.db.models import ApiKey, AuditLog, OAuthState

# An authorisation flow a user does not finish within this window is stale.
OAUTH_STATE_TTL = timedelta(minutes=15)


@dataclass(slots=True)
class IssuedKey:
    """The only moment the plaintext key exists outside the caller's clipboard."""

    key: str
    record: ApiKey


def create_api_key(
    session: Session,
    name: str,
    expires_in_days: int | None = None,
    note: str | None = None,
) -> IssuedKey:
    key = generate_api_key()
    record = ApiKey(
        name=name,
        prefix=display_prefix(key),
        key_hash=hash_api_key(key),
        note=note,
        expires_at=(
            datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None
        ),
    )
    session.add(record)
    session.flush()
    session.add(
        AuditLog(
            occurred_at=datetime.now(UTC),
            actor="user",
            action="api_key.created",
            entity_type="api_key",
            entity_id=str(record.id),
            summary=f"API key {name!r} issued ({record.prefix}…)",
            automatic=False,
        )
    )
    session.flush()
    return IssuedKey(key=key, record=record)


def verify_api_key(session: Session, presented: str) -> ApiKey | None:
    """Return the matching usable key, or None.

    Looked up by hash so the comparison is a single indexed equality; the
    constant-time check then guards against a timing side channel on the
    remaining comparison.
    """
    if not presented:
        return None
    candidate_hash = hash_api_key(presented)
    record = session.scalar(select(ApiKey).where(ApiKey.key_hash == candidate_hash))
    if record is None:
        return None
    if not keys_match(candidate_hash, record.key_hash):  # pragma: no cover - defensive
        return None

    now = datetime.now(UTC)
    if not record.is_usable(now):
        return None

    record.last_used_at = now
    session.flush()
    return record


def list_api_keys(session: Session, include_revoked: bool = False) -> list[ApiKey]:
    stmt = select(ApiKey).order_by(ApiKey.created_at.desc())
    if not include_revoked:
        stmt = stmt.where(ApiKey.revoked_at.is_(None))
    return list(session.scalars(stmt).all())


def revoke_api_key(session: Session, prefix_or_id: str) -> ApiKey | None:
    record = None
    try:
        record = session.get(ApiKey, uuid.UUID(prefix_or_id))
    except ValueError:
        record = session.scalar(select(ApiKey).where(ApiKey.prefix == prefix_or_id))
    if record is None or record.revoked_at is not None:
        return record

    record.revoked_at = datetime.now(UTC)
    session.add(
        AuditLog(
            occurred_at=datetime.now(UTC),
            actor="user",
            action="api_key.revoked",
            entity_type="api_key",
            entity_id=str(record.id),
            summary=f"API key {record.name!r} revoked ({record.prefix}…)",
            automatic=False,
        )
    )
    session.flush()
    return record


# --- OAuth state ----------------------------------------------------------


def issue_oauth_state(session: Session) -> str:
    """Mint a state token for an authorisation flow that is starting."""
    purge_expired_oauth_states(session)
    state = generate_oauth_state()
    session.add(
        OAuthState(state=state, expires_at=datetime.now(UTC) + OAUTH_STATE_TTL)
    )
    session.flush()
    return state


def consume_oauth_state(session: Session, state: str | None) -> bool:
    """Verify and burn a state token. False means the callback is not ours."""
    if not state:
        return False
    record = session.scalar(select(OAuthState).where(OAuthState.state == state))
    now = datetime.now(UTC)
    if record is None or record.consumed_at is not None or record.expires_at <= now:
        return False
    record.consumed_at = now
    session.flush()
    return True


def purge_expired_oauth_states(session: Session) -> int:
    result = session.execute(
        delete(OAuthState).where(OAuthState.expires_at <= datetime.now(UTC))
    )
    return result.rowcount or 0
