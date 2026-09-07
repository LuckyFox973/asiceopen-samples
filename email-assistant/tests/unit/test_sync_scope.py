"""What a sync pass is allowed to skip.

The bug these guard against: the operator moves the mailbox's start date back a
few years to fetch older mail, the sync reports success, and not one older
message arrives — because a completed initial pass sends every later run down
the history cursor, which only ever moves forward.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app import cli
from app.db.models import SyncState
from app.services.sync import needs_full_pass

JANUARY = date(2026, 1, 1)
EARLIER = date(2019, 1, 1)
LATER = date(2026, 6, 1)


def caught_up(**overrides) -> SyncState:
    """A mailbox with a finished pass and a usable history cursor."""
    state = SyncState(
        last_history_id=12345,
        initial_sync_completed_at=datetime(2026, 9, 1, tzinfo=UTC),
        initial_sync_page_token=None,
        initial_sync_start_date=JANUARY,
    )
    for name, value in overrides.items():
        setattr(state, name, value)
    return state


class TestNeedsFullPass:
    def test_a_mailbox_with_no_state_is_walked(self):
        assert needs_full_pass(None, JANUARY) is True

    def test_an_unfinished_first_pass_is_walked(self):
        assert needs_full_pass(caught_up(initial_sync_completed_at=None), JANUARY) is True

    def test_a_missing_history_cursor_is_walked(self):
        assert needs_full_pass(caught_up(last_history_id=None), JANUARY) is True

    def test_a_caught_up_mailbox_hops_the_history(self):
        assert needs_full_pass(caught_up(), JANUARY) is False

    def test_an_earlier_start_date_is_walked(self):
        """The whole point: older mail nobody has ever fetched."""
        assert needs_full_pass(caught_up(), EARLIER) is True

    def test_a_later_start_date_is_not_walked(self):
        """Narrowing the window owes nothing — that mail is already stored."""
        assert needs_full_pass(caught_up(), LATER) is False

    def test_a_walk_in_progress_is_finished_first(self):
        """A checkpointed page token outranks the stale completion stamp."""
        state = caught_up(initial_sync_page_token="page-2", initial_sync_start_date=EARLIER)
        assert needs_full_pass(state, EARLIER) is True

    def test_an_unknown_covered_date_is_not_guessed_at(self):
        """Re-walking years of mail on a guess is worse than doing nothing."""
        assert needs_full_pass(caught_up(initial_sync_start_date=None), EARLIER) is False


class TestAnnouncePlan:
    """A full pass is significant work; it gets said out loud, in advance."""

    @staticmethod
    def announce(capsys, state, requested, *, mode="auto", forced=False) -> str:
        cli._announce_plan(state, requested, mode=mode, forced=forced)
        return capsys.readouterr().out

    def test_a_first_walk_says_nothing(self, capsys):
        state = caught_up(initial_sync_completed_at=None)
        assert self.announce(capsys, state, JANUARY) == ""

    def test_a_caught_up_mailbox_says_nothing(self, capsys):
        assert self.announce(capsys, caught_up(), JANUARY) == ""

    def test_an_earlier_date_names_both_dates(self, capsys):
        out = self.announce(capsys, caught_up(), EARLIER)
        assert "2026-01-01" in out, "must say how far the last pass went"
        assert "2019-01-01" in out, "must say where this one starts"
        assert "not stored twice" in out

    def test_incremental_mode_says_nothing(self, capsys):
        assert self.announce(capsys, caught_up(), EARLIER, mode="incremental") == ""

    def test_full_announces_even_when_nothing_is_owed(self, capsys):
        out = self.announce(capsys, caught_up(), JANUARY, forced=True)
        assert "Full pass" in out


class TestSyncAnnouncesOnce:
    """Three passes of one run are one piece of work, not three."""

    def test_only_the_first_pass_announces(self, monkeypatch):
        seen: list[bool] = []
        outcomes = [("partial", None, None), ("partial", None, None), ("completed", None, None)]
        counts = {"new": 5, "updated": 0, "unchanged": 0, "attachments": 0, "threads": 0}

        def fake_pass(_args, announce=False):
            seen.append(announce)
            status, error, _ = outcomes[len(seen) - 1]
            return status, error, counts

        monkeypatch.setattr(cli, "_sync_once", fake_pass)
        args = cli.build_parser().parse_args(["sync", "hello@example.sk"])
        assert cli.cmd_sync(args) == 0
        assert seen == [True, False, False]


class TestFullFlag:
    def test_full_forces_the_initial_walk(self):
        args = cli.build_parser().parse_args(["sync", "hello@example.sk", "--full"])
        assert args.full is True
        assert args.mode == "auto", "--full must not need --mode as well"

    def test_start_date_arrives_as_a_date(self):
        args = cli.build_parser().parse_args(
            ["sync", "hello@example.sk", "--start-date", "2019-01-01"]
        )
        assert args.start_date == EARLIER

    def test_a_mistyped_start_date_is_a_sentence_not_a_traceback(self, capsys):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(
                ["sync", "hello@example.sk", "--start-date", "01/01/2019"]
            )
        assert "YYYY-MM-DD" in capsys.readouterr().err
