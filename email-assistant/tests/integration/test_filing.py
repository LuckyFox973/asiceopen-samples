"""Filing an invoice to Drive, then archiving the mail that carried it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import ActionStatus, ActionType, AuditLog, PendingAction
from app.services.documents import extract_pending
from app.services.filing import file_attachment, list_folders, register_folder, resolve
from app.services.sync import SyncEngine
from tests.conftest import requires_db
from tests.fixtures import attachment_part, gmail_message, multipart, text_part
from tests.fixtures.documents import make_pdf
from tests.fixtures.fake_gmail import FakeGmailClient

pytestmark = [pytest.mark.integration, requires_db]

INFI_INVOICE = [
    "Faktura c. 2898-2388-5736",
    "Odberatel: INFINITY FINANCE s.r.o., ICO: 51234567",
    "Dodavatel: Anthropic PBC, San Francisco",
]


@dataclass
class FakeDriveFile:
    id: str
    name: str
    size_bytes: int = 0


class FakeDrive:
    """Records what would have been uploaded, and where."""

    def __init__(self, fail: bool = False):
        self.uploads: list[tuple[str, str, int]] = []
        self.fail = fail

    def upload_bytes(self, data, folder_id, name, mime_type=None):
        if self.fail:
            raise RuntimeError("Drive said no")
        self.uploads.append((folder_id, name, len(data)))
        return FakeDriveFile(id=f"file-{len(self.uploads)}", name=name)


class FakeGmailActions:
    def __init__(self):
        self.archived: list[str] = []

    def archive(self, message_id):
        from app.gmail.actions import ActionOutcome

        self.archived.append(message_id)
        return ActionOutcome(
            ok=True, detail="archived", data={"id": message_id}, undo_hint="unarchive"
        )


def invoice_message(mid: str, token: str) -> dict:
    payload = multipart(
        "multipart/mixed",
        [
            text_part("V prilohe faktura.", part_id="0"),
            attachment_part(
                "Receipt-2898-2388-5736.pdf",
                mime_type="application/pdf",
                size=64,
                attachment_id=token,
                part_id="1",
            ),
        ],
    )
    return gmail_message(
        message_id=mid,
        thread_id=mid,
        subject="Your receipt from Anthropic",
        from_="Anthropic <invoice@anthropic.com>",
        to="accountant@foxgroup.sk",
        internal_date_ms="1748772000000",
        payload=payload,
    )


@pytest.fixture
def filing_settings(settings_factory):
    return settings_factory(drive_write_enabled=True, gmail_write_enabled=True)


@pytest.fixture
def stored_invoice(db_session, account, local_storage):
    SyncEngine(
        session=db_session,
        account=account,
        client=FakeGmailClient(
            [invoice_message("inv1", "tok-inv")],
            attachments={"tok-inv": make_pdf(INFI_INVOICE)},
        ),
        storage=local_storage,
        default_start_date=date(2025, 1, 1),
        download_attachments=True,
    ).initial_sync()
    extract_pending(db_session, local_storage)

    from app.db.models import Attachment

    return db_session.scalars(select(Attachment)).first()


@pytest.fixture
def infi(db_session):
    return register_folder(
        db_session,
        name="03_INFI",
        drive_folder_id="11gNYNhRQdl9vPBfLY7sYjii1VGcoKXwz",
        match_terms=["Infinity Finance", "51234567"],
    )


class TestFolderRegistry:
    def test_a_folder_can_be_registered_and_listed(self, db_session, infi):
        assert [f.name for f in list_folders(db_session)] == ["03_INFI"]

    def test_registering_the_same_name_updates_rather_than_duplicates(self, db_session, infi):
        register_folder(db_session, "03_INFI", "new-folder-id", ["Infinity Finance"])
        folders = list_folders(db_session)
        assert len(folders) == 1
        assert folders[0].drive_folder_id == "new-folder-id"

    def test_blank_terms_are_not_stored(self, db_session):
        """A blank term matches every document ever filed."""
        folder = register_folder(db_session, "X", "id", ["Real Name", "", "   "])
        assert folder.match_terms == ["Real Name"]


class TestResolution:
    def test_the_billed_company_is_resolved(self, db_session, infi, stored_invoice):
        from app.services.filing import document_text_for

        outcome = resolve(db_session, document_text_for(db_session, stored_invoice))
        assert outcome.resolved
        assert outcome.suggestion.folder.name == "03_INFI"

    def test_with_no_folders_it_says_so_rather_than_guessing(self, db_session, stored_invoice):
        from app.services.filing import document_text_for

        outcome = resolve(db_session, document_text_for(db_session, stored_invoice))
        assert not outcome.resolved
        assert "no filing folders" in outcome.reason

    def test_two_plausible_companies_are_refused(self, db_session, stored_invoice):
        """Filing it wrong half the time is worse than asking."""
        from app.services.filing import document_text_for

        register_folder(db_session, "A", "id-a", ["INFINITY FINANCE s.r.o."])
        register_folder(db_session, "B", "id-b", ["Infinity Finance s.r.o"])
        outcome = resolve(db_session, document_text_for(db_session, stored_invoice))
        assert not outcome.resolved
        assert "both named" in outcome.reason


class TestFilingThenArchiving:
    def test_the_document_reaches_the_right_folder(
        self, db_session, account, infi, stored_invoice, local_storage, filing_settings
    ):
        drive = FakeDrive()
        result = file_attachment(
            db_session,
            account,
            stored_invoice,
            local_storage,
            drive,
            gmail=FakeGmailActions(),
            settings=filing_settings,
        )
        assert result.filed
        folder_id, name, size = drive.uploads[0]
        assert folder_id == "11gNYNhRQdl9vPBfLY7sYjii1VGcoKXwz"
        assert name == "Receipt-2898-2388-5736.pdf"
        assert size > 0

    def test_the_mail_is_archived_after_the_upload(
        self, db_session, account, infi, stored_invoice, local_storage, filing_settings
    ):
        gmail = FakeGmailActions()
        file_attachment(
            db_session,
            account,
            stored_invoice,
            local_storage,
            FakeDrive(),
            gmail=gmail,
            settings=filing_settings,
        )
        assert gmail.archived == ["inv1"]

    def test_a_failed_upload_leaves_the_mail_in_the_inbox(
        self, db_session, account, infi, stored_invoice, local_storage, filing_settings
    ):
        """The document must be safely elsewhere before the mail moves."""
        gmail = FakeGmailActions()
        result = file_attachment(
            db_session,
            account,
            stored_invoice,
            local_storage,
            FakeDrive(fail=True),
            gmail=gmail,
            settings=filing_settings,
        )
        assert not result.filed
        assert gmail.archived == []
        assert result.archive is None

    def test_nothing_is_archived_when_the_company_cannot_be_told(
        self, db_session, account, stored_invoice, local_storage, filing_settings
    ):
        gmail = FakeGmailActions()
        result = file_attachment(
            db_session,
            account,
            stored_invoice,
            local_storage,
            FakeDrive(),
            gmail=gmail,
            settings=filing_settings,
        )
        assert result.skipped
        assert gmail.archived == []

    def test_filing_alone_is_possible(
        self, db_session, account, infi, stored_invoice, local_storage, filing_settings
    ):
        gmail = FakeGmailActions()
        result = file_attachment(
            db_session,
            account,
            stored_invoice,
            local_storage,
            FakeDrive(),
            gmail=gmail,
            archive_after=False,
            settings=filing_settings,
        )
        assert result.filed
        assert gmail.archived == []

    def test_both_steps_are_written_to_the_audit_log(
        self, db_session, account, infi, stored_invoice, local_storage, filing_settings
    ):
        file_attachment(
            db_session,
            account,
            stored_invoice,
            local_storage,
            FakeDrive(),
            gmail=FakeGmailActions(),
            settings=filing_settings,
        )
        actions = db_session.scalars(select(PendingAction)).all()
        assert {a.action_type for a in actions} == {
            ActionType.DRIVE_UPLOAD.value,
            ActionType.ARCHIVE.value,
        }
        assert all(a.status == ActionStatus.EXECUTED.value for a in actions)
        assert all(a.decided_by == "user" for a in actions)

        logged = {a.action for a in db_session.scalars(select(AuditLog)).all()}
        assert "action.executed.drive_upload" in logged
        assert "action.executed.archive" in logged

    def test_filing_is_refused_without_the_drive_permission(
        self, db_session, account, infi, stored_invoice, local_storage, settings_factory
    ):
        """Wider access than anything else here, so it is never implicit."""
        from app.services.actions import ActionError

        with pytest.raises(ActionError, match="Drive"):
            file_attachment(
                db_session,
                account,
                stored_invoice,
                local_storage,
                FakeDrive(),
                gmail=FakeGmailActions(),
                settings=settings_factory(drive_write_enabled=False, gmail_write_enabled=True),
            )
