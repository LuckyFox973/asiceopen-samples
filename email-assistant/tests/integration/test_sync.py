"""End-to-end sync behaviour against a real database and a fake Gmail."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from app.db.models import (
    Attachment,
    AttachmentBlob,
    AuditLog,
    Contact,
    EmailMessage,
    EmailParticipant,
    EmailThread,
    SyncState,
)
from app.services.sync import SyncEngine
from tests.conftest import requires_db
from tests.fixtures import attachment_part, gmail_message, multipart, text_part
from tests.fixtures.fake_gmail import FakeGmailClient

pytestmark = [pytest.mark.integration, requires_db]

# 2025-06-01T10:00:00Z and onwards — comfortably after the fixture start date.
BASE_MS = 1748772000000


def ms(offset_hours: int = 0) -> str:
    return str(BASE_MS + offset_hours * 3600 * 1000)


def inbound(mid: str, thread: str = "thr-1", **kwargs):
    kwargs.setdefault("from_", "Klient ABC <klient@abc.sk>")
    kwargs.setdefault("to", "info@foxgroup.sk")
    kwargs.setdefault("internal_date_ms", ms())
    return gmail_message(message_id=mid, thread_id=thread, **kwargs)


def outbound(mid: str, thread: str = "thr-1", **kwargs):
    kwargs.setdefault("from_", "Peter <peter@foxgroup.sk>")
    kwargs.setdefault("to", "klient@abc.sk")
    kwargs.setdefault("internal_date_ms", ms(1))
    return gmail_message(message_id=mid, thread_id=thread, **kwargs)


def engine_for(session, account, client, storage=None, **kwargs):
    return SyncEngine(
        session=session,
        account=account,
        client=client,
        storage=storage,
        default_start_date=date(2025, 1, 1),
        download_attachments=storage is not None,
        **kwargs,
    )


class TestInitialSync:
    def test_stores_messages_threads_and_participants(self, db_session, account):
        client = FakeGmailClient([inbound("m1"), outbound("m2")])
        run = engine_for(db_session, account, client).initial_sync()

        assert run.status == "completed"
        assert run.messages_created == 2
        assert db_session.scalar(select(func.count(EmailMessage.id))) == 2
        assert db_session.scalar(select(func.count(EmailThread.id))) == 1

        thread = db_session.scalar(select(EmailThread))
        assert thread.message_count == 2
        assert thread.last_message_direction == "outbound"

        participants = db_session.scalars(select(EmailParticipant)).all()
        assert {p.address for p in participants} == {
            "klient@abc.sk",
            "info@foxgroup.sk",
            "peter@foxgroup.sk",
        }
        assert any(p.is_own and p.address == "info@foxgroup.sk" for p in participants)

    def test_direction_and_receiving_alias_resolved(self, db_session, account):
        client = FakeGmailClient([inbound("m1"), outbound("m2")])
        engine_for(db_session, account, client).initial_sync()

        first = db_session.scalar(select(EmailMessage).where(EmailMessage.gmail_message_id == "m1"))
        second = db_session.scalar(
            select(EmailMessage).where(EmailMessage.gmail_message_id == "m2")
        )
        assert (first.direction, first.account_address) == ("inbound", "info@foxgroup.sk")
        assert (second.direction, second.account_address) == ("outbound", "peter@foxgroup.sk")

    def test_running_twice_creates_nothing_new(self, db_session, account):
        client = FakeGmailClient([inbound("m1"), outbound("m2")])
        engine_for(db_session, account, client).initial_sync()
        before = db_session.scalar(select(func.count(EmailMessage.id)))

        # A second engine, as a scheduled re-run would be.
        second_run = engine_for(db_session, account, client).initial_sync()

        assert db_session.scalar(select(func.count(EmailMessage.id))) == before
        assert second_run.messages_created == 0
        assert second_run.messages_skipped == 2

    def test_changed_message_is_updated_not_duplicated(self, db_session, account):
        client = FakeGmailClient([inbound("m1")])
        engine_for(db_session, account, client).initial_sync()

        client.add_message(inbound("m1", labels=["INBOX", "STARRED"]))
        run = engine_for(db_session, account, client).initial_sync()

        assert run.messages_updated == 1
        assert db_session.scalar(select(func.count(EmailMessage.id))) == 1
        message = db_session.scalar(select(EmailMessage))
        assert "STARRED" in message.labels

    def test_messages_before_start_date_are_skipped(self, db_session, account):
        account.sync_start_date = date(2025, 6, 1)
        db_session.flush()
        old = inbound("old", internal_date_ms=str(BASE_MS - 90 * 86400 * 1000))
        client = FakeGmailClient([old, inbound("new")])

        run = engine_for(db_session, account, client).initial_sync()

        assert run.messages_created == 1
        stored = db_session.scalars(select(EmailMessage.gmail_message_id)).all()
        assert stored == ["new"]

    def test_pagination_walks_every_page(self, db_session, account):
        messages = [inbound(f"m{i}", thread=f"thr-{i}") for i in range(25)]
        client = FakeGmailClient(messages, page_size_override=10)

        run = engine_for(db_session, account, client).initial_sync()

        assert run.messages_created == 25
        assert client.list_calls == 3

    def test_partial_run_checkpoints_page_token(self, db_session, account):
        messages = [inbound(f"m{i}", thread=f"thr-{i}") for i in range(25)]
        client = FakeGmailClient(messages, page_size_override=10)

        run = engine_for(db_session, account, client, max_messages_per_run=10).initial_sync()

        assert run.status == "partial"
        state = db_session.scalar(select(SyncState))
        assert state.initial_sync_page_token == "10"
        assert state.initial_sync_completed_at is None

        # Resuming continues from the checkpoint, not from the start.
        resumed = engine_for(db_session, account, client).initial_sync()
        assert resumed.messages_created == 15
        assert db_session.scalar(select(func.count(EmailMessage.id))) == 25

    def test_completion_anchors_history_id(self, db_session, account):
        client = FakeGmailClient([inbound("m1")], profile_history_id=5555)
        engine_for(db_session, account, client).initial_sync()

        state = db_session.scalar(select(SyncState))
        assert state.last_history_id == 5555
        assert state.initial_sync_completed_at is not None

    def test_one_broken_message_does_not_abort_the_run(self, db_session, account):
        client = FakeGmailClient([inbound("good1"), inbound("bad"), inbound("good2")])
        client.fail_on_message_ids = {"bad"}

        run = engine_for(db_session, account, client).initial_sync()

        assert run.status == "completed"
        assert run.messages_created == 2
        assert run.details["error_count"] == 1

    def test_writes_an_audit_entry(self, db_session, account):
        client = FakeGmailClient([inbound("m1")])
        run = engine_for(db_session, account, client).initial_sync()

        entry = db_session.scalar(select(AuditLog).where(AuditLog.correlation_id == str(run.id)))
        assert entry is not None
        assert entry.action == "gmail.sync.initial"
        assert entry.actor == "system"
        assert entry.automatic is True


class TestIncrementalSync:
    def test_picks_up_only_new_messages(self, db_session, account):
        client = FakeGmailClient([inbound("m1")], profile_history_id=100)
        engine_for(db_session, account, client).initial_sync()
        client.get_message_calls.clear()

        client.add_message(inbound("m2", internal_date_ms=ms(2)))
        client.record_history("messagesAdded", "m2", 101)
        run = engine_for(db_session, account, client).incremental_sync()

        assert run.messages_created == 1
        assert client.get_message_calls == ["m2"]
        assert db_session.scalar(select(func.count(EmailMessage.id))) == 2

    def test_sync_chooses_incremental_after_initial(self, db_session, account):
        client = FakeGmailClient([inbound("m1")], profile_history_id=100)
        engine_for(db_session, account, client).sync()

        client.add_message(inbound("m2"))
        client.record_history("messagesAdded", "m2", 101)
        run = engine_for(db_session, account, client).sync()

        assert run.kind == "incremental"

    def test_label_change_refreshes_the_stored_message(self, db_session, account):
        client = FakeGmailClient([inbound("m1")], profile_history_id=100)
        engine_for(db_session, account, client).initial_sync()

        client.add_message(inbound("m1", labels=["INBOX", "IMPORTANT"]))
        client.record_history("labelsAdded", "m1", 102)
        run = engine_for(db_session, account, client).incremental_sync()

        assert run.messages_updated == 1
        assert "IMPORTANT" in db_session.scalar(select(EmailMessage)).labels

    def test_duplicate_history_entries_fetch_once(self, db_session, account):
        client = FakeGmailClient([inbound("m1")], profile_history_id=100)
        engine_for(db_session, account, client).initial_sync()
        client.get_message_calls.clear()

        client.add_message(inbound("m2"))
        client.record_history("messagesAdded", "m2", 101)
        client.record_history("labelsAdded", "m2", 102)
        engine_for(db_session, account, client).incremental_sync()

        assert client.get_message_calls == ["m2"]

    def test_advances_the_cursor(self, db_session, account):
        client = FakeGmailClient([inbound("m1")], profile_history_id=100)
        engine_for(db_session, account, client).initial_sync()

        client.add_message(inbound("m2"))
        client.record_history("messagesAdded", "m2", 250)
        engine_for(db_session, account, client).incremental_sync()

        assert db_session.scalar(select(SyncState)).last_history_id == 250

    def test_expired_history_falls_back_to_full_pass(self, db_session, account):
        client = FakeGmailClient([inbound("m1")], profile_history_id=100)
        engine_for(db_session, account, client).initial_sync()

        client.add_message(inbound("m2"))
        client.history_expired_before = 10_000  # Gmail forgot our cursor
        client.profile_history_id = 20_000

        run = engine_for(db_session, account, client).sync()

        assert run.kind == "initial"
        assert run.status == "completed"
        assert db_session.scalar(select(func.count(EmailMessage.id))) == 2
        assert db_session.scalar(select(SyncState)).last_history_id == 20_000


class TestAttachments:
    def _message_with_pdf(self, mid: str, filename: str, token: str, thread: str = "thr-a"):
        payload = multipart(
            "multipart/mixed",
            [
                text_part("V prílohe posielam rozhodnutie.", part_id="0"),
                attachment_part(filename, size=11, attachment_id=token, part_id="1"),
            ],
        )
        return inbound(mid, thread=thread, payload=payload)

    def test_metadata_and_blob_are_stored(self, db_session, account, local_storage):
        client = FakeGmailClient(
            [self._message_with_pdf("m1", "Rozhodnutie.pdf", "tok1")],
            attachments={"tok1": b"PDF-BYTES-1"},
        )
        run = engine_for(db_session, account, client, storage=local_storage).initial_sync()

        assert run.attachments_created == 1
        attachment = db_session.scalar(select(Attachment))
        assert attachment.filename == "Rozhodnutie.pdf"
        assert attachment.mime_type == "application/pdf"
        assert attachment.download_status == "downloaded"
        assert attachment.blob_id is not None

        blob = db_session.scalar(select(AttachmentBlob))
        assert blob.size_bytes == 11
        assert local_storage.get(blob.storage_key) == b"PDF-BYTES-1"

    def test_same_file_in_two_messages_stored_once(self, db_session, account, local_storage):
        client = FakeGmailClient(
            [
                self._message_with_pdf("m1", "Rozhodnutie.pdf", "tok1", thread="t1"),
                self._message_with_pdf("m2", "Rozhodnutie-kopia.pdf", "tok2", thread="t2"),
            ],
            attachments={"tok1": b"IDENTICAL!!", "tok2": b"IDENTICAL!!"},
        )
        engine_for(db_session, account, client, storage=local_storage).initial_sync()

        assert db_session.scalar(select(func.count(Attachment.id))) == 2
        assert db_session.scalar(select(func.count(AttachmentBlob.id))) == 1

        blob_ids = db_session.scalars(select(Attachment.blob_id)).all()
        assert len(set(blob_ids)) == 1

    def test_metadata_only_when_downloads_disabled(self, db_session, account):
        client = FakeGmailClient(
            [self._message_with_pdf("m1", "Rozhodnutie.pdf", "tok1")],
            attachments={"tok1": b"PDF-BYTES-1"},
        )
        engine_for(db_session, account, client, storage=None).initial_sync()

        attachment = db_session.scalar(select(Attachment))
        assert attachment.filename == "Rozhodnutie.pdf"
        assert attachment.blob_id is None
        assert attachment.download_status == "pending"
        assert client.get_attachment_calls == []

    def test_oversized_attachment_is_skipped_not_downloaded(
        self, db_session, account, local_storage
    ):
        payload = multipart(
            "multipart/mixed",
            [
                text_part("velky subor", part_id="0"),
                attachment_part("velky.zip", size=50_000_000, attachment_id="big", part_id="1"),
            ],
        )
        client = FakeGmailClient([inbound("m1", payload=payload)], attachments={"big": b"x"})

        engine_for(
            db_session, account, client, storage=local_storage, max_attachment_bytes=1_000_000
        ).initial_sync()

        attachment = db_session.scalar(select(Attachment))
        assert attachment.download_status == "skipped_too_large"
        assert attachment.blob_id is None
        assert client.get_attachment_calls == []

    def test_failed_download_is_recorded_and_sync_continues(
        self, db_session, account, local_storage
    ):
        client = FakeGmailClient(
            [self._message_with_pdf("m1", "chyba.pdf", "missing-token")], attachments={}
        )
        run = engine_for(db_session, account, client, storage=local_storage).initial_sync()

        assert run.status == "completed"
        attachment = db_session.scalar(select(Attachment))
        assert attachment.download_status == "failed"
        assert attachment.download_error

    def test_resync_does_not_redownload(self, db_session, account, local_storage):
        client = FakeGmailClient(
            [self._message_with_pdf("m1", "Rozhodnutie.pdf", "tok1")],
            attachments={"tok1": b"PDF-BYTES-1"},
        )
        engine_for(db_session, account, client, storage=local_storage).initial_sync()
        engine_for(db_session, account, client, storage=local_storage).initial_sync()

        assert len(client.get_attachment_calls) == 1


class TestContacts:
    def test_contacts_created_with_counts_and_flags(self, db_session, account):
        client = FakeGmailClient([inbound("m1"), inbound("m2", internal_date_ms=ms(3))])
        engine_for(db_session, account, client).initial_sync()

        klient = db_session.scalar(
            select(Contact).where(Contact.primary_address == "klient@abc.sk")
        )
        assert klient.display_name == "Klient ABC"
        assert klient.domain == "abc.sk"
        assert klient.is_own is False
        assert klient.message_count == 2

        mine = db_session.scalar(
            select(Contact).where(Contact.primary_address == "info@foxgroup.sk")
        )
        assert mine.is_own is True

    def test_first_and_last_seen_span_the_conversation(self, db_session, account):
        client = FakeGmailClient(
            [inbound("m1", internal_date_ms=ms(0)), inbound("m2", internal_date_ms=ms(48))]
        )
        engine_for(db_session, account, client).initial_sync()

        contact = db_session.scalar(
            select(Contact).where(Contact.primary_address == "klient@abc.sk")
        )
        assert contact.first_seen_at == datetime.fromtimestamp(BASE_MS / 1000, tz=UTC)
        assert contact.last_seen_at > contact.first_seen_at
