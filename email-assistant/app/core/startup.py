"""Configuration checks run at start-up.

Warnings in development; in production these are the difference between a
safe deployment and a leaky one, so they are reported loudly.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.crypto import is_fernet_key


def verify_configuration(settings: Settings) -> list[str]:
    """Return a list of configuration problems, most serious first."""
    problems: list[str] = []

    if not settings.token_encryption_key:
        problems.append("TOKEN_ENCRYPTION_KEY is not set — OAuth tokens cannot be stored.")
    elif settings.is_production and not is_fernet_key(settings.token_encryption_key):
        problems.append(
            "TOKEN_ENCRYPTION_KEY is a derived passphrase, not a generated Fernet "
            "key. Production must use 'python -m app.core.crypto keygen'."
        )

    if not settings.oauth_configured():
        problems.append("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not configured.")

    if settings.is_production:
        if not settings.job_auth_token:
            problems.append("JOB_AUTH_TOKEN is not set — scheduled job endpoints are open.")
        if settings.attachment_backend == "local":
            problems.append(
                "ATTACHMENT_BACKEND=local in production — Cloud Run storage is "
                "ephemeral; attachments would be lost on restart."
            )
        if settings.google_oauth_redirect_uri.startswith("http://"):
            problems.append("GOOGLE_OAUTH_REDIRECT_URI is not HTTPS.")
        if "localhost" in settings.database_url or "127.0.0.1" in settings.database_url:
            problems.append("DATABASE_URL points at localhost in production.")

    return problems
