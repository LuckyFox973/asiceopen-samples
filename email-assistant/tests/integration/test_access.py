"""API keys, OAuth state verification, and HTTP-level authentication."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.deps import require_api_key
from app.core.config import Settings, get_settings
from app.core.security import generate_oauth_state
from app.db.models import ApiKey, AuditLog, Contact, OAuthState
from app.db.session import get_db
from app.main import create_app
from app.services.access import (
    consume_oauth_state,
    create_api_key,
    list_api_keys,
    purge_expired_oauth_states,
    record_oauth_state,
    revoke_api_key,
    verify_api_key,
)
from app.services.maintenance import find_unreferenced_blobs, prune_orphan_contacts
from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


def _orphan_count(session) -> int:
    """Contacts no message refers to, right now."""
    return prune_orphan_contacts(session, dry_run=True)


class TestApiKeyLifecycle:
    def test_created_key_verifies(self, db_session):
        issued = create_api_key(db_session, "mcp")
        assert verify_api_key(db_session, issued.key) is not None

    def test_plaintext_key_is_never_stored(self, db_session):
        issued = create_api_key(db_session, "mcp")
        stored = db_session.scalar(select(ApiKey).where(ApiKey.id == issued.record.id))
        assert issued.key not in (stored.key_hash, stored.prefix, stored.name)
        assert stored.key_hash != issued.key

    def test_wrong_key_is_rejected(self, db_session):
        create_api_key(db_session, "mcp")
        assert verify_api_key(db_session, "eaa_not-a-real-key") is None

    def test_empty_key_is_rejected(self, db_session):
        assert verify_api_key(db_session, "") is None

    def test_revoked_key_stops_working(self, db_session):
        issued = create_api_key(db_session, "mcp")
        revoke_api_key(db_session, issued.record.prefix)
        assert verify_api_key(db_session, issued.key) is None

    def test_expired_key_stops_working(self, db_session):
        issued = create_api_key(db_session, "temp", expires_in_days=1)
        issued.record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.flush()
        assert verify_api_key(db_session, issued.key) is None

    def test_use_is_recorded(self, db_session):
        issued = create_api_key(db_session, "mcp")
        assert issued.record.last_used_at is None
        verify_api_key(db_session, issued.key)
        assert issued.record.last_used_at is not None

    def test_listing_hides_revoked_by_default(self, db_session):
        live = create_api_key(db_session, "live")
        dead = create_api_key(db_session, "dead")
        revoke_api_key(db_session, dead.record.prefix)

        names = {k.name for k in list_api_keys(db_session)}
        assert names == {"live"}
        assert {k.name for k in list_api_keys(db_session, include_revoked=True)} == {
            "live",
            "dead",
        }
        assert live.record.revoked_at is None

    def test_revoking_twice_is_harmless(self, db_session):
        issued = create_api_key(db_session, "mcp")
        first = revoke_api_key(db_session, issued.record.prefix)
        second = revoke_api_key(db_session, issued.record.prefix)
        assert second.revoked_at == first.revoked_at

    def test_revoking_unknown_key_reports_nothing(self, db_session):
        assert revoke_api_key(db_session, "eaa_nonexist") is None

    def test_creation_and_revocation_are_audited(self, db_session):
        issued = create_api_key(db_session, "mcp")
        revoke_api_key(db_session, issued.record.prefix)
        actions = set(
            db_session.scalars(
                select(AuditLog.action).where(AuditLog.entity_id == str(issued.record.id))
            ).all()
        )
        assert actions == {"api_key.created", "api_key.revoked"}


class TestOAuthState:
    def test_issued_state_is_accepted_once(self, db_session):
        state = record_oauth_state(db_session, generate_oauth_state())
        assert consume_oauth_state(db_session, state)[0] is True
        assert consume_oauth_state(db_session, state)[0] is False

    def test_unknown_state_is_rejected(self, db_session):
        assert consume_oauth_state(db_session, "forged-state")[0] is False

    def test_missing_state_is_rejected(self, db_session):
        assert consume_oauth_state(db_session, None)[0] is False
        assert consume_oauth_state(db_session, "")[0] is False

    def test_expired_state_is_rejected(self, db_session):
        state = record_oauth_state(db_session, generate_oauth_state())
        record = db_session.scalar(select(OAuthState).where(OAuthState.state == state))
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db_session.flush()
        assert consume_oauth_state(db_session, state)[0] is False


class TestPkceVerifier:
    """The authorisation URL carries only a hash of a secret the flow invents.

    The callback runs in another process and builds a different Flow, so unless
    that secret is stored with the state and handed back, Google answers the
    exchange with "(invalid_grant) Missing code verifier" — which is exactly
    what happened the first time a real mailbox was connected.
    """

    def test_the_verifier_survives_the_round_trip(self, db_session):
        record_oauth_state(db_session, "state-abc", "verifier-xyz")
        accepted, verifier = consume_oauth_state(db_session, "state-abc")
        assert accepted is True
        assert verifier == "verifier-xyz"

    def test_a_state_stored_without_one_returns_none(self, db_session):
        record_oauth_state(db_session, "state-plain")
        assert consume_oauth_state(db_session, "state-plain") == (True, None)

    def test_a_rejected_state_yields_no_verifier(self, db_session):
        record_oauth_state(db_session, "state-once", "verifier-once")
        consume_oauth_state(db_session, "state-once")
        assert consume_oauth_state(db_session, "state-once") == (False, None)

    def test_the_url_builder_produces_a_verifier_to_store(self, monkeypatch):
        """PKCE is on by default, so a verifier must come back with the URL."""
        from app.core.config import Settings
        from app.gmail.oauth import build_authorisation_url

        settings = Settings(
            google_client_id="test-client-id.apps.googleusercontent.com",
            google_client_secret="test-secret",
            google_oauth_redirect_uri="http://localhost:8000/api/v1/auth/google/callback",
            _env_file=None,
        )
        url, state, verifier = build_authorisation_url(settings)

        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url
        assert verifier, "the URL uses PKCE, so a verifier must be returned with it"
        assert state in url

    def test_the_verifier_reaches_the_token_exchange(self, monkeypatch):
        """The value stored with the state must be the one sent to Google."""
        from app.core.config import Settings
        from app.gmail import oauth as oauth_module

        seen = {}

        class FakeFlow:
            code_verifier = None

            def fetch_token(self, code):
                seen["code"] = code
                seen["verifier"] = self.code_verifier

            @property
            def credentials(self):
                return "creds"

        monkeypatch.setattr(oauth_module, "build_flow", lambda *a, **k: FakeFlow())
        result = oauth_module.exchange_code(
            "auth-code",
            state="s",
            settings=Settings(_env_file=None),
            code_verifier="the-verifier",
        )
        assert result == "creds"
        assert seen == {"code": "auth-code", "verifier": "the-verifier"}

    def test_expired_states_are_purged(self, db_session):
        state = record_oauth_state(db_session, generate_oauth_state())
        record = db_session.scalar(select(OAuthState).where(OAuthState.state == state))
        record.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db_session.flush()
        assert purge_expired_oauth_states(db_session) >= 1
        assert db_session.scalar(select(OAuthState).where(OAuthState.state == state)) is None


class TestMaintenance:
    def test_orphan_contacts_are_removed(self, db_session):
        address = f"orphan-{uuid.uuid4().hex[:8]}@example.sk"
        db_session.add(Contact(primary_address=address, domain="example.sk"))
        db_session.flush()

        # Counted as a delta: the shared test database may hold other rows.
        before = _orphan_count(db_session)
        removed = prune_orphan_contacts(db_session)

        assert removed == before
        assert db_session.scalar(select(Contact).where(Contact.primary_address == address)) is None

    def test_referenced_contacts_survive(self, db_session, account):
        from tests.fixtures import gmail_message
        from tests.fixtures.fake_gmail import FakeGmailClient
        from tests.integration.test_sync import engine_for

        client = FakeGmailClient([gmail_message(from_="klient@abc.sk", to="info@foxgroup.sk")])
        engine_for(db_session, account, client).initial_sync()

        prune_orphan_contacts(db_session)
        remaining = set(db_session.scalars(select(Contact.primary_address)).all())
        assert "klient@abc.sk" in remaining

    def test_dry_run_counts_without_deleting(self, db_session):
        address = f"orphan-{uuid.uuid4().hex[:8]}@example.sk"
        before = _orphan_count(db_session)
        db_session.add(Contact(primary_address=address))
        db_session.flush()

        assert prune_orphan_contacts(db_session, dry_run=True) == before + 1
        # Still there, and still counted — a dry run deletes nothing.
        assert prune_orphan_contacts(db_session, dry_run=True) == before + 1
        assert db_session.scalar(select(Contact).where(Contact.primary_address == address))

    def test_pruning_is_audited(self, db_session):
        db_session.add(Contact(primary_address=f"orphan-{uuid.uuid4().hex[:8]}@example.sk"))
        db_session.flush()
        prune_orphan_contacts(db_session)
        assert db_session.scalar(
            select(AuditLog).where(AuditLog.action == "maintenance.prune_contacts")
        )

    def test_unreferenced_blobs_are_reported_not_deleted(self, db_session):
        from app.db.models import AttachmentBlob

        blob = AttachmentBlob(
            sha256="f" * 64, size_bytes=10, storage_backend="local", storage_key="ff/ff/x"
        )
        db_session.add(blob)
        db_session.flush()

        found = find_unreferenced_blobs(db_session)
        assert blob.sha256 in {b.sha256 for b in found}
        assert db_session.get(AttachmentBlob, blob.id) is not None


class TestHttpAuthentication:
    @pytest.fixture
    def secured_client(self, db_session):
        """A client with API authentication switched on, as in production."""
        app = create_app()
        secured = Settings(app_env="development", api_auth_enabled=True, _env_file=None)
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_settings] = lambda: secured
        with TestClient(app) as client:
            yield client
        app.dependency_overrides.clear()

    def test_health_stays_open(self, secured_client):
        assert secured_client.get("/health").status_code == 200

    def test_data_endpoint_requires_a_key(self, secured_client):
        response = secured_client.get("/api/v1/messages/search")
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_valid_key_is_accepted(self, secured_client, db_session):
        issued = create_api_key(db_session, "test")
        response = secured_client.get(
            "/api/v1/messages/search",
            headers={"Authorization": f"Bearer {issued.key}"},
        )
        assert response.status_code == 200

    def test_invalid_key_is_rejected(self, secured_client):
        response = secured_client.get(
            "/api/v1/messages/search", headers={"Authorization": "Bearer eaa_wrong"}
        )
        assert response.status_code == 401

    def test_revoked_key_is_rejected(self, secured_client, db_session):
        issued = create_api_key(db_session, "test")
        revoke_api_key(db_session, issued.record.prefix)
        response = secured_client.get(
            "/api/v1/messages/search",
            headers={"Authorization": f"Bearer {issued.key}"},
        )
        assert response.status_code == 401

    def test_wrong_scheme_is_rejected(self, secured_client, db_session):
        issued = create_api_key(db_session, "test")
        response = secured_client.get(
            "/api/v1/messages/search", headers={"Authorization": f"Basic {issued.key}"}
        )
        assert response.status_code == 401

    def test_sync_control_requires_a_key(self, secured_client, account):
        assert secured_client.get(f"/api/v1/accounts/{account.id}/sync/status").status_code == 401

    def test_oauth_start_requires_a_key(self, secured_client):
        assert secured_client.post("/api/v1/auth/google/start").status_code == 401

    def test_callback_rejects_a_forged_state(self, secured_client):
        response = secured_client.get(
            "/api/v1/auth/google/callback",
            params={"code": "whatever", "state": "forged"},
        )
        assert response.status_code == 400
        assert "did not match" in response.text

    def test_callback_rejects_a_reused_state(self, secured_client, db_session):
        state = record_oauth_state(db_session, generate_oauth_state())
        consume_oauth_state(db_session, state)
        response = secured_client.get(
            "/api/v1/auth/google/callback", params={"code": "x", "state": state}
        )
        assert response.status_code == 400


class TestDevelopmentStaysOpen:
    def test_no_key_needed_in_development(self, db_session):
        app = create_app()
        app.dependency_overrides[get_db] = lambda: db_session
        with TestClient(app) as client:
            assert client.get("/api/v1/messages/search").status_code == 200
        app.dependency_overrides.clear()

    def test_production_requires_auth_even_if_disabled(self):
        settings = Settings(app_env="production", api_auth_enabled=False, _env_file=None)
        assert settings.require_api_auth is True

    def test_dependency_is_a_no_op_when_auth_is_off(self, db_session):
        settings = Settings(app_env="development", _env_file=None)
        assert require_api_key(None, db_session, settings) is None
