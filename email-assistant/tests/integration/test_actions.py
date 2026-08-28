"""What the assistant may do to a mailbox, and what it must ask about first."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import (
    ActionStatus,
    ActionType,
    AuditLog,
    RiskTier,
)
from app.services.actions import (
    ActionError,
    ActionRequest,
    ApprovalRequiredError,
    approve,
    execute,
    expire_stale,
    history,
    pending,
    propose,
    propose_and_maybe_execute,
    reject,
    risk_tier,
    runs_without_asking,
)
from tests.conftest import requires_db
from tests.fixtures.fake_gmail import FakeGmailActions

pytestmark = [pytest.mark.integration, requires_db]


def settings(**overrides) -> Settings:
    base = {"gmail_write_enabled": True, "_env_file": None}
    base.update(overrides)
    return Settings(**base)


def request_for(action_type: ActionType, **kwargs) -> ActionRequest:
    return ActionRequest(
        action_type=action_type,
        description=kwargs.pop("description", f"{action_type.value} a message"),
        gmail_target_id=kwargs.pop("gmail_target_id", "gmail-msg-1"),
        **kwargs,
    )


@pytest.fixture
def gmail():
    return FakeGmailActions()


class TestRiskTiers:
    @pytest.mark.parametrize(
        ("action", "tier"),
        [
            (ActionType.LABEL_ADD, RiskTier.AUTOMATIC),
            (ActionType.DRAFT_CREATE, RiskTier.AUTOMATIC),
            (ActionType.UNTRASH, RiskTier.AUTOMATIC),
            (ActionType.ARCHIVE, RiskTier.CONFIGURABLE),
            (ActionType.TRASH, RiskTier.APPROVAL),
            (ActionType.SEND, RiskTier.APPROVAL),
            (ActionType.DELETE_PERMANENT, RiskTier.APPROVAL),
        ],
    )
    def test_tiers_match_the_specification(self, action, tier):
        assert risk_tier(action) is tier

    def test_no_setting_promotes_a_destructive_action(self):
        """The point of the approval tier: configuration cannot escape it."""
        permissive = settings(gmail_auto_archive=True, gmail_allow_permanent_delete=True)
        for action in (ActionType.TRASH, ActionType.SEND, ActionType.DELETE_PERMANENT):
            assert runs_without_asking(action, permissive) is False

    def test_archive_becomes_automatic_only_when_switched_on(self):
        assert runs_without_asking(ActionType.ARCHIVE, settings()) is False
        assert runs_without_asking(ActionType.ARCHIVE, settings(gmail_auto_archive=True)) is True


class TestWriteDisabled:
    def test_nothing_can_be_proposed_without_write_access(self, db_session, account):
        with pytest.raises(ActionError, match="read-only"):
            propose(
                db_session,
                account,
                request_for(ActionType.LABEL_ADD),
                settings(gmail_write_enabled=False),
            )

    def test_permanent_delete_needs_its_own_switch(self, db_session, account):
        with pytest.raises(ActionError, match="Permanent deletion is disabled"):
            propose(db_session, account, request_for(ActionType.DELETE_PERMANENT), settings())

    def test_permanent_delete_allowed_when_explicitly_enabled(self, db_session, account):
        action = propose(
            db_session,
            account,
            request_for(ActionType.DELETE_PERMANENT),
            settings(gmail_allow_permanent_delete=True),
        )
        assert action.status == ActionStatus.PENDING.value


class TestAutomaticTier:
    def test_a_label_is_applied_immediately(self, db_session, account, gmail):
        action = propose_and_maybe_execute(
            db_session,
            account,
            request_for(ActionType.LABEL_ADD, payload={"labels": ["AI/Spracovane"]}),
            gmail=gmail,
            settings=settings(),
        )
        assert action.status == ActionStatus.EXECUTED.value
        assert gmail.called("modify_labels")
        assert gmail.last("ensure_label")["name"] == "AI/Spracovane"

    def test_a_draft_is_written_without_asking(self, db_session, account, gmail):
        action = propose_and_maybe_execute(
            db_session,
            account,
            request_for(
                ActionType.DRAFT_CREATE,
                gmail_target_id=None,
                payload={
                    "to": ["klient@abc.sk"],
                    "subject": "Re: Danova kontrola",
                    "body": "Dobry den, podklady som prevzal.",
                },
            ),
            gmail=gmail,
            settings=settings(),
        )
        assert action.status == ActionStatus.EXECUTED.value
        assert gmail.last("create_draft")["to"] == ["klient@abc.sk"]
        assert action.result["draftId"]

    def test_an_automatic_action_is_still_recorded(self, db_session, account, gmail):
        action = propose_and_maybe_execute(
            db_session, account,
            request_for(ActionType.LABEL_ADD, payload={"labels": ["AI/X"]}),
            gmail=gmail, settings=settings(),
        )
        actions = {
            e.action
            for e in db_session.scalars(
                select(AuditLog).where(AuditLog.correlation_id == str(action.id))
            )
        }
        assert "action.proposed" in actions
        assert "action.executed.label_add" in actions


class TestApprovalTier:
    def test_trash_waits_and_touches_nothing(self, db_session, account, gmail):
        action = propose_and_maybe_execute(
            db_session, account, request_for(ActionType.TRASH), gmail=gmail,
            settings=settings(),
        )
        assert action.status == ActionStatus.PENDING.value
        assert gmail.calls == []

    def test_executing_without_approval_is_refused(self, db_session, account, gmail):
        action = propose(db_session, account, request_for(ActionType.TRASH), settings())
        with pytest.raises(ApprovalRequiredError, match="needs approval"):
            execute(db_session, action, gmail, settings())
        assert gmail.calls == []

    def test_approved_then_executed(self, db_session, account, gmail):
        action = propose(db_session, account, request_for(ActionType.TRASH), settings())
        approve(db_session, action.id)
        executed = execute(db_session, action, gmail, settings())

        assert executed.status == ActionStatus.EXECUTED.value
        assert gmail.called("trash")
        assert executed.undo_hint and "untrash" in executed.undo_hint

    def test_rejected_action_never_runs(self, db_session, account, gmail):
        action = propose(db_session, account, request_for(ActionType.TRASH), settings())
        reject(db_session, action.id, note="not this one")

        assert action.status == ActionStatus.REJECTED.value
        with pytest.raises(ActionError, match="nothing to execute"):
            execute(db_session, action, gmail, settings())
        assert gmail.calls == []

    def test_a_decision_cannot_be_taken_twice(self, db_session, account):
        action = propose(db_session, account, request_for(ActionType.TRASH), settings())
        approve(db_session, action.id)
        with pytest.raises(ActionError, match="already approved"):
            approve(db_session, action.id)

    def test_send_requires_a_draft_id(self, db_session, account, gmail):
        action = propose(
            db_session, account,
            request_for(ActionType.SEND, gmail_target_id=None, payload={}),
            settings(),
        )
        approve(db_session, action.id)
        result = execute(db_session, action, gmail, settings())
        assert result.status == ActionStatus.FAILED.value
        assert "draft" in result.error
        assert gmail.calls == []

    def test_decisions_are_audited_as_human(self, db_session, account):
        action = propose(db_session, account, request_for(ActionType.TRASH), settings())
        approve(db_session, action.id)
        entry = db_session.scalar(
            select(AuditLog).where(
                AuditLog.correlation_id == str(action.id),
                AuditLog.action == "action.approved",
            )
        )
        assert entry is not None
        assert entry.automatic is False
        assert entry.actor == "user"


class TestSystemLabelGuard:
    @pytest.mark.parametrize("label", ["TRASH", "INBOX", "SPAM", "trash"])
    def test_a_label_action_cannot_reach_a_system_label(
        self, db_session, account, gmail, label
    ):
        """Otherwise 'add a label' would be an unaudited trash button."""
        action = propose_and_maybe_execute(
            db_session, account,
            request_for(ActionType.LABEL_ADD, payload={"labels": [label]}),
            gmail=gmail, settings=settings(),
        )
        assert action.status == ActionStatus.FAILED.value
        assert "system label" in action.error
        assert not gmail.called("modify_labels")

    def test_archive_may_touch_inbox_through_its_own_operation(
        self, db_session, account, gmail
    ):
        action = propose_and_maybe_execute(
            db_session, account, request_for(ActionType.ARCHIVE), gmail=gmail,
            settings=settings(gmail_auto_archive=True),
        )
        assert action.status == ActionStatus.EXECUTED.value
        assert gmail.called("archive")


class TestFailureHandling:
    def test_a_gmail_failure_is_recorded_not_raised(self, db_session, account):
        gmail = FakeGmailActions(fail_on={"trash"})
        action = propose(db_session, account, request_for(ActionType.TRASH), settings())
        approve(db_session, action.id)
        result = execute(db_session, action, gmail, settings())

        assert result.status == ActionStatus.FAILED.value
        assert "simulated Gmail failure" in result.error

    def test_a_failure_is_audited(self, db_session, account):
        gmail = FakeGmailActions(fail_on={"archive"})
        action = propose(db_session, account, request_for(ActionType.ARCHIVE), settings())
        approve(db_session, action.id)
        execute(db_session, action, gmail, settings())

        entry = db_session.scalar(
            select(AuditLog).where(
                AuditLog.correlation_id == str(action.id),
                AuditLog.action == "action.failed",
            )
        )
        assert entry is not None
        assert entry.result == "failure"

    def test_an_action_without_a_target_fails_cleanly(self, db_session, account, gmail):
        action = propose(
            db_session, account,
            request_for(ActionType.TRASH, gmail_target_id=None), settings(),
        )
        approve(db_session, action.id)
        result = execute(db_session, action, gmail, settings())
        assert result.status == ActionStatus.FAILED.value


class TestQueue:
    def test_pending_lists_only_undecided_actions(self, db_session, account, gmail):
        waiting = propose(db_session, account, request_for(ActionType.TRASH), settings())
        decided = propose(db_session, account, request_for(ActionType.SEND), settings())
        reject(db_session, decided.id)

        ids = {a.id for a in pending(db_session)}
        assert waiting.id in ids
        assert decided.id not in ids

    def test_history_keeps_everything(self, db_session, account):
        action = propose(db_session, account, request_for(ActionType.TRASH), settings())
        reject(db_session, action.id)
        assert action.id in {a.id for a in history(db_session)}

    def test_a_stale_proposal_expires_instead_of_firing_later(
        self, db_session, account, gmail
    ):
        action = propose(db_session, account, request_for(ActionType.TRASH), settings())
        action.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.flush()

        assert expire_stale(db_session) >= 1
        assert action.status == ActionStatus.EXPIRED.value
        with pytest.raises(ActionError):
            approve(db_session, action.id)

    def test_approving_an_unknown_action_is_reported(self, db_session):
        with pytest.raises(ActionError, match="No action"):
            approve(db_session, uuid.uuid4())
