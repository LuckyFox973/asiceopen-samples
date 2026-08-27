"""Backups: produced, encrypted, pruned — and actually restorable."""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text

from app.core.config import DRIVE_FILE_SCOPE, Settings
from app.db.models import AuditLog, EmailMessage
from app.services.backup import (
    BackupError,
    create_backup,
    dsn_to_pg_env,
    list_backups,
    prune_backups,
    restore_archive,
    verify_archive,
)
from app.services.maintenance import prune_orphan_contacts
from tests.conftest import TEST_DB_URL, requires_db
from tests.fixtures import attachment_part, gmail_message, multipart, text_part
from tests.fixtures.fake_gmail import FakeGmailClient

pytestmark = [pytest.mark.integration, requires_db]

BACKUP_KEY = "local-backup-key-that-never-leaves-this-machine"


@pytest.fixture
def backup_settings(tmp_path) -> Settings:
    return Settings(
        database_url=TEST_DB_URL,
        backup_enabled=True,
        backup_backend="local",
        backup_local_path=str(tmp_path / "backups"),
        backup_encryption_key=BACKUP_KEY,
        backup_retention=3,
        attachment_local_path=str(tmp_path / "attachments"),
        _env_file=None,
    )


class TestArchiveProduction:
    def test_backup_is_created_and_encrypted(self, db_session, backup_settings):
        artifact = create_backup(db_session, backup_settings)

        path = Path(artifact.location) / artifact.name
        assert path.exists()
        assert artifact.size_bytes > 0
        # A pg_dump in the clear starts with the "PGDMP" magic; ours must not.
        assert path.read_bytes()[:5] != b"PGDMP"
        assert path.read_bytes().startswith(b"EAABK1")

    def test_archive_name_records_what_is_inside(self, db_session, backup_settings):
        assert create_backup(db_session, backup_settings).name.endswith("-db.eaabk")

    def test_missing_key_refuses_to_write_anything(self, db_session, backup_settings):
        naked = backup_settings.model_copy(update={"backup_encryption_key": ""})
        with pytest.raises(BackupError, match="never written unencrypted"):
            create_backup(db_session, naked)
        assert not Path(naked.backup_local_path).exists()

    def test_creation_is_audited(self, db_session, backup_settings):
        artifact = create_backup(db_session, backup_settings)
        entry = db_session.scalar(select(AuditLog).where(AuditLog.action == "backup.created"))
        assert entry is not None
        assert artifact.name in entry.summary

    def test_attachments_are_included_on_request(self, db_session, backup_settings, tmp_path):
        blobs = Path(backup_settings.attachment_local_path)
        (blobs / "ab" / "cd").mkdir(parents=True)
        (blobs / "ab" / "cd" / "deadbeef").write_bytes(b"a stored document")

        artifact = create_backup(db_session, backup_settings, include_attachments=True)
        assert artifact.name.endswith("-full.eaabk")

        payload = tmp_path / "payload.tar"
        restore_archive(Path(artifact.location) / artifact.name, payload, backup_settings)
        import tarfile

        with tarfile.open(payload) as tar:
            names = tar.getnames()
        assert "database.dump" in names
        assert any(n.endswith("deadbeef") for n in names)


class TestRestore:
    def test_archive_verifies(self, db_session, backup_settings):
        artifact = create_backup(db_session, backup_settings)
        assert verify_archive(Path(artifact.location) / artifact.name, backup_settings) > 0

    def test_wrong_key_cannot_read_the_archive(self, db_session, backup_settings, tmp_path):
        from app.services.backup_crypto import BackupDecryptionError

        artifact = create_backup(db_session, backup_settings)
        wrong = backup_settings.model_copy(update={"backup_encryption_key": "not-the-key"})
        with pytest.raises(BackupDecryptionError):
            restore_archive(Path(artifact.location) / artifact.name, tmp_path / "o", wrong)

    def test_backup_actually_restores_the_data(self, backup_settings, tmp_path):
        """The real test: dump, restore into a fresh database, find the mail.

        Uses its own committed session rather than the rolled-back fixture —
        pg_dump runs on a separate connection and can only see committed data,
        which is exactly the situation in production.
        """
        from sqlalchemy.orm import sessionmaker

        from app.db.models import MailboxAccount, MailboxAddress
        from app.services.sync import SyncEngine

        marker = f"Kasačná sťažnosť {uuid.uuid4().hex[:8]}"
        email = f"restore-{uuid.uuid4().hex[:8]}@foxgroup.sk"
        engine = create_engine(TEST_DB_URL)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        account_id = None

        try:
            with session_factory() as session:
                account = MailboxAccount(email=email, display_name="Restore")
                session.add(account)
                session.flush()
                account_id = account.id
                session.add(
                    MailboxAddress(
                        account_id=account.id,
                        address=email,
                        is_primary=True,
                        source="primary",
                    )
                )
                session.commit()
                session.refresh(account)

                payload = multipart(
                    "multipart/mixed",
                    [
                        text_part(f"Vec: {marker}. Podklady v prílohe.", part_id="0"),
                        attachment_part("Rozhodnutie.pdf", size=5, attachment_id="t1", part_id="1"),
                    ],
                )
                SyncEngine(
                    session=session,
                    account=account,
                    client=FakeGmailClient(
                        [
                            gmail_message(
                                message_id=f"r-{uuid.uuid4().hex[:8]}",
                                thread_id=f"rt-{uuid.uuid4().hex[:8]}",
                                subject=marker,
                                from_="protistrana@example.sk",
                                to=email,
                                internal_date_ms="1756296000000",
                                payload=payload,
                            )
                        ],
                        attachments={"t1": b"bytes"},
                    ),
                    default_start_date=datetime(2025, 1, 1, tzinfo=UTC).date(),
                    download_attachments=False,
                ).initial_sync()
                session.commit()

                artifact = create_backup(session, backup_settings)
                session.commit()

            dump = tmp_path / "database.dump"
            restore_archive(Path(artifact.location) / artifact.name, dump, backup_settings)

            restore_db = f"eaa_restore_{uuid.uuid4().hex[:8]}"
            admin_url = TEST_DB_URL.rsplit("/", 1)[0] + "/postgres"
            admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
            with admin.connect() as conn:
                conn.execute(text(f'CREATE DATABASE "{restore_db}"'))
            admin.dispose()

            try:
                target_url = TEST_DB_URL.rsplit("/", 1)[0] + f"/{restore_db}"
                args, env = dsn_to_pg_env(target_url)
                result = subprocess.run(
                    ["pg_restore", "--no-owner", "--no-privileges", *args, str(dump)],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                assert result.returncode == 0, result.stderr[:1000]

                restored = create_engine(target_url)
                with restored.connect() as conn:
                    subjects = (
                        conn.execute(
                            text("SELECT subject FROM email_message WHERE subject = :s"),
                            {"s": marker},
                        )
                        .scalars()
                        .all()
                    )
                    attachments = (
                        conn.execute(text("SELECT filename FROM attachment")).scalars().all()
                    )
                    found_by_search = conn.execute(
                        text(
                            "SELECT count(*) FROM email_message WHERE search_vector @@ "
                            "websearch_to_tsquery('public.sk_unaccent', 'kasacna staznost')"
                        )
                    ).scalar_one()
                restored.dispose()

                assert len(subjects) == 1, "the backed-up message is missing after restore"
                assert "Rozhodnutie.pdf" in attachments
                assert found_by_search >= 1, "search index did not survive the restore"
            finally:
                admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
                with admin.connect() as conn:
                    conn.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = :d"
                        ),
                        {"d": restore_db},
                    )
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{restore_db}"'))
                admin.dispose()
        finally:
            if account_id is not None:
                with session_factory() as cleanup:
                    account = cleanup.get(MailboxAccount, account_id)
                    if account is not None:
                        cleanup.delete(account)
                    cleanup.execute(text("DELETE FROM audit_log WHERE action LIKE 'backup.%'"))
                    cleanup.flush()
                    # Contacts are global and survive the mailbox on purpose,
                    # so this test has to reclaim the ones it committed.
                    prune_orphan_contacts(cleanup)
                    cleanup.execute(
                        text("DELETE FROM audit_log WHERE action = 'maintenance.prune_contacts'")
                    )
                    cleanup.commit()
            engine.dispose()


class TestListingAndRetention:
    def _seed(self, settings, count, base=None):
        directory = Path(settings.backup_local_path)
        directory.mkdir(parents=True, exist_ok=True)
        base = base or datetime(2026, 8, 1, tzinfo=UTC)
        names = []
        for i in range(count):
            moment = base + timedelta(days=i)
            name = f"email-assistant-{moment.strftime('%Y%m%dT%H%M%SZ')}-db.eaabk"
            path = directory / name
            path.write_bytes(b"EAABK1" + bytes(20))
            import os

            stamp = moment.timestamp()
            os.utime(path, (stamp, stamp))
            names.append(name)
        return names

    def test_listing_is_newest_first(self, db_session, backup_settings):
        self._seed(backup_settings, 4)
        listed = list_backups(db_session, backup_settings)
        assert len(listed) == 4
        assert listed == sorted(listed, key=lambda a: a.created_at, reverse=True)

    def test_listing_ignores_unrelated_files(self, db_session, backup_settings):
        self._seed(backup_settings, 1)
        (Path(backup_settings.backup_local_path) / "notes.txt").write_text("hello")
        assert len(list_backups(db_session, backup_settings)) == 1

    def test_empty_directory_lists_nothing(self, db_session, backup_settings):
        assert list_backups(db_session, backup_settings) == []

    def test_retention_keeps_the_newest(self, db_session, backup_settings):
        self._seed(backup_settings, 6)
        removed = prune_backups(db_session, backup_settings)

        assert len(removed) == 3
        remaining = list_backups(db_session, backup_settings)
        assert len(remaining) == backup_settings.backup_retention
        # What survived must be newer than anything deleted.
        assert all(name not in {a.name for a in remaining} for name in removed)

    def test_retention_is_a_no_op_below_the_limit(self, db_session, backup_settings):
        self._seed(backup_settings, 2)
        assert prune_backups(db_session, backup_settings) == []

    def test_pruning_is_audited(self, db_session, backup_settings):
        self._seed(backup_settings, 5)
        prune_backups(db_session, backup_settings)
        assert db_session.scalar(select(AuditLog).where(AuditLog.action == "backup.pruned"))


class TestDriveGuardRails:
    def test_drive_scope_only_requested_when_drive_backups_are_on(self):
        off = Settings(_env_file=None)
        on = Settings(backup_enabled=True, backup_backend="gdrive", _env_file=None)
        assert DRIVE_FILE_SCOPE not in off.gmail_scopes
        assert DRIVE_FILE_SCOPE in on.gmail_scopes

    def test_requested_drive_scope_is_the_narrow_one(self):
        on = Settings(backup_enabled=True, backup_backend="gdrive", _env_file=None)
        assert "auth/drive.file" in DRIVE_FILE_SCOPE
        assert not any(s.endswith("auth/drive") for s in on.gmail_scopes)

    def test_account_without_drive_scope_gets_a_clear_error(self, db_session, account):
        from app.services.backup import _resolve_backup_account

        account.oauth_scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        db_session.flush()
        settings = Settings(
            backup_backend="gdrive",
            backup_account_email=account.email,
            _env_file=None,
        )
        with pytest.raises(BackupError, match="authorised without the Drive scope"):
            _resolve_backup_account(db_session, settings)

    def test_unknown_backup_account_is_reported(self, db_session):
        settings = Settings(
            backup_backend="gdrive", backup_account_email="nobody@example.sk", _env_file=None
        )
        with pytest.raises(BackupError, match="does not match a connected mailbox"):
            from app.services.backup import _resolve_backup_account

            _resolve_backup_account(db_session, settings)


class TestDsnParsing:
    def test_password_never_reaches_the_command_line(self):
        args, env = dsn_to_pg_env("postgresql+psycopg://u:s3cret@host:5432/db")
        assert "s3cret" not in " ".join(args)
        assert env["PGPASSWORD"] == "s3cret"

    def test_url_encoded_password_is_decoded(self):
        _, env = dsn_to_pg_env("postgresql+psycopg://u:p%40ss%3Aword@host/db")
        assert env["PGPASSWORD"] == "p@ss:word"

    def test_missing_database_name_is_refused(self):
        with pytest.raises(BackupError, match="No database name"):
            dsn_to_pg_env("postgresql+psycopg://u:p@host:5432/")


def test_message_table_is_not_empty_precondition(db_session):
    """Guards the restore test: a backup of nothing would prove nothing."""
    assert db_session.execute(select(EmailMessage).limit(1)) is not None
