"""The local scheduler: what it runs, when, and how it survives failures."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services import scheduler as scheduler_module
from app.services.scheduler import Scheduler

BRATISLAVA_0200 = datetime(2026, 8, 27, 0, 0, tzinfo=UTC)  # 02:00 local (CEST)
BRATISLAVA_0500 = datetime(2026, 8, 27, 3, 0, tzinfo=UTC)  # 05:00 local
NEXT_DAY_0500 = datetime(2026, 8, 28, 3, 0, tzinfo=UTC)


def settings(**overrides) -> Settings:
    base = {
        "app_env": "development",
        "timezone": "Europe/Bratislava",
        "scheduler_sync_interval_minutes": 15,
        "scheduler_backup_hour": 3,
        "backup_enabled": True,
        "_env_file": None,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def fake_world(monkeypatch):
    """Replace the database and the work the scheduler delegates to."""
    state = SimpleNamespace(
        accounts=[SimpleNamespace(email="a@x.sk"), SimpleNamespace(email="b@x.sk")],
        synced=[],
        backups=0,
        pruned=0,
        sync_error_for=set(),
        backup_error=False,
    )

    @contextlib.contextmanager
    def fake_session_scope():
        yield SimpleNamespace(merge=lambda obj: obj)

    def fake_list_accounts(_session, active_only=False):
        return state.accounts

    def fake_run_sync(_session, account, mode="auto", settings=None, **_kwargs):
        if account.email in state.sync_error_for:
            raise RuntimeError(f"boom for {account.email}")
        state.synced.append(account.email)
        return SimpleNamespace(
            status="completed", messages_created=2, messages_updated=0, kind=mode
        )

    def fake_create_backup(_session, _settings):
        if state.backup_error:
            raise RuntimeError("drive unreachable")
        state.backups += 1
        return SimpleNamespace(name="archive.eaabk", size_bytes=123)

    def fake_prune(_session, _settings):
        state.pruned += 1
        return []

    monkeypatch.setattr(scheduler_module, "session_scope", fake_session_scope)
    monkeypatch.setattr(scheduler_module, "list_accounts", fake_list_accounts)
    monkeypatch.setattr(scheduler_module, "run_sync", fake_run_sync)
    monkeypatch.setattr(scheduler_module, "create_backup", fake_create_backup)
    monkeypatch.setattr(scheduler_module, "prune_backups", fake_prune)
    return state


class TestSync:
    def test_tick_syncs_every_active_mailbox(self, fake_world):
        scheduler = Scheduler(settings(backup_enabled=False), clock=lambda: BRATISLAVA_0200)
        scheduler.tick()
        assert fake_world.synced == ["a@x.sk", "b@x.sk"]
        assert scheduler.stats.syncs_run == 2
        assert scheduler.stats.messages_created == 4

    def test_one_failing_mailbox_does_not_stop_the_others(self, fake_world):
        fake_world.sync_error_for = {"a@x.sk"}
        scheduler = Scheduler(settings(backup_enabled=False), clock=lambda: BRATISLAVA_0200)
        scheduler.tick()
        assert fake_world.synced == ["b@x.sk"]
        assert scheduler.stats.sync_failures == 1
        assert any("a@x.sk" in e for e in scheduler.stats.errors)

    def test_no_mailboxes_is_not_an_error(self, fake_world):
        fake_world.accounts = []
        scheduler = Scheduler(settings(backup_enabled=False), clock=lambda: BRATISLAVA_0200)
        scheduler.tick()
        assert scheduler.stats.errors == []

    def test_error_log_does_not_grow_without_bound(self, fake_world):
        fake_world.sync_error_for = {"a@x.sk", "b@x.sk"}
        scheduler = Scheduler(settings(backup_enabled=False), clock=lambda: BRATISLAVA_0200)
        for _ in range(40):
            scheduler.tick()
        assert len(scheduler.stats.errors) == 50


class TestBackupTiming:
    def test_not_due_before_the_configured_hour(self, fake_world):
        scheduler = Scheduler(settings(), clock=lambda: BRATISLAVA_0200)
        scheduler.tick()
        assert fake_world.backups == 0

    def test_due_at_or_after_the_hour(self, fake_world):
        scheduler = Scheduler(settings(), clock=lambda: BRATISLAVA_0500)
        scheduler.tick()
        assert fake_world.backups == 1
        assert fake_world.pruned == 1

    def test_runs_only_once_per_day(self, fake_world):
        scheduler = Scheduler(settings(), clock=lambda: BRATISLAVA_0500)
        scheduler.tick()
        scheduler.tick()
        scheduler.tick()
        assert fake_world.backups == 1

    def test_runs_again_the_next_day(self, fake_world):
        now = SimpleNamespace(value=BRATISLAVA_0500)
        scheduler = Scheduler(settings(), clock=lambda: now.value)
        scheduler.tick()
        now.value = NEXT_DAY_0500
        scheduler.tick()
        assert fake_world.backups == 2

    def test_disabled_backups_never_run(self, fake_world):
        scheduler = Scheduler(settings(backup_enabled=False), clock=lambda: BRATISLAVA_0500)
        scheduler.tick()
        assert fake_world.backups == 0

    def test_failed_backup_is_retried_on_the_next_cycle(self, fake_world):
        scheduler = Scheduler(settings(), clock=lambda: BRATISLAVA_0500)
        fake_world.backup_error = True
        scheduler.tick()
        assert scheduler.stats.backup_failures == 1
        assert scheduler.stats.last_backup_on is None

        fake_world.backup_error = False
        scheduler.tick()
        assert fake_world.backups == 1

    def test_an_unknown_timezone_falls_back_to_utc(self, fake_world):
        scheduler = Scheduler(settings(timezone="Mars/Olympus"), clock=lambda: BRATISLAVA_0500)
        scheduler.tick()
        assert fake_world.backups == 1

    def test_backup_failure_does_not_stop_syncing(self, fake_world):
        fake_world.backup_error = True
        scheduler = Scheduler(settings(), clock=lambda: BRATISLAVA_0500)
        scheduler.tick()
        assert fake_world.synced == ["a@x.sk", "b@x.sk"]


class TestLoopControl:
    def test_max_cycles_stops_the_loop(self, fake_world):
        waits: list[float] = []
        scheduler = Scheduler(
            settings(backup_enabled=False, scheduler_sync_interval_minutes=5),
            clock=lambda: BRATISLAVA_0200,
            wait=lambda seconds: waits.append(seconds) or False,
        )
        stats = scheduler.run_forever(max_cycles=3)
        # It waits between cycles, but not after the final one.
        assert waits == [300.0, 300.0]
        assert stats.cycles == 3
        assert len(fake_world.synced) == 6

    def test_stop_request_ends_the_loop(self, fake_world):
        scheduler = Scheduler(settings(backup_enabled=False), clock=lambda: BRATISLAVA_0200)
        scheduler.request_stop()
        assert scheduler.run_forever(max_cycles=5).cycles == 0

    def test_stop_during_the_wait_ends_the_loop(self, fake_world):
        scheduler = Scheduler(
            settings(backup_enabled=False),
            clock=lambda: BRATISLAVA_0200,
            wait=lambda _s: scheduler.request_stop(),
        )
        assert scheduler.run_forever(max_cycles=10).cycles == 1
