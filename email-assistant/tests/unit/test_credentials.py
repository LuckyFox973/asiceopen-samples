"""Reading Google's downloaded OAuth client file."""

from __future__ import annotations

import json
import stat

import pytest

from app.services.credentials import (
    CredentialsError,
    check_redirect_uris,
    find_download,
    parse,
    write_env,
)

EXPECTED_URI = "http://localhost:8000/api/v1/auth/google/callback"
LOOPBACK_URI = "http://127.0.0.1:8000/api/v1/auth/google/callback"

SECRET = "GOCSPX-thisIsTheSecretValue1234"


def write_client(tmp_path, name="client_secret_1.json", kind="web", **overrides):
    block = {
        "client_id": "123456789012-abcdefghijklmnop.apps.googleusercontent.com",
        "project_id": "email-assistant-471209",
        "client_secret": SECRET,
        "redirect_uris": [EXPECTED_URI, LOOPBACK_URI],
    }
    block.update(overrides)
    path = tmp_path / name
    path.write_text(json.dumps({kind: block}))
    return path


class TestParsing:
    def test_reads_a_web_client(self, tmp_path):
        client = parse(write_client(tmp_path))
        assert client.client_secret == SECRET
        assert client.kind == "web"
        assert client.project_id == "email-assistant-471209"

    def test_reads_a_desktop_client(self, tmp_path):
        assert parse(write_client(tmp_path, kind="installed")).kind == "installed"

    def test_masked_id_hides_the_bulk_of_the_id(self, tmp_path):
        client = parse(write_client(tmp_path))
        assert client.masked_id.startswith("123456789012-")
        assert "abcdefghijklmnop" not in client.masked_id

    def test_a_service_account_key_is_rejected_with_a_hint(self, tmp_path):
        path = tmp_path / "sa.json"
        path.write_text(json.dumps({"type": "service_account", "private_key": "x"}))
        with pytest.raises(CredentialsError, match="service account"):
            parse(path)

    def test_malformed_json(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json")
        with pytest.raises(CredentialsError, match="not valid JSON"):
            parse(path)

    def test_missing_secret(self, tmp_path):
        with pytest.raises(CredentialsError, match="missing the client id or secret"):
            parse(write_client(tmp_path, client_secret=""))


class TestFinding:
    def test_explicit_path_wins(self, tmp_path):
        path = write_client(tmp_path)
        assert find_download(str(path)) == path

    def test_missing_explicit_path_is_reported(self, tmp_path):
        with pytest.raises(CredentialsError, match="No file at"):
            find_download(str(tmp_path / "nope.json"))

    def test_newest_download_wins(self, tmp_path, monkeypatch):
        import os
        import time

        old = write_client(tmp_path, "client_secret_old.json")
        time.sleep(0.01)
        new = write_client(tmp_path, "client_secret_new.json")
        os.utime(old, (time.time() - 500, time.time() - 500))

        monkeypatch.setattr("app.services.credentials.SEARCH_DIRS", (str(tmp_path),))
        assert find_download() == new

    def test_nothing_found_explains_where_to_get_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.services.credentials.SEARCH_DIRS", (str(tmp_path),))
        with pytest.raises(CredentialsError, match="Google Auth Platform"):
            find_download()


class TestRedirectChecks:
    """These turn a baffling redirect_uri_mismatch into a sentence beforehand."""

    def test_a_correct_client_has_no_problems(self, tmp_path):
        assert check_redirect_uris(parse(write_client(tmp_path)), EXPECTED_URI) == []

    def test_missing_loopback_spelling_is_flagged(self, tmp_path):
        client = parse(write_client(tmp_path, redirect_uris=[EXPECTED_URI]))
        problems = check_redirect_uris(client, EXPECTED_URI)
        assert any("127.0.0.1" in p for p in problems)

    def test_missing_localhost_spelling_is_flagged(self, tmp_path):
        client = parse(write_client(tmp_path, redirect_uris=[LOOPBACK_URI]))
        problems = check_redirect_uris(client, LOOPBACK_URI)
        assert any("localhost" in p for p in problems)

    def test_an_unrelated_uri_is_flagged_with_what_is_configured(self, tmp_path):
        client = parse(write_client(tmp_path, redirect_uris=["http://localhost:3000/callback"]))
        problems = check_redirect_uris(client, EXPECTED_URI)
        assert any("http://localhost:3000/callback" in p for p in problems)

    def test_no_redirect_uris_at_all(self, tmp_path):
        client = parse(write_client(tmp_path, redirect_uris=[]))
        problems = check_redirect_uris(client, EXPECTED_URI)
        assert any("no authorised redirect URIs" in p for p in problems)

    def test_a_desktop_client_is_flagged(self, tmp_path):
        client = parse(write_client(tmp_path, kind="installed"))
        problems = check_redirect_uris(client, EXPECTED_URI)
        assert any("Desktop app client" in p for p in problems)


class TestWritingEnv:
    def test_creates_from_the_template(self, tmp_path):
        template = tmp_path / ".env.example"
        template.write_text("SYNC_START_DATE=2026-01-01\nGOOGLE_CLIENT_ID=\n")
        env = tmp_path / ".env"

        write_env(env, {"GOOGLE_CLIENT_ID": "abc"}, template=template)

        text = env.read_text()
        assert "GOOGLE_CLIENT_ID=abc" in text
        assert "SYNC_START_DATE=2026-01-01" in text

    def test_replaces_without_disturbing_other_lines(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("A=1\nGOOGLE_CLIENT_ID=old\nB=2\n")

        write_env(env, {"GOOGLE_CLIENT_ID": "new"})

        assert env.read_text() == "A=1\nGOOGLE_CLIENT_ID=new\nB=2\n"

    def test_appends_a_key_that_is_not_there(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("A=1\n")
        write_env(env, {"GOOGLE_CLIENT_SECRET": "s"})
        assert "GOOGLE_CLIENT_SECRET=s" in env.read_text()

    def test_reports_only_what_changed(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("GOOGLE_CLIENT_ID=same\n")
        assert write_env(env, {"GOOGLE_CLIENT_ID": "same"}) == []
        assert write_env(env, {"GOOGLE_CLIENT_ID": "other"}) == ["GOOGLE_CLIENT_ID"]

    def test_the_file_holding_a_secret_is_owner_only(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("A=1\n")
        write_env(env, {"GOOGLE_CLIENT_SECRET": SECRET})
        assert stat.S_IMODE(env.stat().st_mode) & 0o077 == 0

    def test_a_secret_with_regex_characters_survives(self, tmp_path):
        """Backslashes in a replacement are a classic silent corruption."""
        env = tmp_path / ".env"
        env.write_text("GOOGLE_CLIENT_SECRET=old\n")
        awkward = r"GOCSPX-a\1b$0c\\d"

        write_env(env, {"GOOGLE_CLIENT_SECRET": awkward})

        assert f"GOOGLE_CLIENT_SECRET={awkward}" in env.read_text()
