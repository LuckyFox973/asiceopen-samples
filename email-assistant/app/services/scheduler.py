"""A scheduler that runs on your own machine.

Cloud Scheduler calling an HTTP endpoint is the plan for a server.  Until
there is one, this loop does the same job locally: sync every few minutes,
back up once a day.  Deliberately dependency-free — no broker, no worker
pool, nothing to run out of memory overnight.

Its one honest limitation: it only runs while the machine is on.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import session_scope
from app.services.accounts import list_accounts
from app.services.backup import create_backup, prune_backups
from app.services.runner import run_sync

log = get_logger(__name__)


@dataclass
class SchedulerStats:
    cycles: int = 0
    syncs_run: int = 0
    sync_failures: int = 0
    backups_run: int = 0
    backup_failures: int = 0
    messages_created: int = 0
    last_sync_at: datetime | None = None
    last_backup_on: date | None = None
    errors: list[str] = field(default_factory=list)


class Scheduler:
    """Runs sync on an interval and a backup once a day."""

    def __init__(
        self,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        wait: Callable[[float], bool] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._stop = threading.Event()
        # Waiting on the stop event rather than sleeping means Ctrl-C is acted
        # on immediately instead of after the remaining interval.  Injectable
        # so tests do not have to wait out real minutes.
        self._wait = wait or self._stop.wait
        self.stats = SchedulerStats()

    # --- lifecycle ---------------------------------------------------------

    def request_stop(self, *_args: object) -> None:
        log.info("scheduler.stopping")
        self._stop.set()

    def install_signal_handlers(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self.request_stop)

    def run_forever(self, max_cycles: int | None = None) -> SchedulerStats:
        interval = timedelta(minutes=self.settings.scheduler_sync_interval_minutes)
        log.info(
            "scheduler.started",
            sync_every_minutes=self.settings.scheduler_sync_interval_minutes,
            backup_hour=self.settings.scheduler_backup_hour,
            timezone=self.settings.timezone,
            backups=self.settings.backup_enabled,
        )
        while not self._stop.is_set():
            self.tick()
            if max_cycles is not None and self.stats.cycles >= max_cycles:
                break
            self._wait(interval.total_seconds())
        log.info("scheduler.stopped", cycles=self.stats.cycles)
        return self.stats

    # --- one pass ----------------------------------------------------------

    def tick(self) -> SchedulerStats:
        self.stats.cycles += 1
        self._sync_all()
        if self.settings.backup_enabled and self._backup_is_due():
            self._run_backup()
        return self.stats

    def _sync_all(self) -> None:
        try:
            with session_scope() as session:
                accounts = list_accounts(session, active_only=True)
        except Exception as exc:  # noqa: BLE001 - the loop must survive
            self._record_error("scheduler.accounts_failed", exc)
            return

        for account in accounts:
            # One session per mailbox: a failure on one must not roll back
            # work already committed for another.
            try:
                with session_scope() as session:
                    fresh = session.merge(account)
                    run = run_sync(session, fresh, mode="auto", settings=self.settings)
                    self.stats.syncs_run += 1
                    self.stats.messages_created += run.messages_created
                    self.stats.last_sync_at = self._clock()
                    log.info(
                        "scheduler.synced",
                        account=fresh.email,
                        status=run.status,
                        created=run.messages_created,
                        updated=run.messages_updated,
                    )
            except Exception as exc:  # noqa: BLE001
                self.stats.sync_failures += 1
                self._record_error(f"scheduler.sync_failed:{account.email}", exc)

    def _backup_is_due(self) -> bool:
        """True once per day, at or after the configured local hour."""
        local = self._clock().astimezone(self._zone())
        if local.hour < self.settings.scheduler_backup_hour:
            return False
        return self.stats.last_backup_on != local.date()

    def _run_backup(self) -> None:
        local_today = self._clock().astimezone(self._zone()).date()
        try:
            with session_scope() as session:
                artifact = create_backup(session, self.settings)
                removed = prune_backups(session, self.settings)
            self.stats.backups_run += 1
            self.stats.last_backup_on = local_today
            log.info(
                "scheduler.backed_up",
                name=artifact.name,
                size=artifact.size_bytes,
                pruned=len(removed),
            )
        except Exception as exc:  # noqa: BLE001
            self.stats.backup_failures += 1
            # Not marking the day done means the next cycle retries, rather
            # than silently skipping a day's backup after one bad moment.
            self._record_error("scheduler.backup_failed", exc)

    # --- helpers -----------------------------------------------------------

    def _zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.settings.timezone)
        except Exception:  # noqa: BLE001 - a bad tz must not stop the loop
            return ZoneInfo("UTC")

    def _record_error(self, label: str, exc: Exception) -> None:
        message = f"{label}: {exc}"
        log.error(label, error=str(exc))
        self.stats.errors.append(message)
        del self.stats.errors[:-50]
