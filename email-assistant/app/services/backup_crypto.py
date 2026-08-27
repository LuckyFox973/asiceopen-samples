"""Streaming authenticated encryption for backup archives.

A database dump of this system contains every client e-mail plus the
encrypted OAuth tokens.  It must never leave the machine in the clear, so
archives are encrypted before upload and the key stays local.

The format is chunked AES-256-GCM.  Each chunk carries its index and a
final-chunk flag as associated data, so a truncated or reordered archive
fails to decrypt rather than silently yielding partial data.
"""

from __future__ import annotations

import os
import struct
from collections.abc import Iterator
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"EAABK1"
SALT_BYTES = 16
KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024  # 1 MiB of plaintext per chunk
HEADER = struct.Struct(">6sH")  # magic, salt length


class BackupDecryptionError(Exception):
    """The archive is corrupt, truncated, or the key is wrong."""


def _derive_key(master_key: str, salt: bytes) -> bytes:
    """Per-archive key, so a counter nonce can never repeat under one key."""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=salt,
        info=b"email-assistant backup archive v1",
    ).derive(master_key.encode())


def _nonce(index: int) -> bytes:
    return index.to_bytes(NONCE_BYTES, "big")


def _aad(index: int, final: bool) -> bytes:
    return struct.pack(">Q?", index, final)


def encrypt_stream(source: Path, destination: Path, master_key: str) -> int:
    """Encrypt *source* into *destination*. Returns the ciphertext size."""
    if not master_key:
        raise ValueError("A backup encryption key is required.")

    salt = os.urandom(SALT_BYTES)
    aesgcm = AESGCM(_derive_key(master_key, salt))

    with source.open("rb") as fin, destination.open("wb") as fout:
        fout.write(HEADER.pack(MAGIC, SALT_BYTES))
        fout.write(salt)

        index = 0
        pending = fin.read(CHUNK_BYTES)
        while True:
            lookahead = fin.read(CHUNK_BYTES)
            final = not lookahead
            blob = aesgcm.encrypt(_nonce(index), pending, _aad(index, final))
            fout.write(struct.pack(">I", len(blob)))
            fout.write(blob)
            if final:
                break
            pending = lookahead
            index += 1

    destination.chmod(0o600)
    return destination.stat().st_size


def decrypt_stream(source: Path, destination: Path, master_key: str) -> int:
    """Decrypt *source* into *destination*. Returns the plaintext size."""
    with source.open("rb") as fin, destination.open("wb") as fout:
        header = fin.read(HEADER.size)
        if len(header) != HEADER.size:
            raise BackupDecryptionError("Archive is too short to be valid.")
        magic, salt_length = HEADER.unpack(header)
        if magic != MAGIC:
            raise BackupDecryptionError("Not an Email Assistant backup archive.")

        salt = fin.read(salt_length)
        if len(salt) != salt_length:
            raise BackupDecryptionError("Archive header is truncated.")
        aesgcm = AESGCM(_derive_key(master_key, salt))

        index = 0
        saw_final = False
        for blob in _iter_chunks(fin):
            # Try the chunk as non-final first; the last one is tagged final,
            # which is what makes truncation detectable.
            for final in (False, True):
                try:
                    fout.write(aesgcm.decrypt(_nonce(index), blob, _aad(index, final)))
                except Exception:  # noqa: BLE001 - wrong flag, or genuinely bad
                    continue
                saw_final = final
                break
            else:
                raise BackupDecryptionError(
                    f"Chunk {index} failed authentication — wrong key or corrupt archive."
                )
            if saw_final:
                index += 1
                break
            index += 1

        if not saw_final:
            raise BackupDecryptionError("Archive is truncated: no final chunk.")
        if fin.read(1):
            raise BackupDecryptionError("Trailing data after the final chunk.")

    destination.chmod(0o600)
    return destination.stat().st_size


def _iter_chunks(stream) -> Iterator[bytes]:  # type: ignore[no-untyped-def]
    while True:
        header = stream.read(4)
        if not header:
            return
        if len(header) != 4:
            raise BackupDecryptionError("Archive is truncated mid-chunk header.")
        (length,) = struct.unpack(">I", header)
        blob = stream.read(length)
        if len(blob) != length:
            raise BackupDecryptionError("Archive is truncated mid-chunk.")
        yield blob
