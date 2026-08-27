"""Encrypted database backups, kept locally or on Google Drive.

What is backed up, and why only this:

* **The database** — every run.  It holds the assistant's whole structured
  memory: threads, participants, contacts, audit history.  Nothing else can
  reconstruct it.
* **Attachments** — off by default.  Their bytes are content-addressed copies
  of files that still exist in Gmail, so they are re-fetchable from the source
  as long as the messages are known.  Backing up gigabytes nightly to protect
  data that is already safe elsewhere is a poor trade; ``--include-attachments``
  is there for when you want it anyway.

Every archive is encrypted before it leaves the machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session

from app.core.config import DRIVE_FILE_SCOPE, Settings, get_settings
from app.core.logging import get_logger
from app.db.models import AuditLog, MailboxAccount
from app.services.backup_crypto import decrypt_stream, encrypt_stream
from app.services.drive import DriveClient

log = get_logger(__name__)

ARCHIVE_SUFFIX = ".eaabk"
ARCHIVE_PREFIX = "email-assistant-"


class BackupError(RuntimeError):
    """A backup could not be produced or stored."""


@dataclass(slots=True)
class BackupArtifact:
    name: str
    size_bytes: int
    created_at: datetime
    location: str
    remote_id: str | None = None
    included_attachments: bool = False


def archive_name(moment: datetime, include_attachments: bool) -> str:
    kind = "full" if include_attachments else "db"
    return f"{ARCHIVE_PREFIX}{moment.strftime('%Y%m%dT%H%M%SZ')}-{kind}{ARCHIVE_SUFFIX}"


# ---------------------------------------------------------------------------
# Producing the archive
# ---------------------------------------------------------------------------


def dsn_to_pg_env(database_url: str) -> tuple[list[str], dict[str, str]]:
    """Turn a SQLAlchemy URL into pg_dump arguments plus PGPASSWORD.

    The password goes through the environment, never the command line, where
    it would be visible to every process on the machine.
    """
    parsed = urlparse(database_url.replace("postgresql+psycopg://", "postgresql://"))
    args: list[str] = []
    if parsed.hostname:
        args += ["--host", parsed.hostname]
    if parsed.port:
        args += ["--port", str(parsed.port)]
    if parsed.username:
        args += ["--username", unquote(parsed.username)]
    database = (parsed.path or "").lstrip("/")
    if not database:
        raise BackupError(f"No database name in DATABASE_URL: {database_url!r}")
    args += ["--dbname", database]

    env = dict(os.environ)
    if parsed.password:
        env["PGPASSWORD"] = unquote(parsed.password)
    return args, env


def dump_database(destination: Path, settings: Settings) -> Path:
    """pg_dump in custom format — compressed, and restorable selectively."""
    if shutil.which("pg_dump") is None:
        raise BackupError(
            "pg_dump is not on PATH. Install the PostgreSQL client tools "
            "(macOS: brew install libpq && brew link --force libpq)."
        )
    args, env = dsn_to_pg_env(settings.database_url)
    command = ["pg_dump", "--format=custom", "--compress=9", "--file", str(destination), *args]

    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise BackupError(f"pg_dump failed: {result.stderr.strip()[:500]}")
    if not destination.exists() or destination.stat().st_size == 0:
        raise BackupError("pg_dump produced no output.")
    return destination


def build_archive_payload(workdir: Path, settings: Settings, include_attachments: bool) -> Path:
    """Assemble the plaintext payload that is about to be encrypted."""
    dump_path = workdir / "database.dump"
    dump_database(dump_path, settings)

    if not include_attachments:
        return dump_path

    bundle = workdir / "payload.tar"
    with tarfile.open(bundle, "w") as tar:
        tar.add(dump_path, arcname="database.dump")
        blobs = Path(settings.attachment_local_path)
        if blobs.is_dir():
            tar.add(blobs, arcname="attachments")
    dump_path.unlink(missing_ok=True)
    return bundle


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------


def _resolve_backup_account(session: Session, settings: Settings) -> MailboxAccount:
    from app.services.accounts import get_account_by_email, list_accounts

    if settings.backup_account_email:
        account = get_account_by_email(session, settings.backup_account_email)
        if account is None:
            raise BackupError(
                f"BACKUP_ACCOUNT_EMAIL={settings.backup_account_email!r} "
                "does not match a connected mailbox."
            )
    else:
        accounts = list_accounts(session, active_only=True)
        if not accounts:
            raise BackupError("No connected mailbox to authenticate Drive uploads with.")
        account = accounts[0]

    if DRIVE_FILE_SCOPE not in (account.oauth_scopes or []):
        raise BackupError(
            f"Mailbox {account.email} was authorised without the Drive scope. "
            "Set BACKUP_BACKEND=gdrive, then re-run 'python -m app.cli auth-url' "
            "and approve again — the Drive permission is only requested when "
            "Drive backups are enabled."
        )
    return account


def build_drive_client(session: Session, settings: Settings) -> DriveClient:
    from app.services.runner import build_credentials

    account = _resolve_backup_account(session, settings)
    return DriveClient(build_credentials(session, account, settings))


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------


def create_backup(
    session: Session,
    settings: Settings | None = None,
    include_attachments: bool | None = None,
    drive_client: DriveClient | None = None,
) -> BackupArtifact:
    settings = settings or get_settings()
    include_attachments = (
        settings.backup_include_attachments if include_attachments is None else include_attachments
    )
    if not settings.backup_encryption_key:
        raise BackupError(
            "BACKUP_ENCRYPTION_KEY is not set. A backup of this database "
            "contains every stored message; it is never written unencrypted."
        )

    started = datetime.now(UTC)
    name = archive_name(started, include_attachments)

    with tempfile.TemporaryDirectory(prefix="eaa-backup-") as tmp:
        workdir = Path(tmp)
        payload = build_archive_payload(workdir, settings, include_attachments)
        archive = workdir / name
        size = encrypt_stream(payload, archive, settings.backup_encryption_key)
        payload.unlink(missing_ok=True)

        if settings.backup_backend == "gdrive":
            client = drive_client or build_drive_client(session, settings)
            folder_id = client.ensure_folder(settings.backup_gdrive_folder)
            uploaded = client.upload(archive, folder_id)
            artifact = BackupArtifact(
                name=name,
                size_bytes=size,
                created_at=started,
                location=f"gdrive:{settings.backup_gdrive_folder}",
                remote_id=uploaded.id,
                included_attachments=include_attachments,
            )
        else:
            target_dir = Path(settings.backup_local_path).expanduser()
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(archive, target_dir / name)
            artifact = BackupArtifact(
                name=name,
                size_bytes=size,
                created_at=started,
                location=str(target_dir),
                included_attachments=include_attachments,
            )

    _audit(
        session,
        "backup.created",
        f"Backup {name} written to {artifact.location} ({size:,} bytes)",
        {
            "name": name,
            "size_bytes": size,
            "backend": settings.backup_backend,
            "attachments": include_attachments,
        },
    )
    log.info("backup.created", name=name, size=size, backend=settings.backup_backend)
    return artifact


def list_backups(
    session: Session, settings: Settings | None = None, drive_client: DriveClient | None = None
) -> list[BackupArtifact]:
    settings = settings or get_settings()

    if settings.backup_backend == "gdrive":
        client = drive_client or build_drive_client(session, settings)
        folder_id = client.ensure_folder(settings.backup_gdrive_folder)
        return [
            BackupArtifact(
                name=f.name,
                size_bytes=f.size_bytes,
                created_at=f.created_at or datetime.now(UTC),
                location=f"gdrive:{settings.backup_gdrive_folder}",
                remote_id=f.id,
                included_attachments="-full" in f.name,
            )
            for f in client.list_folder(folder_id)
            if f.is_archive
        ]

    target_dir = Path(settings.backup_local_path).expanduser()
    if not target_dir.is_dir():
        return []
    artifacts = [
        BackupArtifact(
            name=path.name,
            size_bytes=path.stat().st_size,
            created_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
            location=str(target_dir),
            included_attachments="-full" in path.name,
        )
        for path in target_dir.glob(f"*{ARCHIVE_SUFFIX}")
    ]
    return sorted(artifacts, key=lambda a: a.created_at, reverse=True)


def prune_backups(
    session: Session,
    settings: Settings | None = None,
    drive_client: DriveClient | None = None,
) -> list[str]:
    """Delete archives beyond the retention count. Returns what was removed."""
    settings = settings or get_settings()
    artifacts = list_backups(session, settings, drive_client)
    doomed = artifacts[settings.backup_retention :]
    if not doomed:
        return []

    client = None
    if settings.backup_backend == "gdrive":
        client = drive_client or build_drive_client(session, settings)

    removed: list[str] = []
    for artifact in doomed:
        try:
            if client is not None and artifact.remote_id:
                client.delete(artifact.remote_id)
            elif client is None:
                (Path(artifact.location) / artifact.name).unlink(missing_ok=True)
            removed.append(artifact.name)
        except Exception as exc:  # noqa: BLE001 - one failure must not stop the rest
            log.warning("backup.prune_failed", name=artifact.name, error=str(exc))

    if removed:
        _audit(
            session,
            "backup.pruned",
            f"Removed {len(removed)} archive(s) beyond retention of {settings.backup_retention}",
            {"removed": removed},
        )
    return removed


def restore_archive(archive: Path, destination: Path, settings: Settings | None = None) -> Path:
    """Decrypt an archive to *destination*.

    Deliberately stops at the decrypted dump: restoring into a live database
    is destructive, so the actual ``pg_restore`` is a command a person runs
    knowingly, with the target spelled out.
    """
    settings = settings or get_settings()
    if not settings.backup_encryption_key:
        raise BackupError("BACKUP_ENCRYPTION_KEY is required to read an archive.")
    decrypt_stream(archive, destination, settings.backup_encryption_key)
    return destination


def verify_archive(archive: Path, settings: Settings | None = None) -> int:
    """Decrypt to a temporary file to prove the archive is readable.

    A backup nobody has ever decrypted is a hope, not a backup.
    """
    settings = settings or get_settings()
    with tempfile.TemporaryDirectory(prefix="eaa-verify-") as tmp:
        target = Path(tmp) / "payload"
        restore_archive(archive, target, settings)
        return target.stat().st_size


def _audit(session: Session, action: str, summary: str, details: dict) -> None:
    session.add(
        AuditLog(
            occurred_at=datetime.now(UTC),
            actor="system",
            action=action,
            entity_type="backup",
            summary=summary,
            details=details,
            automatic=True,
        )
    )
    session.flush()
