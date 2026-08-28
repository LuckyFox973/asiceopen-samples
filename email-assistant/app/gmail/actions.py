"""Write operations against Gmail.

Every function here changes a real mailbox, so each one is small, explicit,
and validates its own inputs. Nothing calls these directly: they run from the
action engine, which decides whether the action was allowed to happen at all.

The important guard is in :func:`assert_safe_labels`. ``users.messages.modify``
with ``addLabelIds: ["TRASH"]`` moves a message to the bin — so an "add a
label" permission that accepted system labels would be an unaudited trash
button. System labels are refused there and handled by the dedicated
operations instead.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from typing import Any

from googleapiclient.errors import HttpError

from app.core.logging import get_logger
from app.gmail.client import gmail_retry

log = get_logger(__name__)

# Gmail's own labels.  Adding or removing these has consequences far beyond
# "tagging", so they never travel through the label operations.
SYSTEM_LABELS = frozenset(
    {
        "INBOX",
        "SENT",
        "DRAFT",
        "TRASH",
        "SPAM",
        "UNREAD",
        "STARRED",
        "IMPORTANT",
        "CHAT",
        "CATEGORY_PERSONAL",
        "CATEGORY_SOCIAL",
        "CATEGORY_PROMOTIONS",
        "CATEGORY_UPDATES",
        "CATEGORY_FORUMS",
    }
)


class UnsafeLabelError(ValueError):
    """A system label was passed to an operation that must not touch one."""


@dataclass(slots=True)
class ActionOutcome:
    ok: bool
    detail: str
    data: dict[str, Any] | None = None
    undo_hint: str | None = None


def assert_safe_labels(label_ids: list[str]) -> None:
    """Refuse system labels. See the module docstring for why this matters."""
    offending = sorted({label for label in label_ids if label.upper() in SYSTEM_LABELS})
    if offending:
        raise UnsafeLabelError(
            f"Refusing to modify system label(s) {', '.join(offending)} through a "
            "label operation. Archiving, trashing and starring have their own "
            "operations, each with its own risk tier."
        )


class GmailActions:
    """Write operations for one authorised mailbox."""

    def __init__(self, service: Any, user_id: str = "me") -> None:
        self._service = service
        self.user_id = user_id

    # --- labels ------------------------------------------------------------

    @gmail_retry
    def list_labels(self) -> list[dict[str, Any]]:
        response = self._service.users().labels().list(userId=self.user_id).execute()
        return response.get("labels", [])

    @gmail_retry
    def ensure_label(self, name: str) -> str:
        """Find or create a user label, returning its id."""
        if name.upper() in SYSTEM_LABELS:
            raise UnsafeLabelError(f"{name} is a Gmail system label and cannot be created.")

        for label in self.list_labels():
            if label.get("name", "").lower() == name.lower():
                return label["id"]

        created = (
            self._service.users()
            .labels()
            .create(
                userId=self.user_id,
                body={
                    "name": name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        log.info("gmail.label_created", name=name, id=created["id"])
        return created["id"]

    @gmail_retry
    def modify_labels(
        self,
        message_id: str,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        allow_system: bool = False,
    ) -> ActionOutcome:
        """Add and/or remove labels on one message."""
        add = add or []
        remove = remove or []
        if not allow_system:
            assert_safe_labels(add + remove)

        body = {"addLabelIds": add, "removeLabelIds": remove}
        result = (
            self._service.users()
            .messages()
            .modify(userId=self.user_id, id=message_id, body=body)
            .execute()
        )
        return ActionOutcome(
            ok=True,
            detail=(f"labels on {message_id}: +{','.join(add) or '-'} -{','.join(remove) or '-'}"),
            data={"labelIds": result.get("labelIds", [])},
            undo_hint=(f"modify_labels(message_id={message_id!r}, add={remove!r}, remove={add!r})"),
        )

    # --- archive -----------------------------------------------------------

    def archive(self, message_id: str) -> ActionOutcome:
        """Remove INBOX. The message stays in All Mail and stays searchable."""
        outcome = self.modify_labels(message_id, remove=["INBOX"], allow_system=True)
        return ActionOutcome(
            ok=True,
            detail=f"archived {message_id}",
            data=outcome.data,
            undo_hint=f"unarchive(message_id={message_id!r})",
        )

    def unarchive(self, message_id: str) -> ActionOutcome:
        outcome = self.modify_labels(message_id, add=["INBOX"], allow_system=True)
        return ActionOutcome(
            ok=True,
            detail=f"moved {message_id} back to the inbox",
            data=outcome.data,
            undo_hint=f"archive(message_id={message_id!r})",
        )

    # --- trash -------------------------------------------------------------

    @gmail_retry
    def trash(self, message_id: str) -> ActionOutcome:
        """Move to the bin. Gmail keeps it ~30 days; untrash reverses it."""
        self._service.users().messages().trash(userId=self.user_id, id=message_id).execute()
        return ActionOutcome(
            ok=True,
            detail=f"moved {message_id} to the bin",
            undo_hint=f"untrash(message_id={message_id!r})",
        )

    @gmail_retry
    def untrash(self, message_id: str) -> ActionOutcome:
        self._service.users().messages().untrash(userId=self.user_id, id=message_id).execute()
        return ActionOutcome(ok=True, detail=f"restored {message_id} from the bin")

    @gmail_retry
    def delete_permanently(self, message_id: str) -> ActionOutcome:
        """Bypass the bin. Nothing undoes this.

        Requires the restricted https://mail.google.com/ scope, which is only
        requested when permanent deletion is explicitly enabled.
        """
        self._service.users().messages().delete(userId=self.user_id, id=message_id).execute()
        log.warning("gmail.deleted_permanently", message_id=message_id)
        return ActionOutcome(
            ok=True,
            detail=f"permanently deleted {message_id}",
            undo_hint=None,
        )

    # --- drafts ------------------------------------------------------------

    @gmail_retry
    def create_draft(
        self,
        to: list[str],
        subject: str,
        body: str,
        thread_id: str | None = None,
        in_reply_to: str | None = None,
        cc: list[str] | None = None,
        from_address: str | None = None,
    ) -> ActionOutcome:
        """Write a draft. Drafts send nothing — they wait in Gmail for you."""
        message = MimeMessage()
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        if from_address:
            message["From"] = from_address
        message["Subject"] = subject
        if in_reply_to:
            # Both headers, so the reply threads correctly in every client.
            message["In-Reply-To"] = f"<{in_reply_to.strip('<>')}>"
            message["References"] = f"<{in_reply_to.strip('<>')}>"
        message.set_content(body)

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        payload: dict[str, Any] = {"message": {"raw": raw}}
        if thread_id:
            payload["message"]["threadId"] = thread_id

        created = self._service.users().drafts().create(userId=self.user_id, body=payload).execute()
        return ActionOutcome(
            ok=True,
            detail=f"draft created for {', '.join(to)}",
            data={
                "draftId": created.get("id"),
                "messageId": (created.get("message") or {}).get("id"),
            },
            undo_hint=f"delete the draft in Gmail (id {created.get('id')})",
        )

    @gmail_retry
    def send_draft(self, draft_id: str) -> ActionOutcome:
        """Send an existing draft. Irreversible once it leaves."""
        sent = (
            self._service.users()
            .drafts()
            .send(userId=self.user_id, body={"id": draft_id})
            .execute()
        )
        log.warning("gmail.sent", draft_id=draft_id, message_id=sent.get("id"))
        return ActionOutcome(
            ok=True,
            detail=f"sent draft {draft_id}",
            data={"messageId": sent.get("id")},
            undo_hint=None,
        )


def describe_http_error(exc: HttpError) -> str:
    """A readable reason, including the scope hint Google buries in the body."""
    status = getattr(exc.resp, "status", "?")
    text = str(exc)
    if status == 403 and "insufficient" in text.lower():
        return (
            "Gmail refused the action: the mailbox was authorised without write "
            "permission. Set GMAIL_WRITE_ENABLED=true and re-run the consent flow."
        )
    if status == 404:
        return "Gmail no longer has that message — it may already have been deleted."
    return f"Gmail returned {status}: {text[:300]}"
