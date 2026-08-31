"""Deciding what the assistant may do to a mailbox, and recording that it did.

Three tiers, from the brief:

* **automatic** — safe and reversible: apply a label the assistant manages,
  write a draft, put something back that was archived or binned. These run
  immediately.
* **configurable** — archiving. Off by default; once ``GMAIL_AUTO_ARCHIVE`` is
  on, it joins the automatic tier.
* **approval** — sending, binning, permanent deletion. These never happen
  without an explicit yes, and no setting can move them out of this tier.

Every action, including the automatic ones, is written to ``pending_action``
before it runs and to ``audit_log`` after. The rule the brief set is the one
this module enforces: the assistant never makes a consequential change
quietly.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from googleapiclient.errors import HttpError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import (
    ActionStatus,
    ActionType,
    AuditLog,
    EmailMessage,
    EmailThread,
    MailboxAccount,
    PendingAction,
    RiskTier,
)
from app.gmail.actions import (
    ActionOutcome,
    GmailActions,
    UnsafeLabelError,
    describe_http_error,
)

log = get_logger(__name__)

# A proposal nobody answered should lapse, not fire three days later against a
# mailbox that has moved on.
DEFAULT_EXPIRY = timedelta(days=7)

# Which tier each action belongs to.  Approval-tier entries are not
# configurable — there is deliberately no setting that promotes them.
RISK_TIERS: dict[ActionType, RiskTier] = {
    ActionType.LABEL_ADD: RiskTier.AUTOMATIC,
    ActionType.DRAFT_CREATE: RiskTier.AUTOMATIC,
    ActionType.DRAFT_UPDATE: RiskTier.AUTOMATIC,
    ActionType.UNARCHIVE: RiskTier.AUTOMATIC,
    ActionType.UNTRASH: RiskTier.AUTOMATIC,
    ActionType.LABEL_REMOVE: RiskTier.CONFIGURABLE,
    ActionType.ARCHIVE: RiskTier.CONFIGURABLE,
    ActionType.DRIVE_UPLOAD: RiskTier.CONFIGURABLE,
    ActionType.TRASH: RiskTier.APPROVAL,
    ActionType.SEND: RiskTier.APPROVAL,
    ActionType.DELETE_PERMANENT: RiskTier.APPROVAL,
}


class ActionError(RuntimeError):
    """The action cannot be proposed or executed as asked."""


class ApprovalRequiredError(ActionError):
    """Raised when execution is attempted on an unapproved approval-tier action."""


# A Drive filing is carried out by a callable the caller supplies, taking the
# action's payload and returning the same outcome a Gmail call would.  Keeping
# it a type rather than an import leaves this module free of the Drive client.
DriveUpload = Callable[[dict], ActionOutcome]


@dataclass(slots=True)
class ActionRequest:
    action_type: ActionType
    description: str
    target_type: str = "message"
    target_id: uuid.UUID | None = None
    gmail_target_id: str | None = None
    payload: dict | None = None
    reason: str | None = None
    requested_by: str = "agent"


def risk_tier(action_type: ActionType) -> RiskTier:
    return RISK_TIERS.get(action_type, RiskTier.APPROVAL)


# A configurable action is released by its own setting.  One shared flag was
# fine while archiving was the only one; releasing a Drive upload because
# archiving had been allowed would be a different permission than the owner
# granted.
CONFIGURABLE_SETTING: dict[ActionType, str] = {
    ActionType.ARCHIVE: "gmail_auto_archive",
    ActionType.LABEL_REMOVE: "gmail_auto_archive",
    ActionType.DRIVE_UPLOAD: "drive_auto_file",
}


def runs_without_asking(action_type: ActionType, settings: Settings) -> bool:
    """Whether this action may execute the moment it is proposed."""
    tier = risk_tier(action_type)
    if tier is RiskTier.AUTOMATIC:
        return True
    if tier is RiskTier.CONFIGURABLE:
        # An unmapped configurable action waits, rather than inheriting
        # somebody else's permission by accident.
        setting = CONFIGURABLE_SETTING.get(action_type)
        return bool(setting and getattr(settings, setting, False))
    return False


# ---------------------------------------------------------------------------
# Proposing
# ---------------------------------------------------------------------------


def propose(
    session: Session,
    account: MailboxAccount,
    request: ActionRequest,
    settings: Settings | None = None,
) -> PendingAction:
    """Record an intended action. Does not execute it."""
    settings = settings or get_settings()

    if request.action_type is ActionType.DRIVE_UPLOAD:
        if not settings.drive_write_enabled:
            raise ActionError(
                "Filing to Drive is disabled. It needs write access to Drive, "
                "which is a wider permission than anything else here — set "
                "DRIVE_WRITE_ENABLED=true and re-run the consent flow."
            )
    elif not settings.gmail_write_enabled:
        # Only Gmail actions are stopped by the Gmail scope; a Drive upload
        # touches no mailbox and is not the mailbox's permission to withhold.
        raise ActionError(
            "Gmail write access is disabled. The mailbox was authorised with "
            "read-only scopes, so this cannot be carried out even if approved. "
            "Set GMAIL_WRITE_ENABLED=true and re-run the consent flow."
        )
    if (
        request.action_type is ActionType.DELETE_PERMANENT
        and not settings.gmail_allow_permanent_delete
    ):
        raise ActionError(
            "Permanent deletion is disabled. Moving to the bin is reversible "
            "and needs no extra permission; bypassing the bin requires "
            "GMAIL_ALLOW_PERMANENT_DELETE=true and a restricted Google scope."
        )

    action = PendingAction(
        account_id=account.id,
        action_type=request.action_type.value,
        risk_tier=risk_tier(request.action_type).value,
        status=ActionStatus.PENDING.value,
        target_type=request.target_type,
        target_id=request.target_id,
        gmail_target_id=request.gmail_target_id,
        description=request.description,
        reason=request.reason,
        payload=request.payload,
        requested_by=request.requested_by,
        expires_at=datetime.now(UTC) + DEFAULT_EXPIRY,
    )
    session.add(action)
    session.flush()

    _audit(
        session,
        action,
        "action.proposed",
        f"Proposed: {action.description}",
        automatic=request.requested_by != "user",
    )
    return action


def approve(session: Session, action_id: uuid.UUID, decided_by: str = "user") -> PendingAction:
    action = _load_open(session, action_id)
    action.status = ActionStatus.APPROVED.value
    action.decided_at = datetime.now(UTC)
    action.decided_by = decided_by
    session.flush()
    _audit(session, action, "action.approved", f"Approved: {action.description}", False)
    return action


def reject(
    session: Session, action_id: uuid.UUID, decided_by: str = "user", note: str | None = None
) -> PendingAction:
    action = _load_open(session, action_id)
    action.status = ActionStatus.REJECTED.value
    action.decided_at = datetime.now(UTC)
    action.decided_by = decided_by
    if note:
        action.error = note
    session.flush()
    _audit(session, action, "action.rejected", f"Rejected: {action.description}", False)
    return action


def _load_open(session: Session, action_id: uuid.UUID) -> PendingAction:
    action = session.get(PendingAction, action_id)
    if action is None:
        raise ActionError(f"No action {action_id}.")
    if action.status != ActionStatus.PENDING.value:
        raise ActionError(
            f"Action is already {action.status}; only a pending action can be decided."
        )
    if not action.is_open(datetime.now(UTC)):
        action.status = ActionStatus.EXPIRED.value
        session.flush()
        raise ActionError("That proposal has expired. Ask for it again.")
    return action


def expire_stale(session: Session) -> int:
    """Lapse proposals nobody answered."""
    now = datetime.now(UTC)
    stale = list(
        session.scalars(
            select(PendingAction).where(
                PendingAction.status == ActionStatus.PENDING.value,
                PendingAction.expires_at.isnot(None),
                PendingAction.expires_at <= now,
            )
        ).all()
    )
    for action in stale:
        action.status = ActionStatus.EXPIRED.value
    if stale:
        session.flush()
    return len(stale)


# ---------------------------------------------------------------------------
# Executing
# ---------------------------------------------------------------------------


def execute(
    session: Session,
    action: PendingAction,
    gmail: GmailActions,
    settings: Settings | None = None,
    upload: DriveUpload | None = None,
) -> PendingAction:
    """Carry out an action that is allowed to be carried out.

    *upload* is required only for a Drive filing, and is injected rather than
    built here so this module stays free of the Drive client.
    """
    settings = settings or get_settings()
    action_type = ActionType(action.action_type)

    if action.status == ActionStatus.PENDING.value:
        if not runs_without_asking(action_type, settings):
            raise ApprovalRequiredError(
                f"{action_type.value} needs approval before it can run. "
                f"Approve it with the action id {action.id}."
            )
    elif action.status != ActionStatus.APPROVED.value:
        raise ActionError(f"Action is {action.status}; nothing to execute.")

    payload = action.payload or {}
    target = action.gmail_target_id

    try:
        if action_type is ActionType.DRIVE_UPLOAD:
            if upload is None:
                raise ActionError("No Drive uploader was supplied for a filing action.")
            outcome = upload(payload)
        else:
            outcome = _dispatch(gmail, action_type, target, payload)
    except UnsafeLabelError as exc:
        return _fail(session, action, str(exc))
    except Exception as exc:  # noqa: BLE001 - recorded on the action, then reported
        detail = describe_http_error(exc) if isinstance(exc, HttpError) else str(exc)
        return _fail(session, action, detail)

    action.status = ActionStatus.EXECUTED.value
    action.executed_at = datetime.now(UTC)
    action.result = outcome.data or {"detail": outcome.detail}
    action.undo_hint = outcome.undo_hint
    session.flush()

    _audit(
        session,
        action,
        f"action.executed.{action_type.value}",
        f"Executed: {action.description} — {outcome.detail}",
        automatic=action.decided_at is None,
        result="success",
    )
    log.info("action.executed", type=action_type.value, target=target)
    return action


def _dispatch(gmail: GmailActions, action_type: ActionType, target: str | None, payload: dict):
    if not target and action_type not in {
        ActionType.DRAFT_CREATE,
        ActionType.SEND,
        ActionType.DRIVE_UPLOAD,
    }:
        raise ActionError("The action has no Gmail message id to act on.")

    match action_type:
        case ActionType.LABEL_ADD:
            return gmail.modify_labels(target, add=_label_ids(gmail, payload))
        case ActionType.LABEL_REMOVE:
            return gmail.modify_labels(target, remove=_label_ids(gmail, payload))
        case ActionType.ARCHIVE:
            return gmail.archive(target)
        case ActionType.UNARCHIVE:
            return gmail.unarchive(target)
        case ActionType.TRASH:
            return gmail.trash(target)
        case ActionType.UNTRASH:
            return gmail.untrash(target)
        case ActionType.DELETE_PERMANENT:
            return gmail.delete_permanently(target)
        case ActionType.DRAFT_CREATE:
            return gmail.create_draft(
                to=payload.get("to") or [],
                subject=payload.get("subject") or "",
                body=payload.get("body") or "",
                thread_id=payload.get("thread_id"),
                in_reply_to=payload.get("in_reply_to"),
                cc=payload.get("cc"),
                from_address=payload.get("from_address"),
            )
        case ActionType.SEND:
            draft_id = payload.get("draft_id")
            if not draft_id:
                raise ActionError("Sending needs the id of a draft to send.")
            return gmail.send_draft(draft_id)
    raise ActionError(f"No executor for {action_type.value}.")


def _label_ids(gmail: GmailActions, payload: dict) -> list[str]:
    """Resolve label names to ids, creating managed labels on demand."""
    names = payload.get("labels") or []
    if not names:
        raise ActionError("No labels given.")
    return [gmail.ensure_label(name) for name in names]


def _fail(session: Session, action: PendingAction, detail: str) -> PendingAction:
    action.status = ActionStatus.FAILED.value
    action.error = detail[:2000]
    action.executed_at = datetime.now(UTC)
    session.flush()
    _audit(
        session,
        action,
        "action.failed",
        f"Failed: {action.description} — {detail[:300]}",
        automatic=True,
        result="failure",
    )
    log.warning("action.failed", type=action.action_type, error=detail)
    return action


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------


def propose_and_maybe_execute(
    session: Session,
    account: MailboxAccount,
    request: ActionRequest,
    gmail: GmailActions | None = None,
    settings: Settings | None = None,
) -> PendingAction:
    """Propose; run it now if its tier allows, otherwise leave it waiting."""
    settings = settings or get_settings()
    action = propose(session, account, request, settings)
    if gmail is not None and runs_without_asking(request.action_type, settings):
        return execute(session, action, gmail, settings)
    return action


def pending(session: Session, limit: int = 50) -> list[PendingAction]:
    expire_stale(session)
    return list(
        session.scalars(
            select(PendingAction)
            .where(PendingAction.status == ActionStatus.PENDING.value)
            .order_by(PendingAction.created_at.desc())
            .limit(limit)
        ).all()
    )


def history(session: Session, limit: int = 50) -> list[PendingAction]:
    return list(
        session.scalars(
            select(PendingAction).order_by(PendingAction.created_at.desc()).limit(limit)
        ).all()
    )


def describe_target(session: Session, action: PendingAction) -> str:
    """A human-readable name for what the action touches."""
    if action.target_id is None:
        return action.gmail_target_id or "(no target)"
    if action.target_type == "thread":
        thread = session.get(EmailThread, action.target_id)
        return thread.subject or "(no subject)" if thread else str(action.target_id)
    message = session.get(EmailMessage, action.target_id)
    if message is None:
        return str(action.target_id)
    return f"{message.subject or '(no subject)'} — from {message.from_address}"


def _audit(
    session: Session,
    action: PendingAction,
    what: str,
    summary: str,
    automatic: bool,
    result: str = "success",
) -> None:
    session.add(
        AuditLog(
            occurred_at=datetime.now(UTC),
            actor="agent" if automatic else "user",
            action=what,
            entity_type="pending_action",
            entity_id=str(action.id),
            account_id=action.account_id,
            summary=summary,
            details={
                "action_type": action.action_type,
                "risk_tier": action.risk_tier,
                "target": action.gmail_target_id,
                "status": action.status,
            },
            result=result,
            automatic=automatic,
            correlation_id=str(action.id),
        )
    )
    session.flush()
