"""An in-memory Gmail API double.

It implements the same surface the sync engine depends on, including paging,
history semantics and attachment fetching, so sync behaviour can be tested
end to end without touching Google.
"""

from __future__ import annotations

from typing import Any

from app.gmail.client import HistoryTooOldError


class FakeGmailClient:
    def __init__(
        self,
        messages: list[dict[str, Any]] | None = None,
        attachments: dict[str, bytes] | None = None,
        profile_history_id: int = 1000,
        page_size_override: int | None = None,
    ) -> None:
        self.messages: dict[str, dict[str, Any]] = {m["id"]: m for m in (messages or [])}
        self.attachments = attachments or {}
        self.profile_history_id = profile_history_id
        self.page_size_override = page_size_override

        self.history: list[dict[str, Any]] = []
        self.history_expired_before: int | None = None

        # Call counters — used to assert that idempotent runs do not refetch.
        self.get_message_calls: list[str] = []
        self.get_attachment_calls: list[tuple[str, str]] = []
        self.list_calls = 0
        self.fail_on_message_ids: set[str] = set()

    # --- data setup ---------------------------------------------------------

    def add_message(self, message: dict[str, Any]) -> None:
        self.messages[message["id"]] = message

    def record_history(self, kind: str, message_id: str, history_id: int) -> None:
        self.history.append({"id": str(history_id), kind: [{"message": {"id": message_id}}]})
        self.profile_history_id = max(self.profile_history_id, history_id)

    # --- API surface --------------------------------------------------------

    def get_profile(self) -> dict[str, Any]:
        return {
            "emailAddress": "peter@foxgroup.sk",
            "messagesTotal": len(self.messages),
            "historyId": str(self.profile_history_id),
        }

    def list_send_as(self) -> list[dict[str, Any]]:
        return [
            {"sendAsEmail": "peter@foxgroup.sk", "isPrimary": True, "displayName": "Peter"},
            {"sendAsEmail": "info@foxgroup.sk", "isDefault": False, "displayName": "Info"},
        ]

    def list_message_ids(
        self,
        query: str | None = None,
        page_token: str | None = None,
        page_size: int = 100,
    ) -> tuple[list[str], str | None]:
        self.list_calls += 1
        size = self.page_size_override or page_size
        ordered = list(self.messages.keys())
        offset = int(page_token) if page_token else 0
        page = ordered[offset : offset + size]
        next_offset = offset + size
        next_token = str(next_offset) if next_offset < len(ordered) else None
        return page, next_token

    def get_message(self, message_id: str) -> dict[str, Any]:
        self.get_message_calls.append(message_id)
        if message_id in self.fail_on_message_ids:
            raise RuntimeError(f"simulated fetch failure for {message_id}")
        if message_id not in self.messages:
            raise KeyError(message_id)
        return self.messages[message_id]

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        self.get_attachment_calls.append((message_id, attachment_id))
        if attachment_id not in self.attachments:
            raise KeyError(attachment_id)
        return self.attachments[attachment_id]

    def list_history(
        self, start_history_id: int, page_token: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None, int | None]:
        if (
            self.history_expired_before is not None
            and start_history_id < self.history_expired_before
        ):
            raise HistoryTooOldError(f"historyId {start_history_id} expired")
        records = [h for h in self.history if int(h["id"]) > start_history_id]
        return records, None, self.profile_history_id
