"""Extraction over stored files, and searching inside them."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db.models import AuditLog, DocumentText
from app.db.session import get_db
from app.main import create_app
from app.services.documents import (
    extract_pending,
    extraction_summary,
    get_document_text,
    normalise_ocr_text,
    ocr_pending,
    pending_blobs,
    scans_awaiting_ocr,
    unreadable_documents,
)
from app.services.search import search_documents
from app.services.sync import SyncEngine
from tests.conftest import requires_db
from tests.fixtures import attachment_part, gmail_message, multipart, text_part
from tests.fixtures.documents import (
    make_docx,
    make_locked_pdf,
    make_pdf,
    make_scanned_pdf,
    make_xlsx,
    make_zip,
)
from tests.fixtures.fake_gmail import FakeGmailClient

pytestmark = [pytest.mark.integration, requires_db]

CMR_SENTENCE = (
    "Spravca dane v odovodneni tvrdil, ze predlozene CMR listy boli "
    "duplicitne a nepreukazuju prepravu tovaru."
)
BASE_MS = 1748772000000


def message_with(mid: str, filename: str, mime: str, token: str, thread: str):
    payload = multipart(
        "multipart/mixed",
        [
            text_part("V prilohe posielam podklady.", part_id="0"),
            attachment_part(filename, mime_type=mime, size=64, attachment_id=token, part_id="1"),
        ],
    )
    return gmail_message(
        message_id=mid,
        thread_id=thread,
        subject="Podklady",
        from_="Klient ABC <klient@abc.sk>",
        to="info@foxgroup.sk",
        internal_date_ms=str(BASE_MS),
        payload=payload,
    )


@pytest.fixture
def synced(db_session, account, local_storage):
    """A mailbox holding a PDF, a DOCX and an XLSX, bytes downloaded."""
    documents = {
        "tok-pdf": make_pdf([CMR_SENTENCE]),
        "tok-docx": make_docx(["Vyjadrenie k vyzve", "Lehota uplynie 31.08.2026"]),
        "tok-xlsx": make_xlsx({"Faktury": [["Cislo", "Suma"], ["FA-2025-001", 1250.5]]}),
    }
    messages = [
        message_with("d1", "Rozhodnutie.pdf", "application/pdf", "tok-pdf", "t1"),
        message_with(
            "d2",
            "Vyjadrenie.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "tok-docx",
            "t2",
        ),
        message_with(
            "d3",
            "Faktury.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "tok-xlsx",
            "t3",
        ),
    ]
    SyncEngine(
        session=db_session,
        account=account,
        client=FakeGmailClient(messages, attachments=documents),
        storage=local_storage,
        default_start_date=date(2025, 1, 1),
        download_attachments=True,
    ).initial_sync()
    return account


class TestExtractionRun:
    def test_every_stored_file_is_parsed(self, db_session, synced, local_storage):
        stats = extract_pending(db_session, local_storage)
        assert stats.considered == 3
        assert stats.extracted == 3
        assert stats.characters > 0

    def test_text_is_stored_against_the_blob(self, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        rows = db_session.scalars(select(DocumentText)).all()
        assert len(rows) == 3
        assert all(r.status == "extracted" for r in rows)
        assert any("CMR listy" in (r.text or "") for r in rows)
        assert any("FA-2025-001" in (r.text or "") for r in rows)

    def test_pdf_page_count_recorded(self, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        pdf = db_session.scalar(select(DocumentText).where(DocumentText.method == "pypdf"))
        assert pdf.page_count == 1

    def test_running_again_does_nothing(self, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        second = extract_pending(db_session, local_storage)
        assert second.considered == 0
        assert db_session.scalar(select(DocumentText.id).limit(1)) is not None

    def test_pending_shrinks_to_zero(self, db_session, synced, local_storage):
        assert len(pending_blobs(db_session)) == 3
        extract_pending(db_session, local_storage)
        assert pending_blobs(db_session) == []

    def test_limit_processes_a_batch(self, db_session, synced, local_storage):
        stats = extract_pending(db_session, local_storage, limit=2)
        assert stats.considered == 2
        assert len(pending_blobs(db_session)) == 1

    def test_run_is_audited(self, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        entry = db_session.scalar(select(AuditLog).where(AuditLog.action == "documents.extracted"))
        assert entry is not None
        assert entry.details["extracted"] == 3

    def test_missing_stored_file_is_recorded_not_raised(self, db_session, synced, local_storage):
        for blob_key in list(local_storage.root.rglob("*")):
            if blob_key.is_file():
                blob_key.chmod(0o600)
                blob_key.unlink()

        stats = extract_pending(db_session, local_storage)
        assert stats.considered == 3
        assert stats.failed == 3
        rows = db_session.scalars(select(DocumentText)).all()
        assert all(r.status == "failed" for r in rows)
        assert all("Could not read stored file" in (r.error or "") for r in rows)

    def test_failed_extraction_can_be_retried(self, db_session, synced, local_storage):
        stored = {p: p.read_bytes() for p in local_storage.root.rglob("*") if p.is_file()}
        for path in stored:
            path.chmod(0o600)
            path.unlink()
        extract_pending(db_session, local_storage)
        assert extraction_summary(db_session)["failed"] == 3

        for path, data in stored.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        # Without the flag, failures are left alone.
        assert extract_pending(db_session, local_storage).considered == 0
        retried = extract_pending(db_session, local_storage, retry_failed=True)
        assert retried.extracted == 3
        assert extraction_summary(db_session)["failed"] == 0


class TestDeduplication:
    def test_the_same_file_in_two_messages_is_parsed_once(self, db_session, account, local_storage):
        identical = make_pdf([CMR_SENTENCE])
        messages = [
            message_with("x1", "Rozhodnutie.pdf", "application/pdf", "a", "t1"),
            message_with("x2", "Rozhodnutie-kopia.pdf", "application/pdf", "b", "t2"),
        ]
        SyncEngine(
            session=db_session,
            account=account,
            client=FakeGmailClient(messages, attachments={"a": identical, "b": identical}),
            storage=local_storage,
            default_start_date=date(2025, 1, 1),
            download_attachments=True,
        ).initial_sync()

        stats = extract_pending(db_session, local_storage)
        assert stats.considered == 1
        assert db_session.scalar(select(DocumentText.id).where(DocumentText.status == "extracted"))

    def test_both_attachments_resolve_to_the_same_text(self, db_session, account, local_storage):
        from app.db.models import Attachment

        identical = make_pdf([CMR_SENTENCE])
        SyncEngine(
            session=db_session,
            account=account,
            client=FakeGmailClient(
                [
                    message_with("y1", "a.pdf", "application/pdf", "a", "t1"),
                    message_with("y2", "b.pdf", "application/pdf", "b", "t2"),
                ],
                attachments={"a": identical, "b": identical},
            ),
            storage=local_storage,
            default_start_date=date(2025, 1, 1),
            download_attachments=True,
        ).initial_sync()
        extract_pending(db_session, local_storage)

        attachments = db_session.scalars(select(Attachment)).all()
        texts = {get_document_text(db_session, a.id).id for a in attachments}
        assert len(attachments) == 2
        assert len(texts) == 1


class TestDocumentSearch:
    def test_finds_words_that_appear_only_inside_the_pdf(self, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        hits, total = search_documents(db_session, synced.id, "CMR duplicitne")
        assert total == 1
        assert hits[0].attachment.filename == "Rozhodnutie.pdf"

    def test_highlight_shows_the_matching_passage(self, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        hits, _ = search_documents(db_session, synced.id, "duplicitne")
        # Guillemets rather than <b>: ts_headline does not escape the document
        # it is marking up, so HTML markers around attacker-supplied text
        # would be an injection waiting for the first web view of a hit.
        assert "«duplicitne»" in hits[0].headline

    def test_search_ignores_diacritics(self, db_session, account, local_storage):
        SyncEngine(
            session=db_session,
            account=account,
            client=FakeGmailClient(
                [message_with("z1", "Rozhodnutie.pdf", "application/pdf", "tk", "t9")],
                attachments={"tk": make_pdf(["Kasacna staznost proti rozhodnutiu"])},
            ),
            storage=local_storage,
            default_start_date=date(2025, 1, 1),
            download_attachments=True,
        ).initial_sync()
        extract_pending(db_session, local_storage)

        _, total = search_documents(db_session, account.id, "kasačná sťažnosť")
        assert total == 1

    def test_finds_spreadsheet_cell_contents(self, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        hits, total = search_documents(db_session, synced.id, "FA-2025-001")
        assert total == 1
        assert hits[0].attachment.filename == "Faktury.xlsx"

    def test_empty_query_returns_nothing(self, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        assert search_documents(db_session, synced.id, "  ") == ([], 0)

    def test_no_match_returns_empty(self, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        assert search_documents(db_session, synced.id, "nonexistentword")[1] == 0


class TestApi:
    @pytest.fixture
    def client(self, db_session):
        app = create_app()
        app.dependency_overrides[get_db] = lambda: db_session
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()

    def test_document_search_endpoint(self, client, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        body = client.get("/api/v1/documents/search", params={"q": "CMR duplicitne"}).json()
        assert body["total"] == 1
        assert body["items"][0]["filename"] == "Rozhodnutie.pdf"
        assert "«" in body["items"][0]["highlight"]

    def test_attachment_text_endpoint(self, client, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        found = client.get("/api/v1/attachments", params={"filename": "Rozhodnutie"}).json()
        attachment_id = found["items"][0]["id"]

        meta = client.get(f"/api/v1/attachments/{attachment_id}/text").json()
        assert meta["status"] == "extracted"
        assert meta["char_count"] > 0
        assert "text" not in meta

        full = client.get(
            f"/api/v1/attachments/{attachment_id}/text", params={"include_text": True}
        ).json()
        assert "CMR listy" in full["text"]

    def test_attachment_listing_reports_extraction_state(
        self, client, db_session, synced, local_storage
    ):
        extract_pending(db_session, local_storage)
        body = client.get("/api/v1/attachments", params={"filename": "Faktury"}).json()
        assert body["items"][0]["text_status"] == "extracted"
        assert body["items"][0]["text_chars"] > 0

    def test_text_for_unextracted_attachment_is_404(self, client, synced):
        found = client.get("/api/v1/attachments").json()
        attachment_id = found["items"][0]["id"]
        assert client.get(f"/api/v1/attachments/{attachment_id}/text").status_code == 404

    def test_summary_endpoint(self, client, db_session, synced, local_storage):
        before = client.get("/api/v1/documents/summary").json()
        assert before["pending"] == 3

        extract_pending(db_session, local_storage)
        after = client.get("/api/v1/documents/summary").json()
        assert after["extracted"] == 3
        assert after["pending"] == 0


class TestUnreadableReport:
    """Deciding whether OCR is worth building needs the files, not the count."""

    @pytest.fixture
    def mixed(self, db_session, account, local_storage):
        """A scan, two copies of one locked PDF, and a legacy Word file."""
        locked = make_locked_pdf()
        documents = {
            "tok-scan": make_scanned_pdf(),
            "tok-locked": locked,
            "tok-locked-again": locked,
            "tok-doc": b"\xd0\xcf\x11\xe0" + b"\x00" * 512,
        }
        messages = [
            message_with("u1", "Doruce.pdf", "application/pdf", "tok-scan", "u1"),
            message_with("u2", "Faktura.pdf", "application/pdf", "tok-locked", "u2"),
            message_with("u3", "Faktura.pdf", "application/pdf", "tok-locked-again", "u3"),
            message_with("u4", "Stare.doc", "application/msword", "tok-doc", "u4"),
        ]
        SyncEngine(
            session=db_session,
            account=account,
            client=FakeGmailClient(messages, attachments=documents),
            storage=local_storage,
            default_start_date=date(2025, 1, 1),
            download_attachments=True,
        ).initial_sync()
        extract_pending(db_session, local_storage)
        return account

    def test_files_are_grouped_by_extension(self, db_session, mixed):
        groups = {group.extension: group for group in unreadable_documents(db_session)}
        assert ".pdf" in groups
        assert ".doc" in groups

    def test_identical_copies_count_once_as_a_file_and_twice_as_a_copy(self, db_session, mixed):
        """The same locked invoice in two messages is one problem, not two."""
        locked = next(g for g in unreadable_documents(db_session) if g.status == "encrypted")
        assert locked.files == 1
        assert locked.copies == 2

    def test_a_scan_is_reported_as_needing_ocr(self, db_session, mixed):
        scan = next(g for g in unreadable_documents(db_session) if g.status == "needs_ocr")
        assert scan.extension == ".pdf"
        assert scan.example == "Doruce.pdf"

    def test_readable_files_are_absent(self, db_session, synced):
        assert unreadable_documents(db_session) == []

    def test_the_commonest_problem_comes_first(self, db_session, mixed):
        groups = unreadable_documents(db_session)
        assert [group.copies for group in groups] == sorted(
            (group.copies for group in groups), reverse=True
        )


class TestRetryAfterNewFormats:
    """The day a format is added, every file previously refused for it is
    readable — and nothing re-reads them unless asked."""

    @pytest.fixture
    def refused(self, db_session, account, local_storage):
        """A stored file recorded as unsupported by an earlier extractor."""
        from app.db.models import AttachmentBlob, DocumentText

        messages = [message_with("r1", "Podanie.zip", "application/zip", "tok-zip", "r1")]
        SyncEngine(
            session=db_session,
            account=account,
            client=FakeGmailClient(
                messages,
                attachments={"tok-zip": make_zip({"Rozsudok.txt": b"Sud rozhodol"})},
            ),
            storage=local_storage,
            default_start_date=date(2025, 1, 1),
            download_attachments=True,
        ).initial_sync()

        blob = db_session.scalars(select(AttachmentBlob)).first()
        db_session.add(
            DocumentText(
                blob_id=blob.id,
                status="unsupported",
                method="none",
                char_count=0,
                error="No extractor for application/zip",
            )
        )
        db_session.flush()
        return blob

    def test_an_ordinary_run_leaves_it_alone(self, db_session, refused, local_storage):
        """A finished file is not re-read on every pass."""
        assert extraction_summary(db_session)["pending"] == 0
        assert extract_pending(db_session, local_storage).considered == 0

    def test_a_retry_run_counts_it_as_work_to_do(self, db_session, refused):
        """The count and the query must agree, or the command exits early."""
        assert extraction_summary(db_session, retry_failed=True)["pending"] == 1
        assert len(pending_blobs(db_session, retry_failed=True)) == 1

    def test_a_retry_run_reads_it_with_the_new_extractor(self, db_session, refused, local_storage):
        stats = extract_pending(db_session, local_storage, retry_failed=True)
        assert stats.extracted == 1

        document = db_session.scalar(select(DocumentText).where(DocumentText.blob_id == refused.id))
        assert document.status == "extracted"
        assert "Sud rozhodol" in document.text

    def test_a_readable_file_is_not_re_read_by_a_retry(self, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        assert extract_pending(db_session, local_storage, retry_failed=True).considered == 0


class TestRetryConverges:
    """A retry run must finish even when nothing it reads becomes readable."""

    @pytest.fixture
    def only_scans(self, db_session, account, local_storage):
        """Three files that stay `needs_ocr` however often they are read."""
        documents = {f"tok-{i}": make_scanned_pdf() + f"%{i}".encode() for i in range(3)}
        messages = [
            message_with(f"s{i}", f"Sken{i}.pdf", "application/pdf", f"tok-{i}", f"s{i}")
            for i in range(3)
        ]
        SyncEngine(
            session=db_session,
            account=account,
            client=FakeGmailClient(messages, attachments=documents),
            storage=local_storage,
            default_start_date=date(2025, 1, 1),
            download_attachments=True,
        ).initial_sync()
        extract_pending(db_session, local_storage)
        return account

    def test_without_a_run_mark_they_never_settle(self, db_session, only_scans):
        """Status alone cannot end the loop: a scan re-read is still a scan."""
        assert extraction_summary(db_session, retry_failed=True)["pending"] == 3

    def test_files_read_during_this_run_count_as_settled(
        self, db_session, only_scans, local_storage
    ):
        started = datetime.now(UTC)
        stats = extract_pending(db_session, local_storage, retry_failed=True, since=started)
        assert stats.considered == 3
        assert stats.needs_ocr == 3

        remaining = extraction_summary(db_session, retry_failed=True, since=started)["pending"]
        assert remaining == 0, "the loop would otherwise report them as unprocessable"

    def test_a_file_untouched_by_this_run_is_still_outstanding(
        self, db_session, only_scans, local_storage
    ):
        started = datetime.now(UTC)
        extract_pending(db_session, local_storage, limit=1, retry_failed=True, since=started)
        assert extraction_summary(db_session, retry_failed=True, since=started)["pending"] == 2


class TestOcrQueue:
    """Reading the scan queue: what gets stored, and when the run ends."""

    @pytest.fixture
    def scans(self, db_session, account, local_storage):
        """Two files the parsers classified as scans."""
        documents = {f"tok-{i}": make_scanned_pdf() + f"%{i}".encode() for i in range(2)}
        messages = [
            message_with(f"o{i}", f"Sken{i}.pdf", "application/pdf", f"tok-{i}", f"o{i}")
            for i in range(2)
        ]
        SyncEngine(
            session=db_session,
            account=account,
            client=FakeGmailClient(messages, attachments=documents),
            storage=local_storage,
            default_start_date=date(2025, 1, 1),
            download_attachments=True,
        ).initial_sync()
        extract_pending(db_session, local_storage)
        return account

    def test_the_queue_holds_exactly_the_scans(self, db_session, scans):
        assert len(scans_awaiting_ocr(db_session)) == 2

    def test_a_readable_document_is_never_queued(self, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        assert scans_awaiting_ocr(db_session) == []

    def test_a_run_settles_what_it_looked_at(self, db_session, scans, local_storage):
        """Without the run mark, a scan OCR cannot read comes round for ever."""
        started = datetime.now(UTC)
        stats = ocr_pending(db_session, local_storage, since=started)
        assert stats.considered == 2
        assert scans_awaiting_ocr(db_session, since=started) == []

    def test_a_file_untouched_by_the_run_is_still_queued(self, db_session, scans, local_storage):
        started = datetime.now(UTC)
        ocr_pending(db_session, local_storage, limit=1, since=started)
        assert len(scans_awaiting_ocr(db_session, since=started)) == 1

    def test_a_scan_holding_no_text_is_recorded_as_empty(self, db_session, scans, local_storage):
        """Leaving it a scan would queue it again on every future run."""
        ocr_pending(db_session, local_storage, since=datetime.now(UTC))
        statuses = set(db_session.scalars(select(DocumentText.status)).all())
        assert "needs_ocr" not in statuses

    def test_the_run_is_written_to_the_audit_log(self, db_session, scans, local_storage):
        ocr_pending(db_session, local_storage, since=datetime.now(UTC))
        actions = set(db_session.scalars(select(AuditLog.action)).all())
        assert "documents.ocr" in actions

    def test_missing_tools_leave_the_queue_untouched(
        self, db_session, scans, local_storage, monkeypatch
    ):
        """A machine without tesseract must not mark scans as dealt with."""
        from app.services import ocr as ocr_module

        ocr_module.forget_capability()
        monkeypatch.setattr(ocr_module.shutil, "which", lambda _name: None)
        try:
            stats = ocr_pending(db_session, local_storage, since=datetime.now(UTC))
            assert stats.recognised == 0
            assert stats.failed == 2
            assert len(scans_awaiting_ocr(db_session)) == 2
        finally:
            ocr_module.forget_capability()


class TestOcrTextIsWorthStoring:
    """OCR on a photograph of a wall returns stray marks, not text."""

    def test_a_real_sentence_is_kept(self):
        assert normalise_ocr_text("Zmluvna pokuta je 2000 EUR") != ""

    def test_a_scattering_of_marks_is_rejected(self):
        assert normalise_ocr_text("~ | . , ' `` -- _ ~~ || ..") == ""

    def test_a_few_stray_characters_are_rejected(self):
        assert normalise_ocr_text("a1 b") == ""

    def test_slovak_diacritics_count_as_letters(self):
        assert normalise_ocr_text("Záložné právo a náhrada škody") != ""


class TestSearchDeduplication:
    """One invoice sent to three people is one document, not three results."""

    @pytest.fixture
    def circulated(self, db_session, account, local_storage):
        """The same PDF arriving in three separate messages."""
        invoice = make_pdf(["Faktura c. 2864622723, splatnost 19.05.2026"])
        documents = {f"tok-{i}": invoice for i in range(3)}
        messages = [
            message_with(f"c{i}", "Faktura.pdf", "application/pdf", f"tok-{i}", f"c{i}")
            for i in range(3)
        ]
        SyncEngine(
            session=db_session,
            account=account,
            client=FakeGmailClient(messages, attachments=documents),
            storage=local_storage,
            default_start_date=date(2025, 1, 1),
            download_attachments=True,
        ).initial_sync()
        extract_pending(db_session, local_storage)
        return account

    def test_three_copies_are_three_attachments_and_one_blob(self, db_session, circulated):
        """The premise: dedup is by content, so the text exists once."""
        from app.db.models import Attachment, AttachmentBlob

        assert db_session.scalar(select(func.count(Attachment.id))) == 3
        assert db_session.scalar(select(func.count(AttachmentBlob.id))) == 1

    def test_the_document_is_returned_once(self, db_session, circulated):
        hits, _total = search_documents(db_session, None, "faktura")
        assert len(hits) == 1

    def test_the_total_counts_documents_not_copies(self, db_session, circulated):
        """A count of three told the reader there were three invoices."""
        _hits, total = search_documents(db_session, None, "faktura")
        assert total == 1

    def test_the_hit_says_how_many_messages_carried_it(self, db_session, circulated):
        hits, _total = search_documents(db_session, None, "faktura")
        assert hits[0].copies == 3

    def test_a_document_in_one_message_reports_one_copy(self, db_session, synced, local_storage):
        extract_pending(db_session, local_storage)
        hits, _total = search_documents(db_session, None, "CMR")
        assert hits and hits[0].copies == 1

    def test_the_named_attachment_is_stable_across_searches(self, db_session, circulated):
        """A hit must point at the same message every time it is found."""
        first, _ = search_documents(db_session, None, "faktura")
        second, _ = search_documents(db_session, None, "splatnost")
        assert first[0].attachment.id == second[0].attachment.id


class TestRedo:
    """A file read correctly by a worse parser keeps its worse text for ever."""

    @pytest.fixture
    def stale(self, db_session, account, local_storage):
        """A container whose stored text predates a fix to the walker."""
        from app.db.models import AttachmentBlob, DocumentText

        messages = [message_with("z1", "Spis.zip", "application/zip", "tok-zip", "z1")]
        SyncEngine(
            session=db_session,
            account=account,
            client=FakeGmailClient(
                messages,
                attachments={"tok-zip": make_zip({"Rozsudok.txt": b"Sud rozhodol"})},
            ),
            storage=local_storage,
            default_start_date=date(2025, 1, 1),
            download_attachments=True,
        ).initial_sync()

        blob = db_session.scalars(select(AttachmentBlob)).first()
        db_session.add(
            DocumentText(
                blob_id=blob.id,
                status="extracted",
                method="zip-container",
                text="## __MACOSX/._Rozsudok.txt\n[no text (failed)]",
                char_count=46,
                extracted_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
        db_session.flush()
        return blob

    def test_an_ordinary_run_leaves_it_alone(self, db_session, stale):
        assert extraction_summary(db_session)["pending"] == 0

    def test_a_retry_leaves_it_alone_too(self, db_session, stale):
        """It produced text, so it is not in the retry set — that is the trap."""
        assert extraction_summary(db_session, retry_failed=True)["pending"] == 0

    def test_a_redo_run_counts_it(self, db_session, stale):
        started = datetime.now(UTC)
        assert extraction_summary(db_session, redo=True, since=started)["pending"] == 1

    def test_a_redo_run_replaces_the_stale_text(self, db_session, stale, local_storage):
        from app.db.models import DocumentText

        started = datetime.now(UTC)
        stats = extract_pending(db_session, local_storage, redo=True, since=started)
        assert stats.considered == 1

        document = db_session.scalar(select(DocumentText).where(DocumentText.blob_id == stale.id))
        assert "Sud rozhodol" in document.text
        assert "__MACOSX" not in document.text

    def test_a_redo_run_settles_what_it_read(self, db_session, stale, local_storage):
        """Otherwise the loop would re-read the same file until interrupted."""
        started = datetime.now(UTC)
        extract_pending(db_session, local_storage, redo=True, since=started)
        assert extraction_summary(db_session, redo=True, since=started)["pending"] == 0
