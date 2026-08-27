"""Sync control and status."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import SessionDep, SettingsDep, get_account_or_404, require_job_token
from app.core.config import Settings
from app.db.models import (
    Attachment,
    EmailMessage,
    EmailThread,
    MailboxAccount,
    SyncRun,
    SyncState,
)
from app.schemas.common import (
    AccountOut,
    SyncRunOut,
    SyncStateOut,
    SyncStatusOut,
)
from app.services.accounts import list_accounts
from app.services.runner import run_sync

router = APIRouter(tags=["sync"])


def _account_out(account: MailboxAccount) -> AccountOut:
    return AccountOut(
        id=account.id,
        email=account.email,
        display_name=account.display_name,
        is_active=account.is_active,
        sync_start_date=account.sync_start_date,
        authorised_at=account.authorised_at,
        addresses=[a.address for a in account.addresses],
    )


@router.get("/accounts", response_model=list[AccountOut])
def get_accounts(session: Session = SessionDep) -> list[AccountOut]:
    return [_account_out(a) for a in list_accounts(session)]


@router.post("/accounts/{account_id}/sync", response_model=SyncRunOut)
def trigger_sync(
    mode: str = Query(default="auto", pattern="^(auto|initial|incremental)$"),
    download_attachments: bool = Query(default=True),
    account: MailboxAccount = Depends(get_account_or_404),
    session: Session = SessionDep,
    settings: Settings = SettingsDep,
) -> SyncRunOut:
    """Run a synchronisation now and return the result."""
    if not account.is_active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Mailbox is deactivated")
    try:
        run = run_sync(
            session,
            account,
            mode=mode,
            download_attachments=download_attachments,
            settings=settings,
        )
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Sync failed: {exc}") from exc
    return SyncRunOut.model_validate(run)


@router.get("/accounts/{account_id}/sync/status", response_model=SyncStatusOut)
def sync_status(
    account: MailboxAccount = Depends(get_account_or_404),
    session: Session = SessionDep,
) -> SyncStatusOut:
    state = session.scalar(select(SyncState).where(SyncState.account_id == account.id))
    runs = list(
        session.scalars(
            select(SyncRun)
            .where(SyncRun.account_id == account.id)
            .order_by(SyncRun.started_at.desc())
            .limit(10)
        ).all()
    )
    counts = {
        "threads": session.scalar(
            select(func.count(EmailThread.id)).where(EmailThread.account_id == account.id)
        )
        or 0,
        "messages": session.scalar(
            select(func.count(EmailMessage.id)).where(EmailMessage.account_id == account.id)
        )
        or 0,
        "attachments": session.scalar(
            select(func.count(Attachment.id)).where(Attachment.account_id == account.id)
        )
        or 0,
    }
    return SyncStatusOut(
        account=_account_out(account),
        state=(
            SyncStateOut(
                last_history_id=state.last_history_id,
                initial_sync_completed_at=state.initial_sync_completed_at,
                last_sync_at=state.last_sync_at,
                total_messages_synced=state.total_messages_synced,
                initial_sync_pending=state.initial_sync_completed_at is None,
            )
            if state
            else None
        ),
        last_runs=[SyncRunOut.model_validate(r) for r in runs],
        counts=counts,
    )


@router.post(
    "/jobs/sync-all",
    response_model=list[SyncRunOut],
    dependencies=[Depends(require_job_token)],
)
def sync_all_accounts(
    session: Session = SessionDep, settings: Settings = SettingsDep
) -> list[SyncRunOut]:
    """Endpoint for the scheduler: sync every active mailbox.

    One mailbox failing must not stop the others.
    """
    results: list[SyncRunOut] = []
    for account in list_accounts(session, active_only=True):
        try:
            run = run_sync(session, account, mode="auto", settings=settings)
            results.append(SyncRunOut.model_validate(run))
        except Exception:  # noqa: BLE001 - already recorded in sync_run/audit_log
            continue
    return results


@router.get("/sync/runs", response_model=list[SyncRunOut])
def recent_runs(
    account_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    session: Session = SessionDep,
) -> list[SyncRunOut]:
    stmt = select(SyncRun).order_by(SyncRun.started_at.desc()).limit(limit)
    if account_id:
        stmt = stmt.where(SyncRun.account_id == account_id)
    return [SyncRunOut.model_validate(r) for r in session.scalars(stmt).all()]
