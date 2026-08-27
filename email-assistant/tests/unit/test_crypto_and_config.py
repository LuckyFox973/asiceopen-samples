import base64

import pytest

from app.core.config import GMAIL_SCOPES_READONLY, Settings
from app.core.crypto import (
    EncryptionNotConfiguredError,
    TokenCipher,
    generate_key,
    is_fernet_key,
)
from app.core.startup import verify_configuration


class TestTokenCipher:
    def test_round_trip(self):
        cipher = TokenCipher(generate_key())
        secret = "1//0abcdefRefreshToken-with-ünicode"
        assert cipher.decrypt(cipher.encrypt(secret)) == secret

    def test_ciphertext_is_not_plaintext(self):
        cipher = TokenCipher(generate_key())
        assert "RefreshToken" not in cipher.encrypt("RefreshToken")

    def test_same_plaintext_encrypts_differently_each_time(self):
        cipher = TokenCipher(generate_key())
        assert cipher.encrypt("x") != cipher.encrypt("x")

    def test_missing_key_is_refused_loudly(self):
        with pytest.raises(EncryptionNotConfiguredError):
            TokenCipher("")

    def test_passphrase_is_derived_deterministically(self):
        a, b = TokenCipher("dev passphrase"), TokenCipher("dev passphrase")
        assert b.decrypt(a.encrypt("token")) == "token"

    def test_wrong_key_cannot_decrypt(self):
        blob = TokenCipher(generate_key()).encrypt("token")
        with pytest.raises(ValueError):
            TokenCipher(generate_key()).decrypt(blob)

    def test_generated_key_is_a_real_fernet_key(self):
        key = generate_key()
        assert is_fernet_key(key)
        assert len(base64.urlsafe_b64decode(key)) == 32

    def test_passphrase_is_not_a_fernet_key(self):
        assert not is_fernet_key("dev passphrase")


class TestScopes:
    def test_mvp_requests_no_write_access(self):
        joined = " ".join(GMAIL_SCOPES_READONLY)
        for forbidden in ("gmail.send", "gmail.modify", "mail.google.com", "gmail.compose"):
            assert forbidden not in joined

    def test_readonly_scope_is_present(self):
        assert "https://www.googleapis.com/auth/gmail.readonly" in GMAIL_SCOPES_READONLY


class TestStartupChecks:
    def _settings(self, **overrides) -> Settings:
        base = {
            "app_env": "production",
            "token_encryption_key": generate_key(),
            "google_client_id": "id",
            "google_client_secret": "secret",
            "google_oauth_redirect_uri": "https://assistant.example.com/cb",
            "job_auth_token": "token",
            "attachment_backend": "gcs",
            "attachment_gcs_bucket": "bucket",
            "database_url": "postgresql+psycopg://u:p@10.0.0.3:5432/db",
            "_env_file": None,
        }
        base.update(overrides)
        return Settings(**base)

    def test_well_configured_production_has_no_problems(self):
        assert verify_configuration(self._settings()) == []

    def test_missing_encryption_key_is_reported(self):
        problems = verify_configuration(self._settings(token_encryption_key=""))
        assert any("TOKEN_ENCRYPTION_KEY" in p for p in problems)

    def test_derived_passphrase_rejected_in_production(self):
        problems = verify_configuration(self._settings(token_encryption_key="passphrase"))
        assert any("Fernet" in p for p in problems)

    def test_local_attachments_rejected_in_production(self):
        problems = verify_configuration(
            self._settings(attachment_backend="local", attachment_gcs_bucket="")
        )
        assert any("ephemeral" in p for p in problems)

    def test_missing_job_token_reported_in_production(self):
        problems = verify_configuration(self._settings(job_auth_token=""))
        assert any("JOB_AUTH_TOKEN" in p for p in problems)

    def test_plain_http_redirect_reported(self):
        problems = verify_configuration(self._settings(google_oauth_redirect_uri="http://x/cb"))
        assert any("HTTPS" in p for p in problems)

    def test_localhost_database_reported(self):
        problems = verify_configuration(
            self._settings(database_url="postgresql+psycopg://u:p@localhost/db")
        )
        assert any("localhost" in p for p in problems)

    def test_development_tolerates_local_setup(self):
        settings = Settings(
            app_env="development",
            token_encryption_key="dev passphrase",
            google_client_id="id",
            google_client_secret="secret",
            attachment_backend="local",
            _env_file=None,
        )
        assert verify_configuration(settings) == []
