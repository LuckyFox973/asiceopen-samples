"""MCP server: the assistant's memory, as tools Claude can call.

This is what makes the system usable without an API key. Claude — in Claude
Code, or through a connector — queries this database directly, so reasoning
over the mail costs nothing beyond the subscription already being paid for.

Read-only by design, matching the read-only Gmail scopes. Nothing here sends,
deletes or modifies mail; ``run_sync`` only fetches. Actions that change a
mailbox arrive with the approval workflow, not before.
"""

from __future__ import annotations

import functools
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from mcp.server.mcpserver import MCPServer
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import (
    ActionType,
    Attachment,
    AuditLog,
    Client,
    DocumentText,
    EmailMessage,
    EmailThread,
    Matter,
    MatterLink,
    SyncState,
)
from app.db.session import session_scope
from app.mcp.formatting import (
    BODY_CHARS,
    SNIPPET_CHARS,
    clip,
    direction_arrow,
    empty,
    more,
    stamp,
)
from app.services.accounts import list_accounts
from app.services.documents import get_document_text
from app.services.matters import matter_contents
from app.services.search import (
    MessageSearchQuery,
    search_documents,
    search_messages,
    search_threads,
)
from app.services.versions import diff_versions, version_history

log = get_logger(__name__)

server = MCPServer(
    name="email-assistant",
    instructions=(
        "Durable memory over the user's Gmail: messages, threads, contacts, "
        "attachments with their extracted text, clients and matters (case "
        "files). Everything is read-only.\n\n"
        "Search is diacritics-insensitive, so 'kasacna staznost' finds "
        "'Kasačná sťažnosť'. Queries support quoted phrases and -exclusion.\n\n"
        "search_emails looks in subjects and message bodies; search_documents "
        "looks inside attachment text (PDF, Word, Excel). Use the latter when "
        "the answer is likely inside a document rather than in an e-mail.\n\n"
        "Word documents carry tracked changes: text struck out by a revision "
        "is stored separately and deliberately not indexed, so a figure "
        "someone removed never surfaces as current."
    ),
)


class ToolInputError(ValueError):
    """Bad input from the caller — reported as text, never as a crash."""


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value.strip())
    except (ValueError, AttributeError) as exc:
        raise ToolInputError(
            f"{label} must be a UUID like "
            f"'0f7b8ec7-21a3-4f1e-9c2b-8d5a1e6f3b40', not {value!r}. "
            "Ids come from search_emails, search_documents or list_clients."
        ) from exc


def readable_errors(func: Callable[..., str]) -> Callable[..., str]:
    """Turn a failure into an answer the model can act on.

    A raised exception reaches the client as an opaque "error executing tool";
    a sentence explaining what was wrong lets Claude correct itself and try
    again, which is the difference between a dead end and a retry.
    """

    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> str:
        try:
            return func(*args, **kwargs)
        except ToolInputError as exc:
            return f"Invalid input: {exc}"
        except Exception as exc:  # noqa: BLE001 - the tool must answer, not crash
            log.warning("mcp.tool_failed", tool=func.__name__, error=str(exc))
            return f"The query failed: {exc}"

    return wrapper


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------


@server.tool(
    description=(
        "Search stored e-mail by words in the subject or body, with optional "
        "filters. Diacritics-insensitive. Returns matching messages newest or "
        "best-ranked first, each with its thread id for follow-up."
    )
)
@readable_errors
def search_emails(
    query: str = "",
    participant: str = "",
    direction: str = "",
    date_from: str = "",
    date_to: str = "",
    has_attachments: bool | None = None,
    limit: int = 10,
) -> str:
    """Find messages. `direction` is inbound, outbound or internal."""
    with session_scope() as session:
        results = search_messages(
            session,
            MessageSearchQuery(
                text_query=query or None,
                participant=participant or None,
                direction=direction or None,
                date_from=_date(date_from),
                date_to=_date(date_to),
                has_attachments=has_attachments,
                limit=max(1, min(limit, 50)),
            ),
        )
        if not results.hits:
            return empty("messages")

        lines = [f"{results.total} match(es):\n"]
        for hit in results.hits:
            message = hit.message
            lines.append(
                f"{stamp(message.internal_date or message.sent_at)} "
                f"{direction_arrow(message.direction)} {message.from_address}\n"
                f"  {message.subject or '(no subject)'}\n"
                f"  {clip(message.snippet, SNIPPET_CHARS)}\n"
                f"  thread={message.thread_id} message={message.id}"
                + ("  [has attachments]" if message.has_attachments else "")
            )
        return "\n".join(lines) + more(len(results.hits), results.total, "message")


@server.tool(
    description=(
        "Read a whole conversation in order, with each message's direction, "
        "sender, body and attachments. Use after search_emails returns a "
        "thread id."
    )
)
@readable_errors
def get_thread(thread_id: str, max_messages: int = 30) -> str:
    """The full text of one conversation."""
    with session_scope() as session:
        thread = session.get(EmailThread, _parse_uuid(thread_id, "thread_id"))
        if thread is None:
            return f"No thread {thread_id}."

        messages = list(
            session.scalars(
                select(EmailMessage)
                .where(EmailMessage.thread_id == thread.id)
                .order_by(func.coalesce(EmailMessage.internal_date, EmailMessage.sent_at).asc())
                .limit(max(1, min(max_messages, 100)))
            ).all()
        )

        header = (
            f"Thread: {thread.subject or '(no subject)'}\n"
            f"{thread.message_count} message(s), "
            f"{stamp(thread.first_message_at)} to {stamp(thread.last_message_at)}\n"
            f"Last message was {thread.last_message_direction or 'unknown'}.\n"
        )
        parts = [header]
        for message in messages:
            attachments = session.scalars(
                select(Attachment.filename).where(Attachment.message_id == message.id)
            ).all()
            parts.append(
                f"\n--- {stamp(message.internal_date or message.sent_at)} "
                f"{direction_arrow(message.direction)} {message.from_address}"
                + (f" → {message.account_address}" if message.account_address else "")
                + f"\n{clip(message.body_text, BODY_CHARS) or '(no text body)'}"
                + (
                    f"\n  attachments: {', '.join(a for a in attachments if a)}"
                    if attachments
                    else ""
                )
            )
        return "".join(parts) + more(len(messages), thread.message_count, "message")


@server.tool(
    name="search_threads",
    description=(
        "Find conversations by subject or by the content of their messages. "
        "Cheaper than search_emails when you want the conversation, not the "
        "individual message."
    ),
)
@readable_errors
def search_threads_tool(query: str = "", limit: int = 15) -> str:
    """Find conversations."""
    with session_scope() as session:
        threads, total = search_threads(session, None, query or None, limit=limit)
        if not threads:
            return empty("threads")
        lines = [f"{total} thread(s):\n"]
        for thread in threads:
            lines.append(
                f"{stamp(thread.last_message_at)} "
                f"{direction_arrow(thread.last_message_direction)} "
                f"{thread.subject or '(no subject)'}\n"
                f"  {thread.message_count} message(s)  id={thread.id}"
            )
        return "\n".join(lines) + more(len(threads), total, "thread")


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@server.tool(
    name="search_documents",
    description=(
        "Search inside the text of attachments — PDF, Word, Excel — rather "
        "than in e-mail bodies. Use this when the answer is likely in a "
        "document: 'where did the tax authority claim the CMR notes were "
        "duplicates?'. Returns the matching passage."
    ),
)
@readable_errors
def search_documents_tool(query: str, limit: int = 10) -> str:
    """Find text inside stored documents."""
    with session_scope() as session:
        hits, total = search_documents(session, None, query, limit=limit)
        if not hits:
            return empty("documents")
        lines = [f"{total} document(s):\n"]
        for hit in hits:
            pages = f", {hit.document.page_count} pages" if hit.document.page_count else ""
            revised = (
                f"\n  tracked changes: {hit.document.revision_summary}"
                if hit.document.revision_summary
                else ""
            )
            lines.append(
                f"{hit.attachment.filename or '(unnamed)'}{pages}\n"
                f"  {clip(hit.headline, 400)}"
                f"{revised}\n"
                f"  attachment={hit.attachment.id} message={hit.attachment.message_id}"
            )
        return "\n".join(lines) + more(len(hits), total, "document")


def _why_no_text(status: str) -> str:
    """What the reader should do about a document that carries no text."""
    return {
        "needs_ocr": " This is a scan; OCR is not enabled, so its text is not searchable.",
        "encrypted": " The file is password protected; the password is needed to read it.",
        "unsupported": " No extractor handles this format.",
    }.get(status, "")


@server.tool(
    description=(
        "Read the full extracted text of one attachment, plus any tracked "
        "changes and margin comments. Text removed by a revision is reported "
        "separately from the current text."
    )
)
@readable_errors
def get_attachment_text(attachment_id: str, max_chars: int = 12000) -> str:
    """The text of one document."""
    with session_scope() as session:
        document = get_document_text(session, _parse_uuid(attachment_id, "attachment_id"))
        if document is None:
            return (
                "No extracted text: the attachment is unknown, its bytes were "
                "never downloaded, or extraction has not run."
            )
        if document.status != "extracted":
            return (
                f"Status: {document.status}."
                + (f" {document.error}" if document.error else "")
                + _why_no_text(document.status)
            )

        parts = [f"[{document.method}, {document.char_count:,} characters]"]
        if document.revision_summary:
            parts.append(f"Tracked changes: {document.revision_summary}")
        parts.append(f"\n{clip(document.text, max(500, min(max_chars, 50000)))}")
        if document.comment_text:
            parts.append(f"\nComments:\n{clip(document.comment_text, 2000)}")
        if document.deleted_text:
            parts.append(
                f"\nRemoved by revision (NOT current text):\n{clip(document.deleted_text, 2000)}"
            )
        return "\n".join(parts)


@server.tool(
    description=(
        "List every version of a document we have received, oldest first, and "
        "summarise what changed in the most recent one. Files are stored by "
        "content, so a revised Word document is a separate file; this puts the "
        "versions back together by name."
    )
)
@readable_errors
def document_versions(attachment_id: str) -> str:
    """Versions of one document, with a diff of the last change."""
    with session_scope() as session:
        target = _parse_uuid(attachment_id, "attachment_id")
        history = version_history(session, target)
        if not history.versions:
            return "No versions found — unknown attachment, or it has no file name."

        lines = [f"Document family '{history.family}': {history.count} version(s)\n"]
        for index, version in enumerate(history.versions, start=1):
            lines.append(
                f"v{index}  {stamp(version.received_at)}  {version.filename}\n"
                f"    {version.char_count:,} chars  attachment={version.attachment_id}"
                + (
                    f"\n    tracked changes: {version.revision_summary}"
                    if version.revision_summary
                    else ""
                )
            )

        if history.count > 1:
            older, newer = history.versions[-2], history.versions[-1]
            diff = diff_versions(session, older.attachment_id, newer.attachment_id)
            if diff is not None:
                lines.append(f"\nMost recent change: {diff.summary()}")
                for line in diff.removed_lines[:8]:
                    lines.append(f"  - {clip(line, 200)}")
                for line in diff.added_lines[:8]:
                    lines.append(f"  + {clip(line, 200)}")
        return "\n".join(lines)


@server.tool(description="Compare the extracted text of two documents and report what changed.")
@readable_errors
def diff_documents(older_attachment_id: str, newer_attachment_id: str) -> str:
    """Diff two documents."""
    with session_scope() as session:
        diff = diff_versions(
            session,
            _parse_uuid(older_attachment_id, "older_attachment_id"),
            _parse_uuid(newer_attachment_id, "newer_attachment_id"),
        )
        if diff is None:
            return "Both attachments must exist and have extracted text."
        lines = [diff.summary()]
        for line in diff.removed_lines[:20]:
            lines.append(f"- {clip(line, 250)}")
        for line in diff.added_lines[:20]:
            lines.append(f"+ {clip(line, 250)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Clients and matters
# ---------------------------------------------------------------------------


@server.tool(description="List clients and their open matters (case files).")
@readable_errors
def list_clients(limit: int = 50) -> str:
    """Who we act for, and what is open."""
    with session_scope() as session:
        clients = session.scalars(select(Client).order_by(Client.display_name).limit(limit)).all()
        if not clients:
            return empty("clients")

        lines = []
        for client in clients:
            matters = session.scalars(select(Matter).where(Matter.client_id == client.id)).all()
            lines.append(
                f"{client.display_name}"
                + (f" [{client.reference}]" if client.reference else "")
                + f"  ({client.status})  id={client.id}"
            )
            for matter in matters:
                lines.append(
                    f"    {matter.title}"
                    + (f" [{matter.reference}]" if matter.reference else "")
                    + f"  ({matter.status})  id={matter.id}"
                )
        return "\n".join(lines)


@server.tool(
    description=(
        "Everything filed under one matter: what it contains, and its most "
        "recent conversations. This is the 'spis' at a glance."
    )
)
@readable_errors
def get_matter(matter_id: str, limit: int = 15) -> str:
    """One case file."""
    with session_scope() as session:
        matter = session.get(Matter, _parse_uuid(matter_id, "matter_id"))
        if matter is None:
            return f"No matter {matter_id}."

        client = session.get(Client, matter.client_id)
        counts = matter_contents(session, matter.id)
        lines = [
            f"{client.display_name if client else '?'} / {matter.title}"
            + (f" [{matter.reference}]" if matter.reference else ""),
            f"Status: {matter.status}. Opened {matter.opened_on or '?'}.",
            f"Filed: {counts['thread']} thread(s), {counts['messages_in_threads']} message(s).",
        ]
        if matter.description:
            lines.append(f"\n{matter.description}")

        thread_ids = list(
            session.scalars(
                select(MatterLink.target_id).where(
                    MatterLink.matter_id == matter.id,
                    MatterLink.target_type == "thread",
                )
            ).all()
        )
        if thread_ids:
            threads = session.scalars(
                select(EmailThread)
                .where(EmailThread.id.in_(thread_ids))
                .order_by(EmailThread.last_message_at.desc().nullslast())
                .limit(limit)
            ).all()
            lines.append("\nConversations:")
            for thread in threads:
                lines.append(
                    f"  {stamp(thread.last_message_at)} "
                    f"{direction_arrow(thread.last_message_direction)} "
                    f"{thread.subject or '(no subject)'}  id={thread.id}"
                )
        return "\n".join(lines)


@server.tool(
    description=(
        "Filings the system was not confident enough to make on its own, with "
        "the rule that proposed each and why. These are waiting for a human "
        "decision."
    )
)
@readable_errors
def review_queue(limit: int = 20) -> str:
    """Uncertain filings awaiting confirmation."""
    from app.services.matters import links_needing_review

    with session_scope() as session:
        queue = links_needing_review(session, limit)
        if not queue:
            return "Nothing waiting for review."
        lines = []
        for link in queue:
            matter = session.get(Matter, link.matter_id)
            thread = session.get(EmailThread, link.target_id)
            lines.append(
                f"confidence {link.confidence:.2f} via {link.method}\n"
                f"  thread: {thread.subject if thread else link.target_id}\n"
                f"  matter: {matter.title if matter else link.matter_id}\n"
                f"  reason: {link.reason or '-'}\n"
                f"  link={link.id}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# State and activity
# ---------------------------------------------------------------------------


@server.tool(
    description=(
        "What arrived recently, grouped by whether the last word was theirs or "
        "ours. A deterministic starting point for a daily briefing — no "
        "classification, just who spoke last."
    )
)
@readable_errors
def recent_activity(days: int = 3, limit: int = 30) -> str:
    """Recent conversations, split by who is waiting on whom."""
    # `limit` bounds the result size, so the window itself is left generous —
    # silently shrinking a requested range would give a confidently wrong
    # answer to "has anything happened this year?".
    days = max(1, min(days, 3650))
    since = datetime.now(UTC) - timedelta(days=days)
    with session_scope() as session:
        threads = session.scalars(
            select(EmailThread)
            .where(EmailThread.last_message_at >= since)
            .order_by(EmailThread.last_message_at.desc())
            .limit(max(1, min(limit, 100)))
        ).all()
        if not threads:
            return f"No conversations in the last {days} day(s)."

        theirs = [t for t in threads if t.last_message_direction == "inbound"]
        ours = [t for t in threads if t.last_message_direction == "outbound"]
        other = [t for t in threads if t not in theirs and t not in ours]

        def block(title: str, items: list, note: str) -> str:
            if not items:
                return ""
            lines = [f"\n{title} ({len(items)}) — {note}"]
            for thread in items:
                lines.append(
                    f"  {stamp(thread.last_message_at)}  "
                    f"{thread.subject or '(no subject)'}  id={thread.id}"
                )
            return "\n".join(lines)

        return (
            f"Last {days} day(s), {len(threads)} conversation(s):"
            + block("They wrote last", theirs, "may be waiting on you")
            + block("You wrote last", ours, "you may be waiting on them")
            + block("Other", other, "internal or unclear")
            + "\n\nNote: this is who spoke last, not whether a reply is owed. "
            "A newsletter or an automatic receipt looks the same as a question."
        )


@server.tool(description="Synchronisation state and what is stored, per mailbox.")
@readable_errors
def sync_status() -> str:
    """Is the mailbox up to date, and how much is stored."""
    with session_scope() as session:
        accounts = list_accounts(session)
        if not accounts:
            return "No mailbox connected yet."

        lines = []
        for account in accounts:
            state = session.scalar(select(SyncState).where(SyncState.account_id == account.id))
            messages = session.scalar(
                select(func.count(EmailMessage.id)).where(EmailMessage.account_id == account.id)
            )
            threads = session.scalar(
                select(func.count(EmailThread.id)).where(EmailThread.account_id == account.id)
            )
            attachments = session.scalar(
                select(func.count(Attachment.id)).where(Attachment.account_id == account.id)
            )
            extracted = session.scalar(
                select(func.count(DocumentText.id)).where(DocumentText.status == "extracted")
            )
            lines.append(
                f"{account.email}\n"
                f"  {messages} message(s), {threads} thread(s), "
                f"{attachments} attachment(s), {extracted} document(s) with text\n"
                f"  addresses: {', '.join(a.address for a in account.addresses)}\n"
                f"  last sync: {stamp(state.last_sync_at) if state else 'never'}"
                + (
                    "\n  initial sync still in progress"
                    if state and state.initial_sync_completed_at is None
                    else ""
                )
            )
        return "\n".join(lines)


@server.tool(
    description=(
        "Fetch new mail from Gmail now. Read-only: this only downloads, it "
        "never sends, deletes or modifies anything in the mailbox."
    )
)
@readable_errors
def run_sync(mailbox_email: str = "") -> str:
    """Pull new mail."""
    from app.services.runner import run_sync as do_sync

    with session_scope() as session:
        accounts = list_accounts(session, active_only=True)
        if mailbox_email:
            accounts = [a for a in accounts if a.email == mailbox_email.strip().lower()]
        if not accounts:
            return "No matching active mailbox."

        results = []
        for account in accounts:
            try:
                run = do_sync(session, account, mode="auto")
                results.append(
                    f"{account.email}: {run.kind} sync {run.status} — "
                    f"{run.messages_created} new, {run.messages_updated} updated"
                )
            except Exception as exc:  # noqa: BLE001 - report, do not crash the tool
                results.append(f"{account.email}: failed — {exc}")
        return "\n".join(results)


@server.tool(
    description=(
        "Recent entries from the audit log: what the system did, when, and "
        "whether it was automatic."
    )
)
@readable_errors
def recent_actions(limit: int = 20) -> str:
    """What the system has been doing."""
    with session_scope() as session:
        entries = session.scalars(
            select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(max(1, min(limit, 100)))
        ).all()
        if not entries:
            return empty("audit entries")
        return "\n".join(
            f"{stamp(entry.occurred_at)}  {entry.action}  "
            f"[{'auto' if entry.automatic else 'manual'}]\n  {entry.summary or ''}"
            for entry in entries
        )


# ---------------------------------------------------------------------------
# Acting on the mailbox
# ---------------------------------------------------------------------------
#
# Safe, reversible actions run straight away.  Everything destructive returns
# an id and waits: the model proposes, a person decides.  That is the rule the
# brief set, and no configuration moves an action out of the approval tier.


def _act(
    action_type: ActionType,
    description: str,
    gmail_target_id: str | None = None,
    payload: dict | None = None,
    reason: str | None = None,
    target_id: uuid.UUID | None = None,
    target_type: str = "message",
) -> str:
    from app.services.actions import (
        ActionError,
        ActionRequest,
        propose_and_maybe_execute,
        risk_tier,
    )
    from app.services.runner import build_actions

    settings = get_settings()
    with session_scope() as session:
        accounts = list_accounts(session, active_only=True)
        if not accounts:
            return "No connected mailbox."
        account = accounts[0]

        try:
            gmail = build_actions(session, account, settings)
        except PermissionError as exc:
            return str(exc)

        try:
            action = propose_and_maybe_execute(
                session,
                account,
                ActionRequest(
                    action_type=action_type,
                    description=description,
                    target_type=target_type,
                    target_id=target_id,
                    gmail_target_id=gmail_target_id,
                    payload=payload,
                    reason=reason,
                ),
                gmail=gmail,
                settings=settings,
            )
        except ActionError as exc:
            return str(exc)

        if action.status == "executed":
            undo = f"\nUndo: {action.undo_hint}" if action.undo_hint else ""
            return f"Done — {action.description}.{undo}"
        if action.status == "failed":
            return f"Failed — {action.error}"
        return (
            f"Waiting for approval [{risk_tier(action_type).value}] — "
            f"{action.description}\n"
            f"Nothing has changed in the mailbox yet. Approve with "
            f"approve_action(action_id='{action.id}'), or the user can run "
            f"`python -m app.cli action approve {action.id}`."
        )


@server.tool(
    description=(
        "Apply one of the assistant's own labels to a message. Safe and "
        "reversible, so it happens immediately. Gmail's system labels (INBOX, "
        "TRASH, SPAM…) are refused here — archiving and binning have their own "
        "tools with their own safeguards."
    )
)
@readable_errors
def apply_label(gmail_message_id: str, label: str, reason: str = "") -> str:
    """Tag a message."""
    return _act(
        ActionType.LABEL_ADD,
        description=f"Apply label {label!r} to {gmail_message_id}",
        gmail_target_id=gmail_message_id,
        payload={"labels": [label]},
        reason=reason or None,
    )


@server.tool(
    description=(
        "Write a draft reply. Drafts send nothing — the text waits in Gmail "
        "for the user to read, edit and send. Pass gmail_thread_id and "
        "in_reply_to so it threads correctly."
    )
)
@readable_errors
def draft_reply(
    to: str,
    subject: str,
    body: str,
    gmail_thread_id: str = "",
    in_reply_to: str = "",
    cc: str = "",
    reason: str = "",
) -> str:
    """Prepare a reply for the user to review."""
    from app.db.models import ActionType

    recipients = [a.strip() for a in to.split(",") if a.strip()]
    if not recipients:
        raise ToolInputError("At least one recipient is required.")

    return _act(
        ActionType.DRAFT_CREATE,
        description=f"Draft to {', '.join(recipients)}: {subject or '(no subject)'}",
        payload={
            "to": recipients,
            "cc": [a.strip() for a in cc.split(",") if a.strip()] or None,
            "subject": subject,
            "body": body,
            "thread_id": gmail_thread_id or None,
            "in_reply_to": in_reply_to or None,
        },
        reason=reason or None,
    )


@server.tool(
    description=(
        "Archive a message: remove it from the inbox. It stays in All Mail and "
        "stays searchable, and unarchive puts it back. Runs immediately only if "
        "the user enabled automatic archiving; otherwise it waits for approval."
    )
)
@readable_errors
def archive_message(gmail_message_id: str, reason: str = "") -> str:
    """Take a message out of the inbox."""
    return _act(
        ActionType.ARCHIVE,
        description=f"Archive {gmail_message_id}",
        gmail_target_id=gmail_message_id,
        reason=reason or None,
    )


@server.tool(description="Put an archived message back in the inbox. Always safe.")
@readable_errors
def unarchive_message(gmail_message_id: str) -> str:
    """Restore a message to the inbox."""
    return _act(
        ActionType.UNARCHIVE,
        description=f"Move {gmail_message_id} back to the inbox",
        gmail_target_id=gmail_message_id,
    )


@server.tool(
    description=(
        "List the Drive folders that documents are filed into, one per company "
        "of the user's own, with the terms that identify each. Filing is by the "
        "company that was BILLED, not by who sent the invoice."
    )
)
@readable_errors
def filing_folders() -> str:
    """Where documents can be filed, and how each folder is recognised."""
    from app.services.filing import list_folders

    with session_scope() as session:
        folders = list_folders(session)
        if not folders:
            return (
                "No filing folders are registered, so nothing can be filed yet. "
                "They are added from the command line: "
                "python -m app.cli folder add <name> <drive-folder-id> --match <term>"
            )
        return "\n".join(
            f"{f.name}\n  drive folder: {f.drive_folder_id}\n"
            f"  identified by: {', '.join(f.match_terms)}"
            for f in folders
        )


@server.tool(
    description=(
        "Work out which company an attachment was billed to, WITHOUT filing it. "
        "Use this before file_to_drive to show the user what would happen. "
        "Returns the folder and what matched, or says why it cannot tell."
    )
)
@readable_errors
def which_folder(attachment_id: str) -> str:
    """Which folder a document belongs in, and on what evidence."""
    from app.db.models import Attachment
    from app.services.filing import document_text_for, resolve

    with session_scope() as session:
        attachment = session.get(Attachment, _parse_uuid(attachment_id, "attachment_id"))
        if attachment is None:
            return f"No attachment {attachment_id}."

        outcome = resolve(session, document_text_for(session, attachment))
        if outcome.resolved:
            best = outcome.suggestion
            return (
                f"{attachment.filename} belongs in {best.folder.name} "
                f"({best.confidence:.0%} — matched {', '.join(best.matched)})."
            )
        lines = [f"Cannot tell where {attachment.filename} belongs: {outcome.reason}"]
        lines += [
            f"  {c.folder.name} ({c.confidence:.0%}, matched {', '.join(c.matched)})"
            for c in outcome.candidates
        ]
        lines.append("The user can choose one, and file_to_drive takes a folder name.")
        return "\n".join(lines)


@server.tool(
    description=(
        "File an attachment into its company's Drive folder and then archive the "
        "message that carried it. THE USER MUST ASK FOR THIS EXPLICITLY — an "
        "invoice is filed once it has been paid or booked, and only they know "
        "when that is. Never call it to tidy up on your own initiative. If the "
        "company cannot be told from the document, it files nothing; pass "
        "folder= to choose. Nothing is archived unless the upload succeeded."
    )
)
@readable_errors
def file_to_drive(attachment_id: str, folder: str = "", archive: bool = True) -> str:
    """Upload one document to Drive, then archive its message."""
    from app.db.models import Attachment, MailboxAccount
    from app.gmail.actions import GmailActions
    from app.services.drive import DriveClient
    from app.services.filing import file_attachment, list_folders
    from app.services.runner import build_client, build_credentials
    from app.services.storage import build_storage

    settings = get_settings()
    if not settings.drive_write_enabled:
        return (
            "Filing to Drive is switched off. It needs write access to the "
            "user's Drive, which is wider than any other permission here, so it "
            "is never on by default. They can set DRIVE_WRITE_ENABLED=true in "
            ".env and re-authorise."
        )

    with session_scope() as session:
        attachment = session.get(Attachment, _parse_uuid(attachment_id, "attachment_id"))
        if attachment is None:
            return f"No attachment {attachment_id}."

        chosen = None
        if folder:
            chosen = next(
                (f for f in list_folders(session) if f.name.lower() == folder.lower()), None
            )
            if chosen is None:
                known = ", ".join(f.name for f in list_folders(session)) or "none"
                return f"No filing folder named {folder!r}. Registered: {known}."

        account = session.get(MailboxAccount, attachment.account_id)
        result = file_attachment(
            session,
            account,
            attachment,
            build_storage(settings),
            DriveClient(build_credentials(session, account, settings)),
            gmail=GmailActions(build_client(session, account, settings)) if archive else None,
            folder=chosen,
            archive_after=archive,
            requested_by="user",
            settings=settings,
        )

        if not result.filed:
            return f"Nothing was filed and nothing was archived: {result.skipped}"
        answer = f"Filed {result.filename} into {result.folder.name}."
        if result.archive is not None:
            answer += " The message it came from is archived."
        elif result.skipped:
            answer += f" {result.skipped}"
        return answer


@server.tool(
    description=(
        "Propose moving a message to the bin. Never happens on its own — it "
        "returns an id and waits for the user to approve. Gmail keeps binned "
        "mail about 30 days, and restore_message reverses it."
    )
)
@readable_errors
def request_trash(gmail_message_id: str, reason: str = "") -> str:
    """Ask to bin a message."""
    return _act(
        ActionType.TRASH,
        description=f"Move {gmail_message_id} to the bin",
        gmail_target_id=gmail_message_id,
        reason=reason or None,
    )


@server.tool(description="Take a message back out of the bin. Always safe.")
@readable_errors
def restore_message(gmail_message_id: str) -> str:
    """Undo a binning."""
    return _act(
        ActionType.UNTRASH,
        description=f"Restore {gmail_message_id} from the bin",
        gmail_target_id=gmail_message_id,
    )


@server.tool(
    description=(
        "Propose permanently deleting a message, bypassing the bin. Nothing "
        "undoes this. Requires approval, and requires the user to have enabled "
        "permanent deletion — moving to the bin is almost always what is "
        "wanted instead."
    )
)
@readable_errors
def request_permanent_delete(gmail_message_id: str, reason: str = "") -> str:
    """Ask to delete a message irreversibly."""
    return _act(
        ActionType.DELETE_PERMANENT,
        description=f"PERMANENTLY delete {gmail_message_id} — cannot be undone",
        gmail_target_id=gmail_message_id,
        reason=reason or None,
    )


@server.tool(
    description=(
        "Actions waiting for the user's decision, with what each would do and why it was proposed."
    )
)
@readable_errors
def pending_actions(limit: int = 20) -> str:
    """What is waiting to be approved."""
    from app.services.actions import describe_target, pending

    with session_scope() as session:
        items = pending(session, limit)
        if not items:
            return "Nothing waiting for approval."
        return "\n".join(
            f"{item.action_type} [{item.risk_tier}]  {item.description}\n"
            f"  target: {describe_target(session, item)}\n"
            f"  reason: {item.reason or '-'}\n"
            f"  id={item.id}"
            for item in items
        )


@server.tool(
    description=(
        "Approve a waiting action and carry it out. Only call this when the "
        "user has said yes to this specific action — approval is theirs to "
        "give, not yours to infer."
    )
)
@readable_errors
def approve_action(action_id: str) -> str:
    """Execute an action the user approved."""
    from app.db.models import MailboxAccount
    from app.services.actions import ActionError, approve, execute
    from app.services.runner import build_actions

    with session_scope() as session:
        try:
            action = approve(session, _parse_uuid(action_id, "action_id"))
            account = session.get(MailboxAccount, action.account_id)
            done = execute(session, action, build_actions(session, account), get_settings())
        except (ActionError, PermissionError) as exc:
            return str(exc)

        if done.status == "executed":
            undo = f"\nUndo: {done.undo_hint}" if done.undo_hint else ""
            return f"Done — {done.description}.{undo}"
        return f"Failed — {done.error}"


@server.tool(description="Reject a waiting action. Nothing is changed in the mailbox.")
@readable_errors
def reject_action(action_id: str, note: str = "") -> str:
    """Decline a proposed action."""
    from app.services.actions import ActionError, reject

    with session_scope() as session:
        try:
            reject(session, _parse_uuid(action_id, "action_id"), note=note or None)
        except ActionError as exc:
            return str(exc)
        return "Rejected. Nothing was changed in the mailbox."


def _date(value: str) -> datetime | None:
    if not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def main() -> None:
    """Entry point. stdio by default; streamable-http for remote connectors."""
    import argparse

    parser = argparse.ArgumentParser(description="Email Assistant MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="stdio for a local client; streamable-http to expose it remotely",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.settings.host = args.host
        server.settings.port = args.port
        server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
