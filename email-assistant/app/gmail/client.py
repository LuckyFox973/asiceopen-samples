"""Thin, retrying wrapper over the Gmail REST API.

Everything the sync engine needs, and nothing it does not: MVP 1 requests
read-only scopes, so this client physically cannot send, delete or modify
mail even if asked to.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any, Protocol

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.core.logging import get_logger

log = get_logger(__name__)

RETRYABLE_STATUS = {403, 429, 500, 502, 503, 504}

# Gmail bills each mailbox in quota units per second, not requests: 250 a
# second, and a message fetch costs 5 of them.  Firing as fast as the network
# allows therefore buys nothing — it earns a 403 within the first second and
# then sits out a backoff.  These are the documented per-method costs; the
# budget below stays under the ceiling, so an error either way keeps headroom.
QUOTA_UNITS = {
    "messages.list": 5,
    "messages.get": 5,
    "attachments.get": 5,
    "history.list": 2,
    "getProfile": 1,
    "sendAs.list": 1,
}
QUOTA_UNITS_PER_SECOND = 200.0


class QuotaPacer:
    """Spends a per-second quota budget, sleeping rather than overspending.

    Waiting before the call costs the same wall-clock time as being refused
    and backing off, and it keeps the log free of failures that were only ever
    the client's own impatience.
    """

    def __init__(
        self,
        units_per_second: float = QUOTA_UNITS_PER_SECOND,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.units_per_second = units_per_second
        self._clock = clock
        self._sleep = sleep
        self._allowance = units_per_second
        self._last = clock()

    def spend(self, units: int) -> None:
        now = self._clock()
        self._allowance = min(
            self.units_per_second,
            self._allowance + (now - self._last) * self.units_per_second,
        )
        self._last = now
        if self._allowance >= units:
            self._allowance -= units
            return
        self._sleep((units - self._allowance) / self.units_per_second)
        self._allowance = 0.0
        self._last = self._clock()


def _log_rate_limit(state) -> None:  # type: ignore[no-untyped-def]
    """One readable line, instead of the library's per-attempt warning."""
    exc = state.outcome.exception() if state.outcome else None
    log.warning(
        "gmail.retrying",
        after_seconds=round(state.next_action.sleep, 1),
        attempt=state.attempt_number,
        reason=type(exc).__name__ if exc else "unknown",
    )


class HistoryTooOldError(RuntimeError):
    """Gmail no longer has history back to the stored ID — a full resync is due."""


# A 403 means either "slow down" or "you were never allowed to do that", and
# only the first is worth retrying.  Gmail says which in a structured field.
QUOTA_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})


def _http_reasons(exc: HttpError) -> set[str]:
    """The machine-readable reasons Google attached to the response.

    Read from the body rather than from ``str(exc)``: the library fills its
    ``error_details`` only as a side effect of formatting a message, and skips
    that entirely when the payload carries no top-level ``message``.  Deciding
    whether to retry should not depend on how a repr happened to come out.
    """
    try:
        payload = json.loads(exc.content.decode("utf-8", "replace"))
        errors = payload["error"]["errors"]
    except (AttributeError, ValueError, KeyError, TypeError):
        return set()
    return {e["reason"] for e in errors if isinstance(e, dict) and "reason" in e}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", None)
        if status == 403:
            reasons = _http_reasons(exc)
            if reasons:
                return bool(QUOTA_REASONS & reasons)
            # Nothing structured to go on; the text is all that is left.
            return any(reason in str(exc) for reason in QUOTA_REASONS)
        return status in RETRYABLE_STATUS
    return isinstance(exc, (TimeoutError, ConnectionError))


gmail_retry = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(5),
    before_sleep=_log_rate_limit,
    reraise=True,
)


class GmailApi(Protocol):
    """The surface the sync engine depends on — easy to fake in tests."""

    def list_message_ids(
        self, query: str | None = None, page_token: str | None = None, page_size: int = 100
    ) -> tuple[list[str], str | None]: ...

    def get_message(self, message_id: str) -> dict[str, Any]: ...

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes: ...

    def get_profile(self) -> dict[str, Any]: ...

    def list_send_as(self) -> list[dict[str, Any]]: ...

    def list_history(
        self, start_history_id: int, page_token: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None, int | None]: ...


class GmailClient:
    """Concrete Gmail API client for one authorised mailbox."""

    def __init__(
        self, credentials: Any, user_id: str = "me", pacer: QuotaPacer | None = None
    ) -> None:
        self._service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        self.user_id = user_id
        self._pacer = pacer or QuotaPacer()

    def _pace(self, method: str) -> None:
        self._pacer.spend(QUOTA_UNITS[method])

    # --- profile / identity -------------------------------------------------

    @gmail_retry
    def get_profile(self) -> dict[str, Any]:
        self._pace("getProfile")
        return self._service.users().getProfile(userId=self.user_id).execute()

    @gmail_retry
    def list_send_as(self) -> list[dict[str, Any]]:
        """All addresses this mailbox may send from — the alias list."""
        self._pace("sendAs.list")
        response = self._service.users().settings().sendAs().list(userId=self.user_id).execute()
        return response.get("sendAs", [])

    # --- messages -----------------------------------------------------------

    @gmail_retry
    def list_message_ids(
        self,
        query: str | None = None,
        page_token: str | None = None,
        page_size: int = 100,
    ) -> tuple[list[str], str | None]:
        self._pace("messages.list")
        response = (
            self._service.users()
            .messages()
            .list(
                userId=self.user_id,
                q=query,
                pageToken=page_token,
                maxResults=page_size,
                includeSpamTrash=False,
            )
            .execute()
        )
        ids = [m["id"] for m in response.get("messages", [])]
        return ids, response.get("nextPageToken")

    @gmail_retry
    def get_message(self, message_id: str) -> dict[str, Any]:
        self._pace("messages.get")
        return (
            self._service.users()
            .messages()
            .get(userId=self.user_id, id=message_id, format="full")
            .execute()
        )

    @gmail_retry
    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        import base64

        self._pace("attachments.get")
        response = (
            self._service.users()
            .messages()
            .attachments()
            .get(userId=self.user_id, messageId=message_id, id=attachment_id)
            .execute()
        )
        data = response.get("data", "")
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))

    # --- history (incremental sync) ----------------------------------------

    @gmail_retry
    def list_history(
        self, start_history_id: int, page_token: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None, int | None]:
        self._pace("history.list")
        try:
            response = (
                self._service.users()
                .history()
                .list(
                    userId=self.user_id,
                    startHistoryId=str(start_history_id),
                    pageToken=page_token,
                    historyTypes=["messageAdded", "labelAdded", "labelRemoved"],
                )
                .execute()
            )
        except HttpError as exc:
            if getattr(exc.resp, "status", None) == 404:
                raise HistoryTooOldError(
                    f"historyId {start_history_id} is no longer available; full resync required"
                ) from exc
            raise
        history_id = response.get("historyId")
        return (
            response.get("history", []),
            response.get("nextPageToken"),
            int(history_id) if history_id else None,
        )

    # --- convenience --------------------------------------------------------

    def iter_message_ids(
        self, query: str | None = None, page_size: int = 100, start_token: str | None = None
    ) -> Iterator[tuple[str, str | None]]:
        """Yield ``(message_id, next_page_token)`` across all result pages.

        The token travels with each id so an interrupted run can be resumed
        from the page it was working on rather than from the beginning.
        """
        page_token = start_token
        while True:
            ids, next_token = self.list_message_ids(query, page_token, page_size)
            for message_id in ids:
                yield message_id, page_token
            if not next_token:
                return
            page_token = next_token


def build_date_query(start: date, extra: str | None = None) -> str:
    """Gmail search restricting results to messages after *start*.

    ``after:`` is inclusive of the given day in the mailbox's own timezone,
    which is the behaviour the configured start date is meant to express.
    """
    query = f"after:{start.strftime('%Y/%m/%d')}"
    return f"{query} {extra}".strip() if extra else query
