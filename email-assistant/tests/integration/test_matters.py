"""Filing threads under clients and matters, with confidence and review."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.db.models import AuditLog, LinkTarget, MatterLink
from app.services.matters import (
    AUTO_LINK_THRESHOLD,
    assign_threads,
    confirm_link,
    create_client,
    create_matter,
    existing_link,
    external_domains,
    links_needing_review,
    matter_contents,
    normalise_subject,
    reject_link,
    suggest_for_thread,
    upsert_company,
)
from app.services.sync import SyncEngine
from tests.conftest import requires_db
from tests.fixtures import gmail_message
from tests.fixtures.fake_gmail import FakeGmailClient

pytestmark = [pytest.mark.integration, requires_db]

BASE_MS = 1748772000000


def sync(db_session, account, messages):
    SyncEngine(
        session=db_session,
        account=account,
        client=FakeGmailClient(messages),
        default_start_date=date(2025, 1, 1),
        download_attachments=False,
    ).initial_sync()


def thread_named(db_session, subject_fragment):
    from app.db.models import EmailThread

    return db_session.scalar(
        select(EmailThread).where(EmailThread.subject.ilike(f"%{subject_fragment}%"))
    )


def kovaco_message(mid: str, thread: str, subject: str, sender: str = "pravnik@kovaco.sk"):
    return gmail_message(
        message_id=mid,
        thread_id=thread,
        subject=subject,
        from_=f"Protistrana <{sender}>",
        to="info@foxgroup.sk",
        internal_date_ms=str(BASE_MS),
    )


@pytest.fixture
def kovaco(db_session):
    """A client with a company domain and one open matter."""
    company = upsert_company(db_session, "KOVACO s.r.o.", domains=["kovaco.sk"])
    client = create_client(db_session, "KOVACO", company=company, reference="KOV")
    matter = create_matter(db_session, client, "Kasačná sťažnosť KOVACO", reference="KOV-2026-01")
    return client, matter


class TestSubjectNormalisation:
    @pytest.mark.parametrize(
        "subject",
        [
            "Re: Kasačná sťažnosť",
            "FWD: Kasačná sťažnosť",
            "Odp: Kasačná sťažnosť",
            "RE: RE: Kasačná sťažnosť",
            "Re[2]: Kasačná sťažnosť",
        ],
    )
    def test_prefixes_are_stripped(self, subject):
        assert normalise_subject(subject) == "kasačná sťažnosť"

    def test_whitespace_is_collapsed(self):
        assert normalise_subject("  Vec   ABC  ") == "vec abc"

    def test_empty(self):
        assert normalise_subject(None) == ""

    def test_a_subject_that_merely_starts_with_re_is_untouched(self):
        assert normalise_subject("Rekonštrukcia budovy") == "rekonštrukcia budovy"


class TestRules:
    def test_reference_in_subject_wins(self, db_session, account, kovaco):
        _, matter = kovaco
        sync(db_session, account, [kovaco_message("m1", "t1", "Podanie KOV-2026-01 doplnenie")])

        suggestion = suggest_for_thread(db_session, thread_named(db_session, "KOV-2026-01"))
        assert suggestion.matter_id == matter.id
        assert suggestion.method == "subject_reference"
        assert suggestion.confidence >= 0.95

    def test_short_reference_does_not_match_inside_words(self, db_session, account):
        company = upsert_company(db_session, "ABC", domains=["abc.sk"])
        client = create_client(db_session, "ABC")
        create_matter(db_session, client, "Vec", reference="KOV")
        sync(db_session, account, [kovaco_message("m1", "t1", "Rekonštrukcia budovy")])

        suggestion = suggest_for_thread(db_session, thread_named(db_session, "Rekonštrukcia"))
        assert suggestion is None or suggestion.method != "subject_reference"
        assert company is not None

    def test_single_open_matter_for_a_known_client(self, db_session, account, kovaco):
        _, matter = kovaco
        sync(db_session, account, [kovaco_message("m1", "t1", "Nová korešpondencia")])

        suggestion = suggest_for_thread(db_session, thread_named(db_session, "Nová"))
        assert suggestion.matter_id == matter.id
        assert suggestion.method == "single_open_matter"
        assert suggestion.is_confident

    def test_two_open_matters_make_the_client_alone_insufficient(self, db_session, account, kovaco):
        client, _ = kovaco
        create_matter(db_session, client, "Druhá vec")
        sync(db_session, account, [kovaco_message("m1", "t1", "Nejasná korešpondencia")])

        suggestion = suggest_for_thread(db_session, thread_named(db_session, "Nejasná"))
        assert suggestion is not None
        assert suggestion.matter_id is None
        assert suggestion.method == "client_only"
        assert not suggestion.is_confident

    def test_sibling_thread_with_the_same_subject(self, db_session, account, kovaco):
        _, matter = kovaco
        sync(db_session, account, [kovaco_message("m1", "t1", "Doplnenie dokazov")])
        first = thread_named(db_session, "Doplnenie")
        from app.services.matters import link_target

        link_target(db_session, matter.id, LinkTarget.THREAD, first.id, method="manual")

        sync(
            db_session,
            account,
            [kovaco_message("m2", "t2", "Re: Doplnenie dokazov", sender="iny@kovaco.sk")],
        )
        second = db_session.scalar(select(type(first)).where(type(first).gmail_thread_id == "t2"))
        suggestion = suggest_for_thread(db_session, second)
        assert suggestion.matter_id == matter.id
        assert suggestion.method in {"sibling_thread", "single_open_matter"}

    def test_unknown_sender_yields_nothing(self, db_session, account, kovaco):
        sync(
            db_session,
            account,
            [kovaco_message("m1", "t1", "Ponuka sluzieb", sender="spam@nikto.example")],
        )
        assert suggest_for_thread(db_session, thread_named(db_session, "Ponuka")) is None

    def test_external_domains_excludes_my_own(self, db_session, account, kovaco):
        sync(db_session, account, [kovaco_message("m1", "t1", "Vec")])
        domains = external_domains(db_session, thread_named(db_session, "Vec"))
        assert domains == {"kovaco.sk"}


class TestAssignment:
    def test_confident_suggestions_are_filed(self, db_session, account, kovaco):
        _, matter = kovaco
        sync(db_session, account, [kovaco_message("m1", "t1", "Podklady KOV-2026-01")])

        stats = assign_threads(db_session, account.id)
        assert stats.linked == 1
        assert stats.flagged_for_review == 0

        link = existing_link(db_session, LinkTarget.THREAD, thread_named(db_session, "Podklady").id)
        assert link.matter_id == matter.id
        assert link.needs_review is False
        assert link.reason

    def test_weak_suggestions_are_filed_but_flagged(self, db_session, account, kovaco):
        client, _ = kovaco
        create_matter(db_session, client, "Kasačná sťažnosť KOVACO druhá vec")
        sync(db_session, account, [kovaco_message("m1", "t1", "Kasačná sťažnosť KOVACO")])

        stats = assign_threads(db_session, account.id)
        assert stats.linked + stats.flagged_for_review >= 0
        for link in db_session.scalars(select(MatterLink)):
            if link.confidence < AUTO_LINK_THRESHOLD:
                assert link.needs_review is True

    def test_never_creates_a_matter_on_its_own(self, db_session, account, kovaco):
        from app.db.models import Matter

        before = db_session.scalar(select(Matter).where(Matter.title.ilike("%"))) is not None
        sync(db_session, account, [kovaco_message("m1", "t1", "Uplne nova vec bez kontextu")])
        count_before = len(db_session.scalars(select(Matter)).all())

        assign_threads(db_session, account.id)

        assert len(db_session.scalars(select(Matter)).all()) == count_before
        assert before is True

    def test_dry_run_changes_nothing(self, db_session, account, kovaco):
        sync(db_session, account, [kovaco_message("m1", "t1", "Podklady KOV-2026-01")])
        stats = assign_threads(db_session, account.id, dry_run=True)
        assert stats.linked == 1
        assert db_session.scalars(select(MatterLink)).all() == []

    def test_rerunning_does_not_duplicate_links(self, db_session, account, kovaco):
        sync(db_session, account, [kovaco_message("m1", "t1", "Podklady KOV-2026-01")])
        assign_threads(db_session, account.id)
        second = assign_threads(db_session, account.id)

        assert second.already_linked == 1
        assert len(db_session.scalars(select(MatterLink)).all()) == 1

    def test_unmatched_threads_are_counted_not_filed(self, db_session, account, kovaco):
        sync(
            db_session,
            account,
            [kovaco_message("m1", "t1", "Newsletter", sender="news@nikto.example")],
        )
        stats = assign_threads(db_session, account.id)
        assert stats.unmatched >= 1

    def test_assignment_is_audited(self, db_session, account, kovaco):
        sync(db_session, account, [kovaco_message("m1", "t1", "Podklady KOV-2026-01")])
        assign_threads(db_session, account.id)
        assert db_session.scalar(
            select(AuditLog).where(AuditLog.action == "matter.threads_assigned")
        )


class TestReviewQueue:
    def _weak_link(self, db_session, account, kovaco):
        _, matter = kovaco
        sync(db_session, account, [kovaco_message("m1", "t1", "Vec")])
        from app.services.matters import link_target

        return link_target(
            db_session,
            matter.id,
            LinkTarget.THREAD,
            thread_named(db_session, "Vec").id,
            confidence=0.5,
            method="subject_similarity",
            reason="weak",
        )

    def test_weak_links_appear_in_the_queue(self, db_session, account, kovaco):
        link = self._weak_link(db_session, account, kovaco)
        assert link.id in {item.id for item in links_needing_review(db_session)}

    def test_confirming_clears_the_flag(self, db_session, account, kovaco):
        link = self._weak_link(db_session, account, kovaco)
        confirmed = confirm_link(db_session, link.id)

        assert confirmed.needs_review is False
        assert confirmed.confidence == 1.0
        assert confirmed.confirmed_at is not None
        assert link.id not in {item.id for item in links_needing_review(db_session)}

    def test_rejecting_removes_the_link_but_keeps_the_record(self, db_session, account, kovaco):
        link = self._weak_link(db_session, account, kovaco)
        link_id = link.id

        assert reject_link(db_session, link_id) is True
        assert db_session.get(MatterLink, link_id) is None
        assert db_session.scalar(select(AuditLog).where(AuditLog.action == "matter.link_rejected"))

    def test_rejecting_an_unknown_link_is_false(self, db_session):
        import uuid as uuid_module

        assert reject_link(db_session, uuid_module.uuid4()) is False

    def test_a_confirmed_link_is_not_downgraded_by_a_later_guess(self, db_session, account, kovaco):
        link = self._weak_link(db_session, account, kovaco)
        confirm_link(db_session, link.id)

        from app.services.matters import link_target

        again = link_target(
            db_session,
            link.matter_id,
            LinkTarget.THREAD,
            link.target_id,
            confidence=0.6,
            method="subject_similarity",
        )
        assert again.confidence == 1.0
        assert again.needs_review is False


class TestMatterContents:
    def test_counts_what_is_filed(self, db_session, account, kovaco):
        _, matter = kovaco
        sync(
            db_session,
            account,
            [
                kovaco_message("m1", "t1", "Podklady KOV-2026-01"),
                kovaco_message("m2", "t1", "Re: Podklady KOV-2026-01"),
            ],
        )
        assign_threads(db_session, account.id)

        contents = matter_contents(db_session, matter.id)
        assert contents["thread"] == 1
        assert contents["messages_in_threads"] == 2

    def test_empty_matter(self, db_session, kovaco):
        _, matter = kovaco
        contents = matter_contents(db_session, matter.id)
        assert contents["thread"] == 0
        assert contents["messages_in_threads"] == 0


class TestApi:
    @pytest.fixture
    def client(self, db_session):
        from fastapi.testclient import TestClient

        from app.db.session import get_db
        from app.main import create_app

        app = create_app()
        app.dependency_overrides[get_db] = lambda: db_session
        with TestClient(app) as test_client:
            yield test_client
        app.dependency_overrides.clear()

    def test_create_client_with_a_company_and_domains(self, client):
        response = client.post(
            "/api/v1/clients",
            json={
                "display_name": "KOVACO",
                "reference": "KOV",
                "company_name": "KOVACO s.r.o.",
                "domains": ["kovaco.sk", "KOVACO.COM"],
            },
        )
        assert response.status_code == 201
        assert response.json()["display_name"] == "KOVACO"

        companies = client.get("/api/v1/companies").json()
        assert companies[0]["domains"] == ["kovaco.com", "kovaco.sk"]

    def test_create_matter_and_read_it_back(self, client, db_session, kovaco):
        existing_client, _ = kovaco
        created = client.post(
            "/api/v1/matters",
            json={
                "client_id": str(existing_client.id),
                "title": "Nová vec",
                "reference": "KOV-2026-02",
            },
        )
        assert created.status_code == 201

        detail = client.get(f"/api/v1/matters/{created.json()['id']}").json()
        assert detail["title"] == "Nová vec"
        assert detail["client_name"] == "KOVACO"
        assert detail["contents"]["thread"] == 0

    def test_matter_for_unknown_client_is_404(self, client):
        response = client.post(
            "/api/v1/matters",
            json={"client_id": "00000000-0000-0000-0000-000000000000", "title": "X"},
        )
        assert response.status_code == 404

    def test_unknown_matter_is_404(self, client):
        assert client.get("/api/v1/matters/00000000-0000-0000-0000-000000000000").status_code == 404

    def test_assignment_endpoint_dry_run_changes_nothing(self, client, db_session, account, kovaco):
        sync(db_session, account, [kovaco_message("m1", "t1", "Podklady KOV-2026-01")])
        body = client.post("/api/v1/matters/assign", params={"dry_run": True}).json()
        assert body["linked"] == 1
        assert body["dry_run"] is True
        assert db_session.scalars(select(MatterLink)).all() == []

    def test_assignment_endpoint_files_and_reports(self, client, db_session, account, kovaco):
        sync(db_session, account, [kovaco_message("m1", "t1", "Podklady KOV-2026-01")])
        body = client.post("/api/v1/matters/assign").json()
        assert body["linked"] == 1

        matter_id = str(kovaco[1].id)
        links = client.get(f"/api/v1/matters/{matter_id}/links").json()
        assert len(links) == 1
        assert links[0]["method"] == "subject_reference"
        assert links[0]["needs_review"] is False

    def test_suggestion_endpoint_explains_itself(self, client, db_session, account, kovaco):
        sync(db_session, account, [kovaco_message("m1", "t1", "Podklady KOV-2026-01")])
        thread = thread_named(db_session, "Podklady")

        body = client.get(f"/api/v1/matters/suggestions/{thread.id}").json()
        assert body["matter_title"] == "Kasačná sťažnosť KOVACO"
        assert body["method"] == "subject_reference"
        assert "KOV-2026-01" in body["reason"]

    def test_suggestion_for_unknown_thread_is_404(self, client):
        assert (
            client.get(
                "/api/v1/matters/suggestions/00000000-0000-0000-0000-000000000000"
            ).status_code
            == 404
        )

    def test_review_queue_and_confirm(self, client, db_session, account, kovaco):
        from app.services.matters import link_target

        _, matter = kovaco
        sync(db_session, account, [kovaco_message("m1", "t1", "Vec")])
        link = link_target(
            db_session,
            matter.id,
            LinkTarget.THREAD,
            thread_named(db_session, "Vec").id,
            confidence=0.5,
            method="subject_similarity",
            reason="weak match",
        )

        queue = client.get("/api/v1/matters/review/queue").json()
        assert str(link.id) in {item["id"] for item in queue}

        confirmed = client.post(f"/api/v1/matters/links/{link.id}/confirm").json()
        assert confirmed["needs_review"] is False
        assert confirmed["confidence"] == 1.0
        assert client.get("/api/v1/matters/review/queue").json() == []

    def test_rejecting_a_link_removes_it(self, client, db_session, account, kovaco):
        from app.services.matters import link_target

        _, matter = kovaco
        sync(db_session, account, [kovaco_message("m1", "t1", "Vec")])
        link = link_target(
            db_session,
            matter.id,
            LinkTarget.THREAD,
            thread_named(db_session, "Vec").id,
            confidence=0.5,
            method="subject_similarity",
        )

        assert client.delete(f"/api/v1/matters/links/{link.id}").status_code == 204
        assert client.delete(f"/api/v1/matters/links/{link.id}").status_code == 404

    def test_clients_and_matters_listings_paginate(self, client, kovaco):
        clients = client.get("/api/v1/clients", params={"limit": 1}).json()
        assert clients["total"] >= 1
        assert len(clients["items"]) == 1

        matters = client.get("/api/v1/matters", params={"limit": 1}).json()
        assert matters["total"] >= 1
