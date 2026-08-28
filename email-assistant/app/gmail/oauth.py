"""Google OAuth 2.0 authorisation-code flow.

The user consents in a browser; only the resulting refresh token is stored,
encrypted.  No password ever reaches this application.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.core.config import Settings, get_settings
from app.core.crypto import get_cipher
from app.core.logging import get_logger

log = get_logger(__name__)

TOKEN_URI = "https://oauth2.googleapis.com/token"
AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class OAuthNotConfiguredError(RuntimeError):
    pass


@dataclass(slots=True)
class AuthorisedIdentity:
    """Result of a completed consent flow."""

    email: str
    google_sub: str | None
    display_name: str | None
    refresh_token: str | None
    access_token: str | None
    token_expiry: datetime | None
    scopes: list[str]


def _client_config(settings: Settings) -> dict:
    if not settings.oauth_configured():
        raise OAuthNotConfiguredError(
            "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set. "
            "See docs/SETUP.md, section 'Google Cloud project'."
        )
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [settings.google_oauth_redirect_uri],
        }
    }


def build_flow(settings: Settings | None = None, state: str | None = None) -> Flow:
    settings = settings or get_settings()
    flow = Flow.from_client_config(
        _client_config(settings),
        scopes=settings.gmail_scopes,
        state=state,
    )
    flow.redirect_uri = settings.google_oauth_redirect_uri
    return flow


def build_authorisation_url(
    settings: Settings | None = None, state: str | None = None
) -> tuple[str, str, str | None]:
    """Return ``(url, state, code_verifier)``.

    The verifier matters. The library uses PKCE, so the URL carries only a hash
    of a secret it generated on the spot. The callback runs in a different
    process and builds a different ``Flow``, which knows nothing of that
    secret — so it has to travel with the state, or Google rejects the
    exchange with "Missing code verifier".
    """
    settings = settings or get_settings()
    state = state or secrets.token_urlsafe(32)
    flow = build_flow(settings, state=state)
    url, _ = flow.authorization_url(
        access_type="offline",  # required to receive a refresh token
        include_granted_scopes="true",
        prompt="consent",  # force a refresh token even on re-authorisation
    )
    return url, state, getattr(flow, "code_verifier", None)


def exchange_code(
    code: str,
    state: str | None = None,
    settings: Settings | None = None,
    code_verifier: str | None = None,
) -> Credentials:
    """Trade the authorisation code for tokens.

    *code_verifier* is the PKCE secret belonging to the flow that produced the
    URL. It must be that exact one.
    """
    settings = settings or get_settings()
    flow = build_flow(settings, state=state)
    if code_verifier:
        flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    return flow.credentials  # type: ignore[return-value]


def fetch_identity(credentials: Credentials) -> tuple[str, str | None, str | None]:
    """Return ``(email, sub, name)`` for the consenting Google account."""
    import httpx

    response = httpx.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {credentials.token}"},
        timeout=15.0,
    )
    response.raise_for_status()
    data = response.json()
    return data.get("email", ""), data.get("sub"), data.get("name")


def to_identity(credentials: Credentials) -> AuthorisedIdentity:
    email, sub, name = fetch_identity(credentials)
    expiry = credentials.expiry
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return AuthorisedIdentity(
        email=email,
        google_sub=sub,
        display_name=name,
        refresh_token=credentials.refresh_token,
        access_token=credentials.token,
        token_expiry=expiry,
        scopes=list(credentials.scopes or []),
    )


def credentials_from_stored(
    refresh_token_enc: str | None,
    access_token_enc: str | None = None,
    scopes: list[str] | None = None,
    settings: Settings | None = None,
) -> Credentials:
    """Rebuild usable credentials from the encrypted values in the database."""
    settings = settings or get_settings()
    if not refresh_token_enc:
        raise OAuthNotConfiguredError(
            "This mailbox has no stored refresh token; re-run the authorisation flow."
        )
    cipher = get_cipher()
    return Credentials(
        token=cipher.decrypt(access_token_enc) if access_token_enc else None,
        refresh_token=cipher.decrypt(refresh_token_enc),
        token_uri=TOKEN_URI,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=scopes or settings.gmail_scopes,
    )


def refresh_if_needed(credentials: Credentials) -> bool:
    """Refresh an expired access token. Returns True if a refresh happened."""
    if credentials.valid:
        return False
    if not credentials.refresh_token:
        raise OAuthNotConfiguredError("Credentials expired and no refresh token is available.")
    credentials.refresh(Request())
    return True
