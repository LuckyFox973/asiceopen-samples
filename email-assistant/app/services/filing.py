"""Deciding which of my own companies a document belongs to, and filing it.

An invoice is filed under the entity that was *billed*, not under whoever
sent it: Anthropic invoicing INFI belongs in INFI's folder.  So the text is
searched for the recipient company's own identifiers.

Deterministic throughout.  A registration number either appears in the
document or it does not, and no model is asked to guess between two of my own
companies — a misfiled invoice is worse than one that waits for an answer.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import (
    ActionStatus,
    ActionType,
    Attachment,
    AttachmentBlob,
    DocumentText,
    EmailMessage,
    FilingFolder,
    MailboxAccount,
    PendingAction,
)
from app.gmail.actions import ActionOutcome, GmailActions
from app.services.drive import DriveClient
from app.services.storage import AttachmentStorage

log = get_logger(__name__)

# A registration number is unambiguous; a company name can be a substring of
# another company's name.  Ranking by how the match was made, not just that
# one happened, is what makes a single confident answer possible.
IDENTIFIER = re.compile(r"^\d[\d\s/-]{5,}$")


@dataclass(frozen=True)
class FilingSuggestion:
    folder: FilingFolder
    matched: tuple[str, ...]
    confidence: float

    @property
    def certain(self) -> bool:
        return self.confidence >= 0.8


@dataclass(frozen=True)
class FilingOutcome:
    """What the resolver concluded, including when it concluded nothing."""

    suggestion: FilingSuggestion | None = None
    candidates: tuple[FilingSuggestion, ...] = ()
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.suggestion is not None


def fold(text: str) -> str:
    """Lower case, accents stripped, whitespace collapsed.

    The same folding on both sides, so "INFINITY FINANCE  s.r.o." in a PDF
    matches "Infinity Finance s.r.o." as it was typed into the registry.
    """
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", without_accents).strip()


def term_weight(term: str) -> float:
    """How much trust a match on this term earns.

    A registration number is a fact about one company.  A short name is not:
    "Fox" appears in four of these companies, so on its own it decides
    nothing.
    """
    folded = fold(term)
    if IDENTIFIER.match(folded):
        return 1.0
    if len(folded) >= 12:
        return 0.9
    if len(folded) >= 6:
        return 0.6
    return 0.25


def matches_in(text: str, folder: FilingFolder) -> tuple[tuple[str, ...], float]:
    """Which of a folder's terms occur in *text*, and how much they are worth."""
    haystack = fold(text)
    # Fold first, then test for emptiness: a term of only spaces folds to ""
    # and "" is in every string, so such a row would match every document
    # ever filed.
    found = tuple(
        term for term in folder.match_terms if term and fold(term) and fold(term) in haystack
    )
    if not found:
        return (), 0.0
    # The best single term decides; the rest only confirm, so that adding
    # three weak aliases cannot outweigh one registration number.
    best = max(term_weight(term) for term in found)
    confirming = min(0.1, 0.05 * (len(found) - 1))
    return found, min(1.0, best + confirming)


def resolve(session: Session, text: str) -> FilingOutcome:
    """Which folder this document belongs in, or why that is not clear."""
    if not text or not text.strip():
        return FilingOutcome(reason="the document has no text to match against")

    folders = list(session.scalars(select(FilingFolder).where(FilingFolder.is_active)).all())
    if not folders:
        return FilingOutcome(reason="no filing folders are registered")

    scored = []
    for folder in folders:
        matched, confidence = matches_in(text, folder)
        if matched:
            scored.append(FilingSuggestion(folder=folder, matched=matched, confidence=confidence))

    if not scored:
        return FilingOutcome(reason="no registered company was named in the document")

    scored.sort(key=lambda s: (-s.confidence, s.folder.name))
    best = scored[0]

    if len(scored) > 1 and abs(scored[1].confidence - best.confidence) < 0.15:
        # Two of my own companies both plausible: guessing files it wrong half
        # the time, which is worse than asking.
        return FilingOutcome(
            candidates=tuple(scored),
            reason=(
                f"{scored[0].folder.name} and {scored[1].folder.name} are both named; "
                "add a distinguishing term, or choose the folder explicitly"
            ),
        )
    if not best.certain:
        return FilingOutcome(
            candidates=tuple(scored),
            reason=f"only a weak match on {', '.join(best.matched)}",
        )
    return FilingOutcome(suggestion=best, candidates=tuple(scored))


def document_text_for(session: Session, attachment: Attachment) -> str:
    """The extracted text of an attachment, or "" when there is none."""
    if attachment.blob_id is None:
        return ""
    document = session.scalar(
        select(DocumentText).where(DocumentText.blob_id == attachment.blob_id)
    )
    return (document.text or "") if document else ""


def blob_for(session: Session, attachment: Attachment) -> AttachmentBlob | None:
    if attachment.blob_id is None:
        return None
    return session.get(AttachmentBlob, attachment.blob_id)


def register_folder(
    session: Session,
    name: str,
    drive_folder_id: str,
    match_terms: list[str],
    note: str | None = None,
) -> FilingFolder:
    """Add or update one company's folder."""
    folder = session.scalar(select(FilingFolder).where(FilingFolder.name == name))
    if folder is None:
        folder = FilingFolder(name=name, drive_folder_id=drive_folder_id)
        session.add(folder)
    folder.drive_folder_id = drive_folder_id
    folder.match_terms = [t.strip() for t in match_terms if t.strip()]
    folder.note = note
    folder.is_active = True
    session.flush()
    return folder


def list_folders(session: Session, include_inactive: bool = False) -> list[FilingFolder]:
    stmt = select(FilingFolder).order_by(FilingFolder.name)
    if not include_inactive:
        stmt = stmt.where(FilingFolder.is_active)
    return list(session.scalars(stmt).all())


# ---------------------------------------------------------------------------
# Filing a document, then archiving what carried it
# ---------------------------------------------------------------------------


@dataclass
class FiledResult:
    """What happened, in the order it happened."""

    attachment_id: uuid.UUID
    filename: str
    folder: FilingFolder | None = None
    upload: PendingAction | None = None
    archive: PendingAction | None = None
    skipped: str = ""

    @property
    def filed(self) -> bool:
        return self.upload is not None and self.upload.status == ActionStatus.EXECUTED.value


def file_attachment(
    session: Session,
    account: MailboxAccount,
    attachment: Attachment,
    storage: AttachmentStorage,
    drive: DriveClient,
    gmail: GmailActions | None = None,
    folder: FilingFolder | None = None,
    archive_after: bool = True,
    requested_by: str = "user",
    settings: Settings | None = None,
) -> FiledResult:
    """Put one attachment in its company's Drive folder, then archive the mail.

    The order is not an implementation detail: the document is safely
    somewhere else before the message leaves the inbox.  When the upload
    fails nothing is archived, and the mail is still where its owner expects
    to find it.
    """
    from app.services.actions import ActionRequest, approve, execute, propose

    settings = settings or get_settings()
    name = attachment.filename or "(unnamed)"
    blob = blob_for(session, attachment)
    if blob is None:
        return FiledResult(attachment.id, name, skipped="its bytes were never downloaded")

    if folder is None:
        outcome = resolve(session, document_text_for(session, attachment))
        if not outcome.resolved:
            return FiledResult(attachment.id, name, skipped=outcome.reason)
        assert outcome.suggestion is not None
        folder = outcome.suggestion.folder

    def upload(payload: dict) -> ActionOutcome:
        created = drive.upload_bytes(
            storage.get(blob.storage_key),
            folder_id=payload["drive_folder_id"],
            name=payload["filename"],
            mime_type=attachment.mime_type,
        )
        return ActionOutcome(
            ok=True,
            detail=f"filed as {created.name} in {payload['folder']}",
            data={"drive_file_id": created.id, "folder": payload["folder"]},
            undo_hint="Delete it from the Drive folder to undo.",
        )

    upload_action = _decide_and_run(
        session,
        account,
        ActionRequest(
            action_type=ActionType.DRIVE_UPLOAD,
            description=f"File {name} into {folder.name} on Drive",
            target_type="attachment",
            target_id=attachment.id,
            payload={
                "attachment_id": str(attachment.id),
                "filename": name,
                "folder": folder.name,
                "drive_folder_id": folder.drive_folder_id,
                "sha256": blob.sha256,
            },
            requested_by=requested_by,
        ),
        requested_by=requested_by,
        settings=settings,
        run=lambda action: execute(session, action, gmail=None, settings=settings, upload=upload),
        propose=propose,
        approve=approve,
    )

    result = FiledResult(attachment.id, name, folder=folder, upload=upload_action)
    if upload_action.status != ActionStatus.EXECUTED.value:
        result.skipped = upload_action.error or "the upload did not complete"
        return result
    if not archive_after:
        return result

    message = session.get(EmailMessage, attachment.message_id)
    if message is None or not message.gmail_message_id:
        result.skipped = "filed, but the message it came from is no longer stored"
        return result
    if gmail is None:
        result.skipped = "filed, but no Gmail client was supplied to archive with"
        return result

    result.archive = _decide_and_run(
        session,
        account,
        ActionRequest(
            action_type=ActionType.ARCHIVE,
            description=f"Archive the message carrying {name}",
            target_type="message",
            target_id=message.id,
            gmail_target_id=message.gmail_message_id,
            requested_by=requested_by,
        ),
        requested_by=requested_by,
        settings=settings,
        run=lambda action: execute(session, action, gmail=gmail, settings=settings),
        propose=propose,
        approve=approve,
    )
    return result


def _decide_and_run(
    session: Session,
    account: MailboxAccount,
    request,
    requested_by: str,
    settings: Settings,
    run,
    propose,
    approve,
) -> PendingAction:
    """Record the action, count the command as the yes, then carry it out.

    Running a command *is* the instruction — but it is still written down as
    a decision with a name against it, so the audit log answers "on whose
    say-so?" the same way for this as for anything else.  An action proposed
    by the agent rather than by a person is left waiting.
    """
    action = propose(session, account, request, settings=settings)
    if action.status == ActionStatus.PENDING.value and requested_by == "user":
        approve(session, action.id, decided_by=requested_by)
        session.refresh(action)
    if action.status not in {ActionStatus.APPROVED.value, ActionStatus.PENDING.value}:
        return action
    if action.status == ActionStatus.PENDING.value:
        # Proposed by the agent and not released by its setting: it waits.
        return action
    return run(action)


# ---------------------------------------------------------------------------
# Turning an invoice into a reminder
# ---------------------------------------------------------------------------


def task_for_attachment(
    session: Session,
    account: MailboxAccount,
    attachment: Attachment,
    tasks,
    title: str = "",
    requested_by: str = "user",
    settings: Settings | None = None,
) -> PendingAction:
    """A task carrying what the invoice actually says.

    "Pay Orange" is a reminder to go and look something up.  "Pay Orange
    2897510916 — 47.90 EUR, due 2026-09-15" is the answer, and the difference
    is only that the due date and the amount were read out of the document,
    which has already happened.
    """
    from app.services.actions import ActionRequest, approve, execute, propose
    from app.services.invoices import read_invoice

    settings = settings or get_settings()
    facts = read_invoice(document_text_for(session, attachment))
    sender = _sender_of(session, attachment)

    if not title:
        subject = sender or attachment.filename or "attachment"
        title = f"Pay {subject}"
        if facts.number:
            title += f" — invoice {facts.number}"
        if facts.amount:
            title += f", {facts.amount} {facts.currency or ''}".rstrip()

    notes = "\n".join(
        line
        for line in (
            f"File: {attachment.filename}" if attachment.filename else "",
            f"From: {sender}" if sender else "",
            facts.summary(),
            f"attachment={attachment.id}",
        )
        if line
    )

    def create(payload: dict) -> ActionOutcome:
        created = tasks.create(
            title=payload["title"],
            notes=payload.get("notes", ""),
            due=facts.due_date,
            list_id=tasks.resolve_list(settings.tasks_list),
        )
        when = f" due {facts.due_date.isoformat()}" if facts.due_date else ""
        return ActionOutcome(
            ok=True,
            detail=f"task created{when}",
            data={"task_id": created.id, "title": created.title},
            undo_hint="Delete it in Google Tasks to undo.",
        )

    return _decide_and_run(
        session,
        account,
        ActionRequest(
            action_type=ActionType.TASK_CREATE,
            description=title,
            target_type="attachment",
            target_id=attachment.id,
            payload={
                "title": title,
                "notes": notes,
                "due": facts.due_date.isoformat() if facts.due_date else None,
            },
            requested_by=requested_by,
        ),
        requested_by=requested_by,
        settings=settings,
        run=lambda action: execute(session, action, gmail=None, settings=settings, upload=create),
        propose=propose,
        approve=approve,
    )


def _sender_of(session: Session, attachment: Attachment) -> str:
    message = session.get(EmailMessage, attachment.message_id)
    if message is None:
        return ""
    return message.from_name or message.from_address or ""
