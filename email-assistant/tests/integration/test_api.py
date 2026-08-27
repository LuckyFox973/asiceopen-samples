"""HTTP-level tests against the real application and database."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import create_app
from app.services.sync import SyncEngine
from tests.conftest import requires_db
from tests.fixtures import attachment_part, gmail_message, multipart, text_part
from tests.fixtures.fake_gmail import FakeGmailClient

pytestmark = [pytest.mark.integration, requires_db]

BASE_MS = 1748772000000


@pytest.fixture
def client(db_session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def seeded(db_session, account, local_storage):
    """A mailbox with a small but realistic conversation already synced."""
    payload = multipart(
        "multipart/mixed",
        [
            text_part(
                "Dobrý deň, v prílohe posielam rozhodnutie správcu dane "
                "o daňovej kontrole DPH za rok 2025.",
                part_id="0",
            ),
            attachment_part("Rozhodnutie.pdf", size=9, attachment_id="tok1", part_id="1"),
        ],
    )
    messages = [
        gmail_message(
            message_id="m1",
            thread_id="t1",
            subject="Daňová kontrola DPH 2025",
            from_="Klient ABC <klient@abc.sk>",
            to="info@foxgroup.sk",
            internal_date_ms=str(BASE_MS),
            payload=payload,
        ),
        gmail_message(
            message_id="m2",
            thread_id="t1",
            subject="Re: Daňová kontrola DPH 2025",
            from_="Peter <peter@foxgroup.sk>",
            to="klient@abc.sk",
            internal_date_ms=str(BASE_MS + 3600_000),
            payload=text_part("Ďakujem, podklady som prevzal. Pripravím vyjadrenie."),
        ),
        gmail_message(
            message_id="m3",
            thread_id="t2",
            subject="Kasačná sťažnosť KOVACO",
            from_="Sud <podatelna@justice.sk>",
            to="peter@foxgroup.sk",
            internal_date_ms=str(BASE_MS + 7200_000),
            payload=text_part("Správca dane tvrdil, že CMR listy boli duplicitné."),
        ),
    ]
    engine = SyncEngine(
        session=db_session,
        account=account,
        client=FakeGmailClient(messages, attachments={"tok1": b"PDF-BYTES"}),
        storage=local_storage,
        default_start_date=date(2025, 1, 1),
        download_attachments=True,
    )
    engine.initial_sync()
    return account


class TestHealth:
    def test_reports_database_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"


class TestAccounts:
    def test_lists_mailboxes_with_their_addresses(self, client, account):
        response = client.get("/api/v1/accounts")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1
        assert body[0]["email"] == "peter@foxgroup.sk"
        assert set(body[0]["addresses"]) == {"peter@foxgroup.sk", "info@foxgroup.sk"}

    def test_unknown_account_is_404(self, client):
        response = client.get("/api/v1/accounts/00000000-0000-0000-0000-000000000000/sync/status")
        assert response.status_code == 404


class TestSyncStatus:
    def test_reports_counts_and_state(self, client, seeded):
        response = client.get(f"/api/v1/accounts/{seeded.id}/sync/status")
        assert response.status_code == 200
        body = response.json()
        assert body["counts"] == {"threads": 2, "messages": 3, "attachments": 1}
        assert body["state"]["initial_sync_pending"] is False
        assert body["last_runs"][0]["messages_created"] == 3


class TestSearch:
    def test_full_text_finds_slovak_content(self, client, seeded):
        response = client.get("/api/v1/messages/search", params={"q": "kontrola DPH"})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] >= 1
        assert "Daňová kontrola" in body["items"][0]["subject"]

    def test_search_ignores_diacritics(self, client, seeded):
        with_diacritics = client.get(
            "/api/v1/messages/search", params={"q": "kasačná sťažnosť"}
        ).json()
        without = client.get("/api/v1/messages/search", params={"q": "kasacna staznost"}).json()
        assert with_diacritics["total"] == 1
        assert without["total"] == 1
        assert with_diacritics["items"][0]["id"] == without["items"][0]["id"]

    def test_finds_body_text_not_present_in_subject(self, client, seeded):
        body = client.get("/api/v1/messages/search", params={"q": "CMR duplicitné"}).json()
        assert body["total"] == 1
        assert body["items"][0]["subject"] == "Kasačná sťažnosť KOVACO"

    def test_phrase_query(self, client, seeded):
        body = client.get("/api/v1/messages/search", params={"q": '"správca dane"'}).json()
        assert body["total"] == 1

    def test_negation_excludes(self, client, seeded):
        body = client.get("/api/v1/messages/search", params={"q": "kontrola -podklady"}).json()
        assert all("podklady" not in (i["snippet"] or "") for i in body["items"])

    def test_highlight_marks_the_match(self, client, seeded):
        body = client.get(
            "/api/v1/messages/search",
            params={"q": "duplicitné", "with_highlight": True},
        ).json()
        assert "<b>" in body["items"][0]["highlight"]

    def test_filter_by_direction(self, client, seeded):
        body = client.get("/api/v1/messages/search", params={"direction": "outbound"}).json()
        assert body["total"] == 1
        assert body["items"][0]["from_address"] == "peter@foxgroup.sk"

    def test_filter_by_participant_matches_any_header(self, client, seeded):
        body = client.get("/api/v1/messages/search", params={"participant": "klient@abc.sk"}).json()
        assert body["total"] == 2

    def test_filter_by_attachments(self, client, seeded):
        body = client.get("/api/v1/messages/search", params={"has_attachments": True}).json()
        assert body["total"] == 1

    def test_filter_by_receiving_alias_via_search(self, client, seeded):
        body = client.get(
            "/api/v1/messages/search", params={"participant": "info@foxgroup.sk"}
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["account_address"] == "info@foxgroup.sk"

    def test_pagination(self, client, seeded):
        first = client.get("/api/v1/messages/search", params={"limit": 2}).json()
        second = client.get("/api/v1/messages/search", params={"limit": 2, "offset": 2}).json()
        assert first["total"] == 3
        assert len(first["items"]) == 2
        assert len(second["items"]) == 1
        assert {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]} == set()

    def test_nonsense_query_returns_empty_not_error(self, client, seeded):
        body = client.get("/api/v1/messages/search", params={"q": "!!! &&& ???"}).json()
        assert body["total"] == 0

    def test_results_ordered_newest_first_without_query(self, client, seeded):
        items = client.get("/api/v1/messages/search").json()["items"]
        dates = [i["internal_date"] for i in items]
        assert dates == sorted(dates, reverse=True)


class TestThreadsAndMessages:
    def test_thread_returns_full_conversation_in_order(self, client, seeded):
        threads = client.get("/api/v1/threads", params={"q": "Daňová"}).json()
        thread_id = threads["items"][0]["id"]

        detail = client.get(f"/api/v1/threads/{thread_id}").json()
        assert detail["message_count"] == 2
        assert [m["direction"] for m in detail["messages"]] == ["inbound", "outbound"]
        assert detail["messages"][0]["body_text"].startswith("Dobrý deň")

    def test_message_detail_includes_participants_and_attachments(self, client, seeded):
        found = client.get("/api/v1/messages/search", params={"q": "rozhodnutie"}).json()
        message_id = found["items"][0]["id"]

        detail = client.get(f"/api/v1/messages/{message_id}").json()
        kinds = {p["kind"] for p in detail["participants"]}
        assert {"from", "to"} <= kinds
        assert detail["attachments"][0]["filename"] == "Rozhodnutie.pdf"
        assert detail["attachments"][0]["sha256"]
        assert detail["attachments"][0]["download_status"] == "downloaded"

    def test_missing_message_is_404(self, client, seeded):
        response = client.get("/api/v1/messages/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404


class TestAttachmentsEndpoint:
    def test_lists_and_filters_by_filename(self, client, seeded):
        body = client.get("/api/v1/attachments", params={"filename": "rozhod"}).json()
        assert body["total"] == 1
        assert body["items"][0]["mime_type"] == "application/pdf"

    def test_filter_that_matches_nothing(self, client, seeded):
        assert client.get("/api/v1/attachments", params={"filename": "zzz"}).json()["total"] == 0


class TestStats:
    def test_summarises_the_store(self, client, seeded):
        body = client.get("/api/v1/stats").json()
        assert body["messages"] == 3
        assert body["threads"] == 2
        assert body["attachments"] == 1
        assert body["attachment_blobs"] == 1
        assert body["messages_by_direction"] == {"inbound": 2, "outbound": 1}
        assert body["oldest_message"] < body["newest_message"]


class TestAuthEndpoint:
    def test_start_without_oauth_configured_explains_itself(self, client):
        response = client.post("/api/v1/auth/google/start")
        assert response.status_code == 400
        assert "GOOGLE_CLIENT_ID" in response.json()["detail"]

    def test_callback_reports_user_denial(self, client):
        response = client.get("/api/v1/auth/google/callback", params={"error": "access_denied"})
        assert response.status_code == 400
        assert "access_denied" in response.text
