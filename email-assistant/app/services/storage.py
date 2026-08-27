"""Content-addressed blob storage for attachments.

Files are keyed by the SHA-256 of their bytes, so the same document sent
twenty times occupies one object.  That is both a cost decision and a GDPR
one: there is exactly one place to look for, export, or erase a given file.

The original bytes are written once and never modified.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def storage_key(digest: str, filename: str | None = None) -> str:
    """Sharded key: ``ab/cd/<digest>`` keeps directories small.

    The filename is intentionally *not* part of the key — the same bytes under
    two names are still one object; the names live on the attachment rows.
    """
    return f"{digest[:2]}/{digest[2:4]}/{digest}"


class AttachmentStorage(ABC):
    """Write-once blob store."""

    backend: str

    @abstractmethod
    def put(self, data: bytes, digest: str, content_type: str | None = None) -> str:
        """Store *data* and return its storage key. Idempotent."""

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove a blob. Used only by GDPR erasure, never by ingest."""


class LocalAttachmentStorage(AttachmentStorage):
    """Filesystem backend — development and single-node deployments."""

    backend = "local"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError(f"storage key escapes root: {key!r}")
        return candidate

    def put(self, data: bytes, digest: str, content_type: str | None = None) -> str:
        key = storage_key(digest)
        path = self._path(key)
        if path.exists():
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file then rename, so a crash never leaves a partial
        # blob under a hash that claims to describe complete content.
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        path.chmod(0o440)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.exists():
            return False
        path.chmod(0o640)
        path.unlink()
        return True

    def clear(self) -> None:  # pragma: no cover - test helper
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)


class GCSAttachmentStorage(AttachmentStorage):
    """Google Cloud Storage backend — production.

    Imported lazily so the dependency is only needed where it is used.
    """

    backend = "gcs"

    def __init__(self, bucket_name: str, prefix: str = "attachments") -> None:
        from google.cloud import storage  # type: ignore[import-not-found]

        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)
        self._prefix = prefix.strip("/")

    def _blob(self, key: str):  # type: ignore[no-untyped-def]
        return self._bucket.blob(f"{self._prefix}/{key}")

    def put(self, data: bytes, digest: str, content_type: str | None = None) -> str:
        key = storage_key(digest)
        blob = self._blob(key)
        if not blob.exists():
            blob.upload_from_string(data, content_type=content_type or "application/octet-stream")
        return key

    def get(self, key: str) -> bytes:
        return self._blob(key).download_as_bytes()

    def exists(self, key: str) -> bool:
        return bool(self._blob(key).exists())

    def delete(self, key: str) -> bool:
        blob = self._blob(key)
        if not blob.exists():
            return False
        blob.delete()
        return True


def build_storage(settings: Settings | None = None) -> AttachmentStorage:
    settings = settings or get_settings()
    if settings.attachment_backend == "gcs":
        if not settings.attachment_gcs_bucket:
            raise ValueError("ATTACHMENT_BACKEND=gcs requires ATTACHMENT_GCS_BUCKET")
        return GCSAttachmentStorage(settings.attachment_gcs_bucket)
    return LocalAttachmentStorage(settings.attachment_local_path)
