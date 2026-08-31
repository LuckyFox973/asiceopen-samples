"""Changing a setting without breaking the file it lives in."""

from __future__ import annotations

import pytest

from app.services.envfile import Setting, get, read, set_value

ORIGINAL = """# Database
DATABASE_URL=postgresql://localhost/x

# Sync
SYNC_START_DATE=2026-01-01
GMAIL_WRITE_ENABLED=true
"""


@pytest.fixture
def env(tmp_path):
    path = tmp_path / ".env"
    path.write_text(ORIGINAL)
    return path


class TestReading:
    def test_comments_are_not_settings(self, env):
        assert [s.name for s in read(env)] == [
            "DATABASE_URL",
            "SYNC_START_DATE",
            "GMAIL_WRITE_ENABLED",
        ]

    def test_quotes_are_stripped_from_a_value(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text('NAME="quoted value"\n')
        assert get(path, "NAME").value == "quoted value"

    def test_an_export_prefix_is_understood(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("export NAME=value\n")
        assert get(path, "NAME").value == "value"

    def test_a_missing_file_holds_nothing(self, tmp_path):
        assert read(tmp_path / "nope.env") == []


class TestSecrets:
    @pytest.mark.parametrize(
        "name",
        [
            "GOOGLE_CLIENT_SECRET",
            "TOKEN_ENCRYPTION_KEY",
            "BACKUP_ENCRYPTION_KEY",
            "JOB_AUTH_TOKEN",
            "DATABASE_URL",
        ],
    )
    def test_a_credential_is_never_printed(self, name):
        """Terminal output gets pasted into chats."""
        setting = Setting(name, "hunter2hunter2", 0)
        assert setting.secret
        assert "hunter2" not in setting.display()

    def test_an_ordinary_setting_shows_its_value(self):
        assert Setting("SYNC_START_DATE", "2026-01-01", 0).display() == "2026-01-01"


class TestWriting:
    def test_a_new_setting_is_appended(self, env):
        created, previous = set_value(env, "DRIVE_WRITE_ENABLED", "true")
        assert created is True
        assert previous == ""
        assert get(env, "DRIVE_WRITE_ENABLED").value == "true"

    def test_an_existing_setting_is_replaced_in_place(self, env):
        created, previous = set_value(env, "SYNC_START_DATE", "2025-01-01")
        assert created is False
        assert previous == "2026-01-01"
        assert env.read_text().count("SYNC_START_DATE") == 1

    def test_the_rest_of_the_file_is_untouched(self, env):
        set_value(env, "SYNC_START_DATE", "2025-01-01")
        text = env.read_text()
        assert "# Database" in text
        assert "DATABASE_URL=postgresql://localhost/x" in text
        assert "# Sync" in text

    def test_the_file_always_ends_with_a_newline(self, tmp_path):
        """The bug this module exists for: appending onto an unterminated
        last line glued two settings together and every command then died."""
        path = tmp_path / ".env"
        path.write_text("SYNC_START_DATE=2026-01-01")  # no trailing newline
        set_value(path, "DRIVE_WRITE_ENABLED", "true")

        lines = path.read_text().splitlines()
        assert lines == ["SYNC_START_DATE=2026-01-01", "DRIVE_WRITE_ENABLED=true"]
        assert path.read_text().endswith("\n")

    def test_writing_into_a_missing_file_creates_it(self, tmp_path):
        path = tmp_path / "nested" / ".env"
        set_value(path, "NAME", "value")
        assert path.read_text() == "NAME=value\n"

    def test_a_commented_out_setting_is_not_mistaken_for_the_real_one(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("# DRIVE_WRITE_ENABLED=false\nOTHER=1\n")
        created, _ = set_value(path, "DRIVE_WRITE_ENABLED", "true")

        assert created is True
        assert "# DRIVE_WRITE_ENABLED=false" in path.read_text()
        assert get(path, "DRIVE_WRITE_ENABLED").value == "true"

    def test_the_name_is_upper_cased(self, env):
        set_value(env, "drive_write_enabled", "true")
        assert "DRIVE_WRITE_ENABLED=true" in env.read_text()
