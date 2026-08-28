"""FastAPI dependencies."""

from __future__ import annotations

import secrets
import uuid

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.security import extract_bearer
from app.db.models import ApiKey, MailboxAccount
from app.db.session import get_db
from app.services.access import verify_api_key
from app.services.accounts import get_account

SessionDep = Depends(get_db)
SettingsDep = Depends(get_settings)


def require_api_key(
    authorization: str | None = Header(default=None),
    session: Session = SessionDep,
    settings: Settings = SettingsDep,
) -> ApiKey | None:
    """Authenticate a caller by API key.

    Development stays open so local work needs no ceremony.  Production does
    not: an unauthenticated request is rejected, and there is no configuration
    that quietly disables the check.
    """
    if not settings.require_api_auth:
        return None

    token = extract_bearer(authorization)
    if not token:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing API key. Send 'Authorization: Bearer <key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    record = verify_api_key(session, token)
    if record is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid, expired or revoked API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return record


def get_account_or_404(account_id: uuid.UUID, session: Session = SessionDep) -> MailboxAccount:
    account = get_account(session, account_id)
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No mailbox {account_id}")
    return account


def require_job_token(
    x_job_token: str | None = Header(default=None),
    settings: Settings = SettingsDep,
) -> None:
    """Guard for endpoints Cloud Scheduler calls.

    Unset in development means "open"; in production an unset token is a
    configuration error, not permission to run unauthenticated.
    """
    if not settings.job_auth_token:
        if settings.is_production:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "JOB_AUTH_TOKEN must be configured in production",
            )
        return
    if not x_job_token or not secrets.compare_digest(x_job_token, settings.job_auth_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid job token")
