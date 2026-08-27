import pytest

from app.core.config import Settings
from app.services.storage import (
    LocalAttachmentStorage,
    build_storage,
    sha256_hex,
    storage_key,
)


class TestLocalStorage:
    def test_put_and_get(self, tmp_path):
        store = LocalAttachmentStorage(tmp_path)
        data = b"PDF bytes"
        key = store.put(data, sha256_hex(data))
        assert store.get(key) == data
        assert store.exists(key)

    def test_put_is_idempotent_and_content_addressed(self, tmp_path):
        store = LocalAttachmentStorage(tmp_path)
        data = b"same bytes"
        assert store.put(data, sha256_hex(data)) == store.put(data, sha256_hex(data))
        assert len(list(tmp_path.rglob("*"))) == 3  # two shard dirs + one file

    def test_different_content_gets_different_keys(self, tmp_path):
        store = LocalAttachmentStorage(tmp_path)
        assert store.put(b"a", sha256_hex(b"a")) != store.put(b"b", sha256_hex(b"b"))

    def test_stored_file_is_read_only(self, tmp_path):
        store = LocalAttachmentStorage(tmp_path)
        key = store.put(b"immutable", sha256_hex(b"immutable"))
        path = tmp_path / key
        assert not path.stat().st_mode & 0o200

    def test_delete_removes_and_reports(self, tmp_path):
        store = LocalAttachmentStorage(tmp_path)
        key = store.put(b"x", sha256_hex(b"x"))
        assert store.delete(key) is True
        assert store.delete(key) is False
        assert not store.exists(key)

    def test_key_traversal_is_refused(self, tmp_path):
        store = LocalAttachmentStorage(tmp_path)
        with pytest.raises(ValueError):
            store.get("../../etc/passwd")

    def test_key_is_sharded(self):
        digest = "abcdef" + "0" * 58
        assert storage_key(digest) == f"ab/cd/{digest}"


class TestBuildStorage:
    def test_local_by_default(self, tmp_path):
        settings = Settings(
            attachment_backend="local", attachment_local_path=str(tmp_path), _env_file=None
        )
        assert build_storage(settings).backend == "local"

    def test_gcs_without_bucket_is_refused(self):
        settings = Settings(attachment_backend="gcs", attachment_gcs_bucket="", _env_file=None)
        with pytest.raises(ValueError, match="ATTACHMENT_GCS_BUCKET"):
            build_storage(settings)
