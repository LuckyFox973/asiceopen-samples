"""Gmail synchronisation engine.

Two modes:

*initial*
    Walk ``users.messages.list`` restricted to ``after:<start date>``.  The
    page token is checkpointed so an interrupted run resumes from the page it
    was on rather than starting over.

*incremental*
    Walk ``users.history.list`` from the stored ``historyId``.  If Gmail has
    aged that history out (404), fall back to an initial-style pass — the
    ingest layer is idempotent, so re-walking costs time, never correctness.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import (
    AuditLog,
    MailboxAccount,
    SyncKind,
    SyncRun,
    SyncState,
    SyncStatus,
)
from app.gmail.addresses import OwnedAddressSet
from app.gmail.client import GmailApi, HistoryTooOldError, build_date_query
from app.gmail.parser import parse_and_resolve
from app.services.ingest import MessageIngestor
from app.services.storage import AttachmentStorage

log = get_logger(__name__)

# Chats are not mail and only add noise to a legal mailbox.
DEFAULT_QUERY_FILTER = "-in:chats"


@dataclass
class SyncStats:
    messages_seen: int = 0
    messages_created: int = 0
    messages_updated: int = 0
    messages_skipped: int = 0
    attachments_created: int = 0
    blobs_created: int = 0
    threads_touched: set[uuid.UUID] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)

    def merge_result(self, result) -> None:  # type: ignore[no-untyped-def]
        self.messages_seen += 1
        if result.created:
            self.messages_created += 1
        elif result.updated:
            self.messages_updated += 1
        else:
            self.messages_skipped += 1
        self.attachments_created += result.attachments_created
        self.blobs_created += result.blobs_created
        self.threads_touched.add(result.thread_id)


class SyncEngine:
    def __init__(
        self,
        session: Session,
        account: MailboxAccount,
        client: GmailApi,
        storage: AttachmentStorage | None = None,
        default_start_date: date | None = None,
        page_size: int = 100,
        max_messages_per_run: int = 2000,
        download_attachments: bool = True,
        max_attachment_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        self.session = session
        self.account = account
        self.client = client
        self.storage = storage
        self.default_start_date = default_start_date or date(2026, 9, 1)
        self.page_size = page_size
        self.max_messages_per_run = max_messages_per_run
        self.download_attachments = download_attachments

        owned = OwnedAddressSet([a.address for a in account.addresses])
        self.owned = owned
        self.ingestor = MessageIngestor(
            session=session,
            account=account,
            owned=owned,
            storage=storage,
            fetcher=client,  # type: ignore[arg-type]
            max_attachment_bytes=max_attachment_bytes,
        )

    # --- public API ---------------------------------------------------------

    @property
    def start_date(self) -> date:
        return self.account.sync_start_date or self.default_start_date

    def sync(self) -> SyncRun:
        """Run whichever mode is appropriate for the mailbox's current state."""
        state = self._get_or_create_state()
        if state.initial_sync_completed_at is None or state.last_history_id is None:
            return self.initial_sync()
        return self.incremental_sync()

    def initial_sync(self) -> SyncRun:
        state = self._get_or_create_state()
        run = self._start_run(SyncKind.INITIAL, start_history_id=state.last_history_id)
        stats = SyncStats()

        try:
            query = build_date_query(self.start_date, DEFAULT_QUERY_FILTER)
            page_token = state.initial_sync_page_token
            processed = 0
            reached_end = False

            while processed < self.max_messages_per_run:
                ids, next_token = self.client.list_message_ids(
                    query=query, page_token=page_token, page_size=self.page_size
                )
                for message_id in ids:
                    self._ingest_one(message_id, stats)
                    processed += 1

                # Checkpoint after every page so a crash costs one page at most.
                state.initial_sync_page_token = next_token
                state.total_messages_synced += len(ids)
                self.session.commit()

                if not next_token:
                    reached_end = True
                    break
                page_token = next_token
                if processed >= self.max_messages_per_run:
                    break

            if reached_end:
                state.initial_sync_page_token = None
                state.initial_sync_completed_at = datetime.now(UTC)
                # Anchor incremental sync at the mailbox's current historyId.
                profile = self.client.get_profile()
                history_id = profile.get("historyId")
                if history_id:
                    state.last_history_id = int(history_id)
                    run.end_history_id = state.last_history_id

            state.last_sync_at = datetime.now(UTC)
            self._finish_run(
                run,
                stats,
                SyncStatus.COMPLETED if reached_end else SyncStatus.PARTIAL,
            )
        except Exception as exc:
            self._fail_run(run, stats, exc)
            raise
        return run

    def incremental_sync(self) -> SyncRun:
        state = self._get_or_create_state()
        run = self._start_run(SyncKind.INCREMENTAL, start_history_id=state.last_history_id)
        stats = SyncStats()

        try:
            message_ids: list[str] = []
            page_token: str | None = None
            latest_history_id = state.last_history_id

            while True:
                records, next_token, history_id = self.client.list_history(
                    start_history_id=state.last_history_id or 0, page_token=page_token
                )
                if history_id:
                    latest_history_id = max(latest_history_id or 0, history_id)
                message_ids.extend(self._message_ids_from_history(records))
                if not next_token:
                    break
                page_token = next_token

            # Preserve order but drop repeats — one message may appear in
            # several history records (added, then labelled).
            for message_id in dict.fromkeys(message_ids):
                if stats.messages_seen >= self.max_messages_per_run:
                    break
                self._ingest_one(message_id, stats)

            if latest_history_id:
                state.last_history_id = latest_history_id
                run.end_history_id = latest_history_id
            state.last_sync_at = datetime.now(UTC)
            state.total_messages_synced += stats.messages_created
            self._finish_run(run, stats, SyncStatus.COMPLETED)

        except HistoryTooOldError as exc:
            log.warning(
                "sync.history_expired",
                account=self.account.email,
                last_history_id=state.last_history_id,
            )
            self._finish_run(run, stats, SyncStatus.PARTIAL, error=str(exc))
            # The stored cursor is useless; a date-bounded full pass rebuilds it.
            state.last_history_id = None
            state.initial_sync_completed_at = None
            state.initial_sync_page_token = None
            self.session.flush()
            return self.initial_sync()
        except Exception as exc:
            self._fail_run(run, stats, exc)
            raise
        return run

    # --- helpers ------------------------------------------------------------

    def _message_ids_from_history(self, records: list[dict]) -> list[str]:
        ids: list[str] = []
        for record in records:
            for key in ("messagesAdded", "labelsAdded", "labelsRemoved"):
                for entry in record.get(key, []) or []:
                    message = entry.get("message") or {}
                    if message.get("id"):
                        ids.append(message["id"])
        return ids

    def _ingest_one(self, message_id: str, stats: SyncStats) -> None:
        try:
            raw = self.client.get_message(message_id)
        except Exception as exc:  # noqa: BLE001 - skip the message, keep the run
            log.warning("sync.fetch_failed", message_id=message_id, error=str(exc))
            stats.errors.append(f"{message_id}: {exc}")
            return

        parsed = parse_and_resolve(raw, self.owned)

        if self._is_before_start_date(parsed.internal_date or parsed.sent_at):
            stats.messages_skipped += 1
            stats.messages_seen += 1
            return

        # A savepoint per message: one malformed message is rolled back on its
        # own and the rest of the batch survives.
        savepoint = self.session.begin_nested()
        try:
            result = self.ingestor.ingest(parsed, download_attachments=self.download_attachments)
            savepoint.commit()
        except Exception as exc:  # noqa: BLE001
            savepoint.rollback()
            log.warning("sync.ingest_failed", message_id=message_id, error=str(exc))
            stats.errors.append(f"{message_id}: {exc}")
            return
        stats.merge_result(result)

    def _is_before_start_date(self, moment: datetime | None) -> bool:
        """Enforce the configured cut-off even for messages history hands us."""
        if moment is None:
            return False
        boundary = datetime.combine(self.start_date, time.min, tzinfo=UTC)
        return moment < boundary

    def _get_or_create_state(self) -> SyncState:
        state = self.session.scalar(
            select(SyncState).where(SyncState.account_id == self.account.id)
        )
        if state is None:
            state = SyncState(account_id=self.account.id)
            self.session.add(state)
            self.session.flush()
        return state

    def _start_run(self, kind: SyncKind, start_history_id: int | None) -> SyncRun:
        run = SyncRun(
            account_id=self.account.id,
            kind=kind.value,
            status=SyncStatus.RUNNING.value,
            started_at=datetime.now(UTC),
            start_history_id=start_history_id,
        )
        self.session.add(run)
        # Committed immediately so a run in progress is visible to operators
        # and survives to be marked failed if the process dies mid-sync.
        self.session.commit()
        return run

    def _finish_run(
        self,
        run: SyncRun,
        stats: SyncStats,
        status: SyncStatus,
        error: str | None = None,
    ) -> None:
        run.status = status.value
        run.finished_at = datetime.now(UTC)
        run.messages_seen = stats.messages_seen
        run.messages_created = stats.messages_created
        run.messages_updated = stats.messages_updated
        run.messages_skipped = stats.messages_skipped
        run.threads_touched = len(stats.threads_touched)
        run.attachments_created = stats.attachments_created
        run.error = error
        run.details = {
            "blobs_created": stats.blobs_created,
            "errors": stats.errors[:50],
            "error_count": len(stats.errors),
        }
        self._audit(run, stats, status)
        self.session.commit()

    def _fail_run(self, run: SyncRun, stats: SyncStats, exc: Exception) -> None:
        """Record the failure durably, discarding only uncommitted work."""
        log.error("sync.failed", account=self.account.email, error=str(exc))
        run_id = run.id
        self.session.rollback()
        # The run row was committed by _start_run, so it is still there.
        persisted = self.session.get(SyncRun, run_id)
        if persisted is None:  # pragma: no cover - defensive
            return
        self._finish_run(persisted, stats, SyncStatus.FAILED, error=str(exc)[:2000])

    def _audit(self, run: SyncRun, stats: SyncStats, status: SyncStatus) -> None:
        self.session.add(
            AuditLog(
                occurred_at=datetime.now(UTC),
                actor="system",
                action=f"gmail.sync.{run.kind}",
                entity_type="sync_run",
                entity_id=str(run.id),
                account_id=self.account.id,
                summary=(
                    f"{run.kind} sync {status.value}: "
                    f"{stats.messages_created} new, {stats.messages_updated} updated, "
                    f"{stats.messages_skipped} unchanged"
                ),
                details={
                    "messages_seen": stats.messages_seen,
                    "messages_created": stats.messages_created,
                    "messages_updated": stats.messages_updated,
                    "attachments_created": stats.attachments_created,
                    "errors": len(stats.errors),
                },
                result="success" if status != SyncStatus.FAILED else "failure",
                automatic=True,
                correlation_id=str(run.id),
            )
        )
