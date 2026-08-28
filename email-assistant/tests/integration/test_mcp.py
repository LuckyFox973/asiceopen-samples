"""The MCP surface: what Claude can call, and what it gets back."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import Attachment, EmailThread
from app.mcp import server as mcp_module
from app.services.documents import extract_pending
from app.services.matters import assign_threads, create_client, create_matter, upsert_company
from app.services.sync import SyncEngine
from tests.conftest import requires_db
from tests.fixtures import attachment_part, gmail_message, multipart, text_part
from tests.fixtures.documents import make_docx, make_docx_with_revisions, make_pdf
from tests.fixtures.fake_gmail import FakeGmailClient

pytestmark = [pytest.mark.integration, requires_db]

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
BASE_MS = 1748772000000
CMR = "Spravca dane tvrdil, ze predlozene CMR listy boli duplicitne."


def call(name: str, **kwargs) -> str:
    """Invoke a tool the way an MCP client would, and read its text."""
    result = asyncio.run(mcp_module.server.call_tool(name, kwargs))
    if hasattr(result, "content"):
        return "\n".join(getattr(b, "text", str(b)) for b in result.content)
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, list):
        return "\n".join(getattr(b, "text", str(b)) for b in result)
    return str(result)


@pytest.fixture
def mcp_db(db_session, monkeypatch):
    """Point the server's session_scope at the rolled-back test session."""
    import contextlib

    @contextlib.contextmanager
    def scope():
        yield db_session

    monkeypatch.setattr(mcp_module, "session_scope", scope)
    return db_session


@pytest.fixture
def populated(mcp_db, account, local_storage):
    """A mailbox with mail, a revised contract, a client and a matter."""
    messages = [
        gmail_message(
            message_id="m1",
            thread_id="t1",
            subject="Danova kontrola DPH 2025 KOV-2026-01",
            from_="Klient ABC <pravnik@kovaco.sk>",
            to="info@foxgroup.sk",
            internal_date_ms=str(BASE_MS),
            payload=multipart(
                "multipart/mixed",
                [
                    text_part("V prilohe rozhodnutie.", part_id="0"),
                    attachment_part(
                        "Rozhodnutie.pdf", size=64, attachment_id="tok-pdf", part_id="1"
                    ),
                ],
            ),
        ),
        gmail_message(
            message_id="m2",
            thread_id="t1",
            subject="Re: Danova kontrola DPH 2025 KOV-2026-01",
            from_="Peter <peter@foxgroup.sk>",
            to="pravnik@kovaco.sk",
            internal_date_ms=str(BASE_MS + 3600_000),
            payload=text_part("Podklady som prevzal."),
        ),
        gmail_message(
            message_id="m3",
            thread_id="t2",
            subject="Zmluva o dielo",
            from_="Protistrana <pravnik@kovaco.sk>",
            to="info@foxgroup.sk",
            internal_date_ms=str(BASE_MS + 7200_000),
            payload=multipart(
                "multipart/mixed",
                [
                    text_part("Navrh zmluvy.", part_id="0"),
                    attachment_part(
                        "Zmluva.docx",
                        mime_type=DOCX_MIME,
                        size=64,
                        attachment_id="tok-v1",
                        part_id="1",
                    ),
                ],
            ),
        ),
        gmail_message(
            message_id="m4",
            thread_id="t2",
            subject="Re: Zmluva o dielo",
            from_="Protistrana <pravnik@kovaco.sk>",
            to="info@foxgroup.sk",
            internal_date_ms=str(BASE_MS + 90000_000),
            payload=multipart(
                "multipart/mixed",
                [
                    text_part("Nase pripomienky.", part_id="0"),
                    attachment_part(
                        "Zmluva_v2.docx",
                        mime_type=DOCX_MIME,
                        size=64,
                        attachment_id="tok-v2",
                        part_id="1",
                    ),
                ],
            ),
        ),
    ]
    SyncEngine(
        session=mcp_db,
        account=account,
        client=FakeGmailClient(
            messages,
            attachments={
                "tok-pdf": make_pdf([CMR]),
                "tok-v1": make_docx(["Zmluva o dielo", "Zmluvna pokuta je 5000 EUR."]),
                "tok-v2": make_docx_with_revisions(
                    before="Zmluvna pokuta je ",
                    deleted="5000 EUR",
                    inserted="2000 EUR",
                    comment=("Advokat", "Trvame na povodnej sume."),
                ),
            },
        ),
        storage=local_storage,
        default_start_date=date(2025, 1, 1),
        download_attachments=True,
    ).initial_sync()
    extract_pending(mcp_db, local_storage)

    company = upsert_company(mcp_db, "KOVACO s.r.o.", domains=["kovaco.sk"])
    client = create_client(mcp_db, "KOVACO", company=company, reference="KOV")
    create_matter(mcp_db, client, "Danova kontrola DPH 2025", reference="KOV-2026-01")
    assign_threads(mcp_db, account.id)
    return account


class TestToolSurface:
    def test_every_tool_is_listed_with_a_description(self):
        tools = asyncio.run(mcp_module.server.list_tools())
        assert len(tools) >= 14
        assert all(t.description for t in tools)
        assert all(t.input_schema is not None for t in tools)

    def test_expected_tools_are_present(self):
        names = {t.name for t in asyncio.run(mcp_module.server.list_tools())}
        assert {
            "search_emails",
            "get_thread",
            "search_threads",
            "search_documents",
            "get_attachment_text",
            "document_versions",
            "diff_documents",
            "list_clients",
            "get_matter",
            "review_queue",
            "recent_activity",
            "sync_status",
            "run_sync",
            "recent_actions",
        } <= names

    def test_nothing_sends_mail(self):
        """Sending is not exposed at all: it needs a deliberate, separate step."""
        names = {t.name for t in asyncio.run(mcp_module.server.list_tools())}
        assert not any(name.startswith("send") for name in names)

    def test_destructive_tools_are_in_the_approval_tier(self):
        """Binning and permanent deletion must never run on the model's say-so."""
        from app.db.models import ActionType, RiskTier
        from app.services.actions import risk_tier

        names = {t.name for t in asyncio.run(mcp_module.server.list_tools())}
        assert {"request_trash", "request_permanent_delete"} <= names
        for action in (ActionType.TRASH, ActionType.DELETE_PERMANENT, ActionType.SEND):
            assert risk_tier(action) is RiskTier.APPROVAL

    def test_destructive_tools_say_they_wait(self):
        """The description is what the model reads before choosing a tool."""
        tools = {t.name: t.description or "" for t in asyncio.run(mcp_module.server.list_tools())}
        assert "waits" in tools["request_trash"].lower()
        assert "undoes this" in tools["request_permanent_delete"].lower()

    def test_write_tools_refuse_when_write_access_is_off(self, populated):
        """Read-only scopes mean the action cannot happen even if proposed."""
        output = call("request_trash", gmail_message_id="m1")
        assert "without write permission" in output or "read-only" in output


class TestMailTools:
    def test_search_finds_a_message(self, populated):
        output = call("search_emails", query="danova kontrola")
        assert "Danova kontrola" in output
        assert "thread=" in output

    def test_search_is_diacritics_insensitive(self, populated):
        assert "Danova kontrola" in call("search_emails", query="daňová kontrola")

    def test_search_filters_by_direction(self, populated):
        output = call("search_emails", direction="outbound")
        assert "peter@foxgroup.sk" in output

    def test_search_with_no_match_says_so(self, populated):
        assert "No messages found" in call("search_emails", query="zzzunlikelyzzz")

    def test_get_thread_returns_the_conversation_in_order(self, populated, mcp_db):
        thread = mcp_db.scalar(select(EmailThread).where(EmailThread.subject.ilike("%Danova%")))
        output = call("get_thread", thread_id=str(thread.id))
        assert "2 message(s)" in output
        assert "V prilohe rozhodnutie" in output
        assert "Podklady som prevzal" in output
        assert output.index("V prilohe") < output.index("Podklady som prevzal")

    def test_get_thread_lists_attachments(self, populated, mcp_db):
        thread = mcp_db.scalar(select(EmailThread).where(EmailThread.subject.ilike("%Danova%")))
        assert "Rozhodnutie.pdf" in call("get_thread", thread_id=str(thread.id))

    def test_unknown_thread_is_reported_not_raised(self, populated):
        assert "No thread" in call("get_thread", thread_id="00000000-0000-0000-0000-000000000000")

    def test_a_malformed_id_is_reported_clearly(self, populated):
        output = call("get_thread", thread_id="not-a-uuid")
        assert "UUID" in output or "uuid" in output.lower()


class TestDocumentTools:
    def test_search_finds_text_inside_a_pdf(self, populated):
        output = call("search_documents", query="CMR duplicitne")
        assert "Rozhodnutie.pdf" in output
        assert "attachment=" in output

    def test_attachment_text_returns_the_document(self, populated, mcp_db):
        attachment = mcp_db.scalar(
            select(Attachment).where(Attachment.filename == "Rozhodnutie.pdf")
        )
        output = call("get_attachment_text", attachment_id=str(attachment.id))
        assert "CMR listy" in output

    def test_attachment_text_separates_current_from_removed(self, populated, mcp_db):
        attachment = mcp_db.scalar(
            select(Attachment).where(Attachment.filename == "Zmluva_v2.docx")
        )
        output = call("get_attachment_text", attachment_id=str(attachment.id))

        assert "2000 EUR" in output
        assert "Tracked changes:" in output
        assert "NOT current text" in output
        # The struck-out figure appears only under the removed heading.
        assert output.index("NOT current text") < output.index("5000 EUR")

    def test_comments_are_shown(self, populated, mcp_db):
        attachment = mcp_db.scalar(
            select(Attachment).where(Attachment.filename == "Zmluva_v2.docx")
        )
        assert "Trvame na povodnej sume" in call(
            "get_attachment_text", attachment_id=str(attachment.id)
        )

    def test_versions_are_listed_with_a_diff(self, populated, mcp_db):
        attachment = mcp_db.scalar(select(Attachment).where(Attachment.filename == "Zmluva.docx"))
        output = call("document_versions", attachment_id=str(attachment.id))

        assert "2 version(s)" in output
        assert "Zmluva.docx" in output and "Zmluva_v2.docx" in output
        assert "Most recent change" in output
        assert "+ Zmluvna pokuta je 2000 EUR" in output

    def test_diff_between_two_attachments(self, populated, mcp_db):
        older = mcp_db.scalar(select(Attachment).where(Attachment.filename == "Zmluva.docx"))
        newer = mcp_db.scalar(select(Attachment).where(Attachment.filename == "Zmluva_v2.docx"))
        output = call(
            "diff_documents",
            older_attachment_id=str(older.id),
            newer_attachment_id=str(newer.id),
        )
        assert "added" in output
        assert any("5000 EUR" in line for line in output.splitlines() if line.startswith("-"))


class TestMatterTools:
    def test_clients_and_their_matters_are_listed(self, populated):
        output = call("list_clients")
        assert "KOVACO" in output
        assert "Danova kontrola DPH 2025" in output

    def test_matter_shows_what_is_filed_under_it(self, populated, mcp_db):
        from app.db.models import Matter

        matter = mcp_db.scalar(select(Matter))
        output = call("get_matter", matter_id=str(matter.id))
        assert "KOVACO" in output
        assert "thread(s)" in output

    def test_unknown_matter_is_reported(self, populated):
        assert "No matter" in call("get_matter", matter_id="00000000-0000-0000-0000-000000000000")


class TestStateTools:
    def test_sync_status_reports_what_is_stored(self, populated):
        output = call("sync_status")
        assert "message(s)" in output
        assert "peter@foxgroup.sk" in output or "info@foxgroup.sk" in output

    def test_recent_activity_splits_by_who_spoke_last(self, populated):
        output = call("recent_activity", days=3650)
        assert "They wrote last" in output
        assert "You wrote last" in output
        # It must not overclaim: who spoke last is not who owes a reply.
        assert "not whether a reply is owed" in output

    def test_recent_actions_reads_the_audit_log(self, populated):
        output = call("recent_actions")
        assert "gmail.sync" in output

    def test_review_queue_is_empty_when_nothing_is_uncertain(self, populated):
        output = call("review_queue")
        assert "Nothing waiting" in output or "confidence" in output
