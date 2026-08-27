"""Google OAuth endpoints.

The user consents in their own browser.  This service never sees a password;
it stores only the refresh token, encrypted.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.api.deps import SessionDep, SettingsDep, require_api_key
from app.core.config import Settings
from app.core.logging import get_logger
from app.gmail.client import GmailClient
from app.gmail.oauth import (
    OAuthNotConfiguredError,
    build_authorisation_url,
    exchange_code,
    to_identity,
)
from app.schemas.common import AuthStartOut
from app.services.access import consume_oauth_state, issue_oauth_state
from app.services.accounts import refresh_send_as_addresses, upsert_account_from_identity

router = APIRouter(prefix="/auth/google", tags=["auth"])
log = get_logger(__name__)


@router.post(
    "/start", response_model=AuthStartOut, dependencies=[Depends(require_api_key)]
)
def start_authorisation(
    session: Session = SessionDep, settings: Settings = SettingsDep
) -> AuthStartOut:
    """Return the Google consent URL to open in a browser."""
    try:
        state = issue_oauth_state(session)
        url, _ = build_authorisation_url(settings, state=state)
    except OAuthNotConfiguredError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return AuthStartOut(
        authorisation_url=url,
        state=state,
        instructions=(
            "Open this URL in a browser, sign in with the Google Workspace "
            "account whose mailbox should be synchronised, and approve the "
            "requested read-only Gmail scopes."
        ),
    )


@router.get("/callback", response_class=HTMLResponse)
def oauth_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    session: Session = SessionDep,
    settings: Settings = SettingsDep,
) -> HTMLResponse:
    """Google redirects here after consent."""
    if error:
        return HTMLResponse(_page("Authorisation cancelled", error), status_code=400)
    if not code:
        return HTMLResponse(_page("Missing code", "No authorisation code."), status_code=400)

    # The callback carries no API key — Google redirects the browser here — so
    # the one-time state token is what proves this flow is the one we started.
    if not consume_oauth_state(session, state):
        log.warning("oauth.state_rejected", state_present=bool(state))
        return HTMLResponse(
            _page(
                "Authorisation rejected",
                "This callback did not match an authorisation flow started by "
                "this service, or it has already been used. Start again.",
            ),
            status_code=400,
        )

    try:
        credentials = exchange_code(code, state=state, settings=settings)
        identity = to_identity(credentials)
    except Exception as exc:  # noqa: BLE001
        log.error("oauth.exchange_failed", error=str(exc))
        return HTMLResponse(_page("Authorisation failed", str(exc)), status_code=400)

    account = upsert_account_from_identity(session, identity)

    aliases = 0
    try:
        aliases = refresh_send_as_addresses(session, account, GmailClient(credentials))
    except Exception as exc:  # noqa: BLE001 - aliases can be added later
        log.warning("oauth.send_as_failed", error=str(exc))

    session.flush()
    body = (
        f"Mailbox <b>{account.email}</b> is connected.<br>"
        f"Account id: <code>{account.id}</code><br>"
        f"Addresses recognised as yours: {len(account.addresses)}"
        f" ({aliases} imported from send-as aliases)."
        "<br><br>You can close this window and start a sync."
    )
    return HTMLResponse(_page("Mailbox connected", body))


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>
 body{{font-family:system-ui,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;
      line-height:1.6;color:#111}}
 code{{background:#f4f4f5;padding:.15rem .35rem;border-radius:.25rem}}
 h1{{font-size:1.35rem}}
</style></head>
<body><h1>{title}</h1><p>{body}</p></body></html>"""
