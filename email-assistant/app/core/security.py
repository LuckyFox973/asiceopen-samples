"""API key generation and verification.

The assistant is a single-person system, so the right authentication is a
small number of long, revocable keys — one per consumer (the MCP server, a
future UI, a script) — rather than a user directory.

Keys are stored only as SHA-256 hashes.  A database dump therefore yields no
usable credential, and a lost key can only be replaced, never recovered.
"""

from __future__ import annotations

import hashlib
import secrets

KEY_PREFIX = "eaa_"
# Long enough that guessing is hopeless, short enough to paste in one line.
KEY_ENTROPY_BYTES = 32
# Stored in clear so a key can be identified and revoked without knowing it.
DISPLAY_PREFIX_LENGTH = 12


def generate_api_key() -> str:
    return f"{KEY_PREFIX}{secrets.token_urlsafe(KEY_ENTROPY_BYTES)}"


def hash_api_key(key: str) -> str:
    """SHA-256 is right here: the input is already high-entropy random.

    A slow password hash would only add latency to every request without
    making a 256-bit random token any harder to guess.
    """
    return hashlib.sha256(key.strip().encode()).hexdigest()


def display_prefix(key: str) -> str:
    return key.strip()[:DISPLAY_PREFIX_LENGTH]


def keys_match(candidate_hash: str, stored_hash: str) -> bool:
    return secrets.compare_digest(candidate_hash, stored_hash)


def extract_bearer(header_value: str | None) -> str | None:
    """Pull the token out of an ``Authorization: Bearer <token>`` header."""
    if not header_value:
        return None
    parts = header_value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def generate_oauth_state() -> str:
    return secrets.token_urlsafe(32)
