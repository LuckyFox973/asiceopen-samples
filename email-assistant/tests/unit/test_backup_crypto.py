import os

import pytest

from app.services.backup_crypto import (
    CHUNK_BYTES,
    BackupDecryptionError,
    decrypt_stream,
    encrypt_stream,
)

KEY = "a-long-local-backup-key-kept-off-google-drive"


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return path


class TestRoundTrip:
    @pytest.mark.parametrize(
        "size",
        [0, 1, 1024, CHUNK_BYTES - 1, CHUNK_BYTES, CHUNK_BYTES + 1, CHUNK_BYTES * 2 + 7],
    )
    def test_any_size_round_trips(self, tmp_path, size):
        data = os.urandom(size)
        source = write(tmp_path, "plain", data)
        archive = tmp_path / "archive"
        restored = tmp_path / "restored"

        encrypt_stream(source, archive, KEY)
        decrypt_stream(archive, restored, KEY)

        assert restored.read_bytes() == data

    def test_ciphertext_does_not_contain_plaintext(self, tmp_path):
        secret = "Klient ABC, daňová kontrola DPH, dôverné".encode() * 200
        archive = tmp_path / "archive"
        encrypt_stream(write(tmp_path, "plain", secret), archive, KEY)
        assert b"Klient ABC" not in archive.read_bytes()

    def test_two_archives_of_the_same_input_differ(self, tmp_path):
        source = write(tmp_path, "plain", b"same input")
        first, second = tmp_path / "a", tmp_path / "b"
        encrypt_stream(source, first, KEY)
        encrypt_stream(source, second, KEY)
        assert first.read_bytes() != second.read_bytes()

    def test_archive_is_owner_readable_only(self, tmp_path):
        archive = tmp_path / "archive"
        encrypt_stream(write(tmp_path, "plain", b"x"), archive, KEY)
        assert archive.stat().st_mode & 0o077 == 0


class TestTampering:
    def _archive(self, tmp_path, data=b"sensitive backup contents" * 1000):
        archive = tmp_path / "archive"
        encrypt_stream(write(tmp_path, "plain", data), archive, KEY)
        return archive

    def test_wrong_key_is_refused(self, tmp_path):
        archive = self._archive(tmp_path)
        with pytest.raises(BackupDecryptionError):
            decrypt_stream(archive, tmp_path / "out", "the-wrong-key")

    def test_flipped_byte_is_detected(self, tmp_path):
        archive = self._archive(tmp_path)
        raw = bytearray(archive.read_bytes())
        raw[-5] ^= 0x01
        archive.write_bytes(bytes(raw))
        with pytest.raises(BackupDecryptionError):
            decrypt_stream(archive, tmp_path / "out", KEY)

    def test_truncation_is_detected(self, tmp_path):
        # Two chunks, so cutting the second leaves a structurally valid file.
        archive = self._archive(tmp_path, os.urandom(CHUNK_BYTES * 2))
        raw = archive.read_bytes()
        archive.write_bytes(raw[: len(raw) // 2])
        with pytest.raises(BackupDecryptionError):
            decrypt_stream(archive, tmp_path / "out", KEY)

    def test_foreign_file_is_rejected(self, tmp_path):
        foreign = write(tmp_path, "foreign", b"just some bytes, not an archive")
        with pytest.raises(BackupDecryptionError, match="Not an Email Assistant"):
            decrypt_stream(foreign, tmp_path / "out", KEY)

    def test_empty_file_is_rejected(self, tmp_path):
        with pytest.raises(BackupDecryptionError, match="too short"):
            decrypt_stream(write(tmp_path, "empty", b""), tmp_path / "out", KEY)

    def test_trailing_data_is_rejected(self, tmp_path):
        archive = self._archive(tmp_path)
        archive.write_bytes(archive.read_bytes() + b"extra")
        with pytest.raises(BackupDecryptionError):
            decrypt_stream(archive, tmp_path / "out", KEY)


def test_encryption_requires_a_key(tmp_path):
    with pytest.raises(ValueError, match="key is required"):
        encrypt_stream(write(tmp_path, "plain", b"x"), tmp_path / "out", "")
