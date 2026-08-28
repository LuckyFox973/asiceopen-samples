"""Successive versions of the same document, end to end."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import Attachment, AttachmentBlob, AuditLog, DocumentText
from app.services.documents import extract_pending
from app.services.sync import SyncEngine
from app.services.versions import (
    count_versions,
    diff_versions,
    documents_with_revisions,
    families_with_multiple_versions,
    version_history,
)
from tests.conftest import requires_db
from tests.fixtures import attachment_part, gmail_message, multipart, text_part
from tests.fixtures.documents import make_docx, make_docx_with_revisions
from tests.fixtures.fake_gmail import FakeGmailClient

pytestmark = [pytest.mark.integration, requires_db]

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
BASE_MS = 1748772000000


def message(mid: str, thread: str, filename: str, token: str, hours: int = 0):
    payload = multipart(
        "multipart/mixed",
        [
            text_part("V prilohe znenie.", part_id="0"),
            attachment_part(filename, mime_type=DOCX_MIME, size=64,
                            attachment_id=token, part_id="1"),
        ],
    )
    return gmail_message(
        message_id=mid,
        thread_id=thread,
        subject="Zmluva",
        from_="Protistrana <pravnik@kovaco.sk>",
        to="info@foxgroup.sk",
        internal_date_ms=str(BASE_MS + hours * 3600_000),
        payload=payload,
    )


@pytest.fixture
def two_versions(db_session, account, local_storage):
    """The same contract twice: a clean draft, then a revised one."""
    first = make_docx(["Zmluva o dielo", "Zmluvna pokuta je 5000 EUR."])
    second = make_docx_with_revisions(
        before="Zmluvna pokuta je ",
        deleted="5000 EUR",
        inserted="2000 EUR",
        comment=("Advokat", "Trvame na povodnej sume."),
    )
    SyncEngine(
        session=db_session,
        account=account,
        client=FakeGmailClient(
            [
                message("v1", "t1", "Zmluva.docx", "tok1", hours=0),
                message("v2", "t1", "Zmluva_v2.docx", "tok2", hours=48),
            ],
            attachments={"tok1": first, "tok2": second},
        ),
        storage=local_storage,
        default_start_date=date(2025, 1, 1),
        download_attachments=True,
    ).initial_sync()
    extract_pending(db_session, local_storage)
    return account


class TestSeparateBlobs:
    def test_a_revised_document_is_a_separate_stored_file(self, db_session, two_versions):
        blobs = db_session.scalars(select(AttachmentBlob)).all()
        assert len(blobs) == 2
        assert len({b.sha256 for b in blobs}) == 2

    def test_both_versions_are_extracted(self, db_session, two_versions):
        rows = db_session.scalars(select(DocumentText)).all()
        assert len(rows) == 2
        assert all(r.status == "extracted" for r in rows)

    def test_the_revised_one_carries_its_changes(self, db_session, two_versions):
        revised = db_session.scalar(
            select(DocumentText).where(DocumentText.revision_count > 0)
        )
        assert revised is not None
        assert "2000 EUR" in revised.text
        assert revised.deleted_text == "5000 EUR"
        assert "Trvame na povodnej sume" in revised.comment_text
        assert "Advokat" in revised.revision_authors


class TestVersionHistory:
    def test_both_versions_belong_to_one_family(self, db_session, two_versions):
        attachment = db_session.scalar(
            select(Attachment).where(Attachment.filename == "Zmluva.docx")
        )
        history = version_history(db_session, attachment.id)

        assert history.family == "zmluva"
        assert history.count == 2
        assert history.has_multiple is True

    def test_versions_are_ordered_oldest_first(self, db_session, two_versions):
        attachment = db_session.scalar(
            select(Attachment).where(Attachment.filename == "Zmluva.docx")
        )
        history = version_history(db_session, attachment.id)
        assert [v.filename for v in history.versions] == ["Zmluva.docx", "Zmluva_v2.docx"]
        assert history.versions[0].received_at < history.versions[1].received_at

    def test_the_newer_version_is_flagged_as_revised(self, db_session, two_versions):
        attachment = db_session.scalar(
            select(Attachment).where(Attachment.filename == "Zmluva.docx")
        )
        newest = version_history(db_session, attachment.id).versions[-1]
        assert newest.has_revisions
        assert "insertion" in newest.revision_summary

    def test_a_document_seen_once_has_one_version(self, db_session, account, local_storage):
        SyncEngine(
            session=db_session,
            account=account,
            client=FakeGmailClient(
                [message("s1", "t9", "Podanie.docx", "tk")],
                attachments={"tk": make_docx(["Jedina verzia"])},
            ),
            storage=local_storage,
            default_start_date=date(2025, 1, 1),
            download_attachments=True,
        ).initial_sync()
        extract_pending(db_session, local_storage)

        attachment = db_session.scalar(
            select(Attachment).where(Attachment.filename == "Podanie.docx")
        )
        history = version_history(db_session, attachment.id)
        assert history.count == 1
        assert history.has_multiple is False

    def test_count_versions_counts_distinct_contents(self, db_session, two_versions):
        assert count_versions(db_session, "Zmluva.docx") == 2

    def test_unknown_attachment_yields_an_empty_history(self, db_session):
        import uuid as uuid_module

        assert version_history(db_session, uuid_module.uuid4()).count == 0


class TestDiff:
    def test_diff_shows_what_changed(self, db_session, two_versions):
        older, newer = version_history(
            db_session,
            db_session.scalar(
                select(Attachment).where(Attachment.filename == "Zmluva.docx")
            ).id,
        ).versions

        diff = diff_versions(db_session, older.attachment_id, newer.attachment_id)
        assert diff is not None
        assert diff.is_identical is False
        assert any("2000 EUR" in line for line in diff.added_lines)
        assert any("5000 EUR" in line for line in diff.removed_lines)
        assert "line(s) added" in diff.summary()

    def test_diff_of_a_document_with_itself_is_identical(self, db_session, two_versions):
        attachment = db_session.scalar(
            select(Attachment).where(Attachment.filename == "Zmluva.docx")
        )
        diff = diff_versions(db_session, attachment.id, attachment.id)
        assert diff.is_identical
        assert diff.similarity == 1.0
        assert diff.summary() == "No textual difference."

    def test_diff_against_an_unextracted_attachment_is_none(self, db_session, two_versions):
        import uuid as uuid_module

        attachment = db_session.scalar(select(Attachment))
        assert diff_versions(db_session, attachment.id, uuid_module.uuid4()) is None


class TestSignals:
    def test_a_new_version_is_written_to_the_audit_log(self, db_session, two_versions):
        entry = db_session.scalar(
            select(AuditLog).where(AuditLog.action == "documents.new_version")
        )
        assert entry is not None
        assert "Zmluva" in entry.summary
        assert entry.details["previous_sha256"] != entry.details["sha256"]

    def test_a_single_version_raises_no_signal(self, db_session, account, local_storage):
        SyncEngine(
            session=db_session,
            account=account,
            client=FakeGmailClient(
                [message("u1", "t8", "Unikat.docx", "tk")],
                attachments={"tk": make_docx(["Jedina verzia"])},
            ),
            storage=local_storage,
            default_start_date=date(2025, 1, 1),
            download_attachments=True,
        ).initial_sync()
        extract_pending(db_session, local_storage)

        assert (
            db_session.scalar(
                select(AuditLog).where(AuditLog.action == "documents.new_version")
            )
            is None
        )

    def test_documents_with_revisions_are_listed(self, db_session, two_versions):
        flagged = documents_with_revisions(db_session)
        assert len(flagged) == 1
        assert flagged[0].revision_count == 2

    def test_families_with_multiple_versions_are_reported(self, db_session, two_versions):
        families = families_with_multiple_versions(db_session)
        assert ("zmluva", 2) in families


class TestSearchBehaviour:
    def test_the_current_figure_is_searchable(self, db_session, two_versions):
        from app.services.search import search_documents

        _, total = search_documents(db_session, two_versions.id, "2000 EUR")
        assert total == 1

    def test_a_struck_out_figure_does_not_surface_from_the_revised_file(
        self, db_session, two_versions
    ):
        """5000 appears in v1's body and in v2's comment, never in v2's body."""
        from app.services.search import search_documents

        hits, _ = search_documents(db_session, two_versions.id, "5000 EUR")
        revised = db_session.scalar(
            select(DocumentText).where(DocumentText.revision_count > 0)
        )
        matched_blob_ids = {
            db_session.get(Attachment, hit.attachment.id).blob_id for hit in hits
        }
        assert revised.blob_id not in matched_blob_ids

    def test_comment_content_is_searchable(self, db_session, two_versions):
        from app.services.search import search_documents

        _, total = search_documents(db_session, two_versions.id, "povodnej sume")
        assert total == 1
