"""Envelope for secrets stored in the database.

Only OAuth refresh/access tokens are encrypted here.  The key itself lives
outside the database: ``.env`` in development, Google Secret Manager in
production.  Rotating the key means re-running the OAuth consent flow.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class EncryptionNotConfiguredError(RuntimeError):
    """Raised when TOKEN_ENCRYPTION_KEY is missing but encryption is needed."""


class TokenCipher:
    """Thin wrapper around Fernet (AES-128-CBC + HMAC-SHA256)."""

    def __init__(self, key: str) -> None:
        if not key:
            raise EncryptionNotConfiguredError(
                "TOKEN_ENCRYPTION_KEY is not set. Generate one with: "
                "python -m app.core.crypto keygen"
            )
        self._fernet = Fernet(self._normalise(key))

    @staticmethod
    def _normalise(key: str) -> bytes:
        """Accept a real Fernet key, or derive one from an arbitrary string.

        Deriving keeps local development friction low; production must use a
        generated Fernet key (checked by :func:`app.core.startup.verify`).
        """
        raw = key.strip().encode()
        try:
            if len(base64.urlsafe_b64decode(raw)) == 32:
                return raw
        except Exception:  # noqa: BLE001 - not a valid Fernet key, derive one
            pass
        return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:  # pragma: no cover - defensive
            raise ValueError(
                "Stored token could not be decrypted; TOKEN_ENCRYPTION_KEY "
                "likely changed. Re-authorise the mailbox."
            ) from exc


def is_fernet_key(key: str) -> bool:
    """True if *key* is a properly generated 32-byte Fernet key."""
    try:
        return len(base64.urlsafe_b64decode(key.strip().encode())) == 32
    except Exception:  # noqa: BLE001
        return False


def get_cipher() -> TokenCipher:
    return TokenCipher(get_settings().token_encryption_key)


def generate_key() -> str:
    return Fernet.generate_key().decode()


if __name__ == "__main__":  # pragma: no cover
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "keygen":
        print(generate_key())
    else:
        print("usage: python -m app.core.crypto keygen")
