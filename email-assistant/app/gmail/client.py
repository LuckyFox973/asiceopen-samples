"""Thin, retrying wrapper over the Gmail REST API.

Everything the sync engine needs, and nothing it does not: MVP 1 requests
read-only scopes, so this client physically cannot send, delete or modify
mail even if asked to.
"""

from __future__ import annotations

from collections.abc import Iterator
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


class HistoryTooOldError(RuntimeError):
    """Gmail no longer has history back to the stored ID — a full resync is due."""


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", None)
        if status == 403:
            # 403 is retryable only for quota errors, not for missing scopes.
            return "rateLimitExceeded" in str(exc) or "userRateLimitExceeded" in str(exc)
        return status in RETRYABLE_STATUS
    return isinstance(exc, (TimeoutError, ConnectionError))


gmail_retry = retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(5),
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

    def __init__(self, credentials: Any, user_id: str = "me") -> None:
        self._service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        self.user_id = user_id

    # --- profile / identity -------------------------------------------------

    @gmail_retry
    def get_profile(self) -> dict[str, Any]:
        return self._service.users().getProfile(userId=self.user_id).execute()

    @gmail_retry
    def list_send_as(self) -> list[dict[str, Any]]:
        """All addresses this mailbox may send from — the alias list."""
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
        return (
            self._service.users()
            .messages()
            .get(userId=self.user_id, id=message_id, format="full")
            .execute()
        )

    @gmail_retry
    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        import base64

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
