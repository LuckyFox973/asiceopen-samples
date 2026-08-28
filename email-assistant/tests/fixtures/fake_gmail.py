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


class FakeGmailActions:
    """Records write operations instead of performing them.

    Every method mirrors :class:`app.gmail.actions.GmailActions`, so the action
    engine runs unchanged and the test can assert exactly which calls a policy
    decision produced — including that a refused action produced none.
    """

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.labels: dict[str, str] = {}
        self.fail_on = fail_on or set()
        self._next_id = 0

    def _record(self, call: str, **kwargs):
        if call in self.fail_on:
            raise RuntimeError(f"simulated Gmail failure in {call}")
        self.calls.append((call, kwargs))

    def called(self, name: str) -> bool:
        return any(call == name for call, _ in self.calls)

    def call_count(self, name: str) -> int:
        return sum(1 for call, _ in self.calls if call == name)

    def last(self, name: str) -> dict:
        for call, kwargs in reversed(self.calls):
            if call == name:
                return kwargs
        raise AssertionError(f"{name} was never called")

    # --- mirrored surface ---------------------------------------------------

    def list_labels(self):
        return [{"id": v, "name": k} for k, v in self.labels.items()]

    def ensure_label(self, name: str) -> str:
        from app.gmail.actions import SYSTEM_LABELS, UnsafeLabelError

        if name.upper() in SYSTEM_LABELS:
            raise UnsafeLabelError(f"{name} is a Gmail system label.")
        self._record("ensure_label", name=name)
        return self.labels.setdefault(name, f"Label_{len(self.labels) + 1}")

    def modify_labels(self, message_id, add=None, remove=None, allow_system=False):
        from app.gmail.actions import ActionOutcome, assert_safe_labels

        if not allow_system:
            assert_safe_labels((add or []) + (remove or []))
        self._record("modify_labels", message_id=message_id, add=add, remove=remove)
        return ActionOutcome(ok=True, detail="labels changed", data={"labelIds": add or []})

    def archive(self, message_id):
        from app.gmail.actions import ActionOutcome

        self._record("archive", message_id=message_id)
        return ActionOutcome(
            ok=True, detail=f"archived {message_id}", undo_hint=f"unarchive({message_id})"
        )

    def unarchive(self, message_id):
        from app.gmail.actions import ActionOutcome

        self._record("unarchive", message_id=message_id)
        return ActionOutcome(ok=True, detail=f"unarchived {message_id}")

    def trash(self, message_id):
        from app.gmail.actions import ActionOutcome

        self._record("trash", message_id=message_id)
        return ActionOutcome(
            ok=True, detail=f"trashed {message_id}", undo_hint=f"untrash({message_id})"
        )

    def untrash(self, message_id):
        from app.gmail.actions import ActionOutcome

        self._record("untrash", message_id=message_id)
        return ActionOutcome(ok=True, detail=f"untrashed {message_id}")

    def delete_permanently(self, message_id):
        from app.gmail.actions import ActionOutcome

        self._record("delete_permanently", message_id=message_id)
        return ActionOutcome(ok=True, detail=f"deleted {message_id}", undo_hint=None)

    def create_draft(
        self, to, subject, body, thread_id=None, in_reply_to=None, cc=None, from_address=None
    ):
        from app.gmail.actions import ActionOutcome

        self._next_id += 1
        self._record(
            "create_draft",
            to=to,
            subject=subject,
            body=body,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
        )
        return ActionOutcome(
            ok=True, detail=f"draft for {', '.join(to)}", data={"draftId": f"draft-{self._next_id}"}
        )

    def send_draft(self, draft_id):
        from app.gmail.actions import ActionOutcome

        self._record("send_draft", draft_id=draft_id)
        return ActionOutcome(ok=True, detail=f"sent {draft_id}", data={"messageId": "sent-1"})
