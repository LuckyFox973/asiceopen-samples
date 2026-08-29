"""Running extraction over stored files and recording the result.

Extraction is deliberately a separate pass from sync.  Parsing a large PDF is
slow, and a sync that has to finish quickly should not wait for it; a blob
that failed to parse should also be retryable without re-fetching mail.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Attachment, AttachmentBlob, AuditLog, DocumentText
from app.services.extraction import ExtractionStatus, extract
from app.services.storage import AttachmentStorage
from app.services.versions import normalise_filename

log = get_logger(__name__)

# Statuses that mean the file's contents are not searchable.
UNREADABLE = (
    ExtractionStatus.NEEDS_OCR.value,
    ExtractionStatus.ENCRYPTED.value,
    ExtractionStatus.UNSUPPORTED.value,
    ExtractionStatus.FAILED.value,
)

# Statuses worth reading again on request.  Everything that produced no text
# qualifies, not merely what crashed: "unsupported" is a statement about the
# extractor of the day, and the day a format is added every file previously
# refused for it is readable.  `empty` is excluded — that file was read fine
# and genuinely has nothing in it.
RETRYABLE = {*UNREADABLE, ExtractionStatus.NOT_A_DOCUMENT.value}


@dataclass
class ExtractionRunStats:
    considered: int = 0
    extracted: int = 0
    empty: int = 0
    needs_ocr: int = 0
    encrypted: int = 0
    not_a_document: int = 0
    unsupported: int = 0
    failed: int = 0
    characters: int = 0
    errors: list[str] = field(default_factory=list)

    def record(self, status: ExtractionStatus, chars: int = 0) -> None:
        self.considered += 1
        self.characters += chars
        match status:
            case ExtractionStatus.EXTRACTED:
                self.extracted += 1
            case ExtractionStatus.EMPTY:
                self.empty += 1
            case ExtractionStatus.NEEDS_OCR:
                self.needs_ocr += 1
            case ExtractionStatus.ENCRYPTED:
                self.encrypted += 1
            case ExtractionStatus.NOT_A_DOCUMENT:
                self.not_a_document += 1
            case ExtractionStatus.UNSUPPORTED:
                self.unsupported += 1
            case ExtractionStatus.FAILED:
                self.failed += 1


def _already_done(retry_failed: bool, since: datetime | None = None):
    """The single definition of "this blob has been dealt with".

    Shared deliberately: when the count and the query disagreed about it, the
    command reported nothing to do and exited before running.

    *since* is what lets a retry run finish.  A file re-read and classified as
    a scan is still `needs_ocr`, which is itself retryable — so on status
    alone it stays outstanding for ever and the batch loop never converges.
    Having been looked at during this run is what counts as dealt with,
    whatever status it landed on.
    """
    if not retry_failed:
        return exists().where(DocumentText.blob_id == AttachmentBlob.id)

    settled = DocumentText.status.notin_(sorted(RETRYABLE))
    if since is not None:
        settled = or_(settled, DocumentText.extracted_at >= since)
    return exists().where(DocumentText.blob_id == AttachmentBlob.id, settled)


def pending_blobs(
    session: Session,
    limit: int | None = None,
    retry_failed: bool = False,
    since: datetime | None = None,
) -> list[AttachmentBlob]:
    """Stored files with no usable extraction result yet."""
    stmt = (
        select(AttachmentBlob)
        .where(~_already_done(retry_failed, since))
        .order_by(AttachmentBlob.first_seen_at.desc().nullslast())
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())


def describe_blob(session: Session, blob: AttachmentBlob) -> tuple[str | None, str | None]:
    """The most useful filename and MIME type seen for this content.

    The blob itself is just bytes; the names live on the attachment rows, and
    a correct filename materially improves format detection.
    """
    row = session.execute(
        select(Attachment.filename, Attachment.mime_type)
        .where(Attachment.blob_id == blob.id)
        .order_by(Attachment.created_at.asc())
        .limit(1)
    ).first()
    if row is None:
        return None, blob.mime_type
    filename, mime_type = row
    return filename, mime_type or blob.mime_type


def extract_blob(
    session: Session,
    blob: AttachmentBlob,
    storage: AttachmentStorage,
) -> DocumentText:
    """Extract one blob and upsert its :class:`DocumentText` row."""
    filename, mime_type = describe_blob(session, blob)

    try:
        data = storage.get(blob.storage_key)
    except Exception as exc:  # noqa: BLE001 - a missing file is a result, not a crash
        result_status = ExtractionStatus.FAILED
        record = _upsert(
            session,
            blob,
            status=result_status,
            method="storage",
            text="",
            error=f"Could not read stored file: {exc}"[:500],
        )
        log.warning("extraction.unreadable", blob=str(blob.id), error=str(exc))
        return record

    result = extract(data, mime_type=mime_type, filename=filename)
    record = _upsert(
        session,
        blob,
        status=result.status,
        method=result.method,
        text=result.text,
        page_count=result.page_count,
        truncated=result.truncated,
        error=result.error,
        deleted_text=result.deleted_text,
        comment_text=result.comment_text,
        revision_count=result.revision_count,
        revision_authors=result.revision_authors,
        revision_summary=result.revision_summary,
    )
    if result.revision_summary:
        log.info(
            "extraction.revisions_found",
            filename=filename,
            summary=result.revision_summary,
        )
    _note_new_version(session, blob, filename)
    return record


def _upsert(
    session: Session,
    blob: AttachmentBlob,
    status: ExtractionStatus,
    method: str,
    text: str,
    page_count: int | None = None,
    truncated: bool = False,
    error: str | None = None,
    deleted_text: str = "",
    comment_text: str = "",
    revision_count: int = 0,
    revision_authors: list[str] | None = None,
    revision_summary: str | None = None,
) -> DocumentText:
    record = session.scalar(select(DocumentText).where(DocumentText.blob_id == blob.id))
    if record is None:
        record = DocumentText(blob_id=blob.id)
        session.add(record)

    record.status = status.value
    record.method = method or None
    record.text = text or None
    record.char_count = len(text or "")
    record.page_count = page_count
    record.truncated = truncated
    record.error = error
    record.deleted_text = deleted_text or None
    record.comment_text = comment_text or None
    record.revision_count = revision_count
    record.revision_authors = revision_authors or None
    record.revision_summary = revision_summary
    record.extracted_at = datetime.now(UTC)
    session.flush()
    return record


def _note_new_version(session: Session, blob: AttachmentBlob, filename: str | None) -> None:
    """Record in the audit log that a known file arrived with new content.

    Deduplication is by content, so a revised Word document is a *different*
    blob — correctly parsed on its own, but easy to miss as a new version of
    something already on file.  This is the signal that says: this changed.
    """
    if not filename:
        return

    family = normalise_filename(filename)
    if not family:
        return

    # Narrow in SQL on the first significant word, then compare families
    # exactly in Python — the normalisation strips version noise that no
    # LIKE pattern could express.
    anchor = family.split(" ", 1)[0]
    if len(anchor) < 3:
        return

    earlier = None
    candidates = session.execute(
        select(Attachment.filename, AttachmentBlob.sha256)
        .join(AttachmentBlob, Attachment.blob_id == AttachmentBlob.id)
        .where(
            func.lower(Attachment.filename).like(f"%{anchor}%"),
            AttachmentBlob.id != blob.id,
        )
        .limit(200)
    ).all()
    for candidate_name, candidate_sha in candidates:
        if normalise_filename(candidate_name) == family and candidate_sha != blob.sha256:
            earlier = candidate_sha
            break
    if earlier is None:
        return

    session.add(
        AuditLog(
            occurred_at=datetime.now(UTC),
            actor="system",
            action="documents.new_version",
            entity_type="attachment_blob",
            entity_id=str(blob.id),
            summary=(
                f"{filename!r} arrived with different content — a previous "
                "version is already on file"
            ),
            details={
                "filename": filename,
                "sha256": blob.sha256,
                "previous_sha256": earlier,
            },
            automatic=True,
        )
    )
    session.flush()


def extract_pending(
    session: Session,
    storage: AttachmentStorage,
    limit: int | None = 100,
    retry_failed: bool = False,
    since: datetime | None = None,
) -> ExtractionRunStats:
    """Extract every stored file that has no result yet."""
    stats = ExtractionRunStats()
    blobs = pending_blobs(session, limit=limit, retry_failed=retry_failed, since=since)

    for blob in blobs:
        # A savepoint per file: one unparseable document rolls back alone.
        savepoint = session.begin_nested()
        try:
            record = extract_blob(session, blob, storage)
            savepoint.commit()
        except Exception as exc:  # noqa: BLE001
            savepoint.rollback()
            stats.failed += 1
            stats.considered += 1
            stats.errors.append(f"{blob.sha256[:12]}: {exc}")
            log.warning("extraction.blob_failed", blob=str(blob.id), error=str(exc))
            continue
        stats.record(ExtractionStatus(record.status), record.char_count)

    if stats.considered:
        session.add(
            AuditLog(
                occurred_at=datetime.now(UTC),
                actor="system",
                action="documents.extracted",
                entity_type="document_text",
                summary=(
                    f"Extracted {stats.extracted} document(s); "
                    f"{stats.needs_ocr} need OCR, {stats.encrypted} password protected, "
                    f"{stats.unsupported} unsupported, "
                    f"{stats.failed} failed"
                ),
                details={
                    "considered": stats.considered,
                    "extracted": stats.extracted,
                    "needs_ocr": stats.needs_ocr,
                    "encrypted": stats.encrypted,
                    "unsupported": stats.unsupported,
                    "failed": stats.failed,
                    "characters": stats.characters,
                },
                automatic=True,
            )
        )
        session.flush()
    return stats


def extraction_summary(
    session: Session, retry_failed: bool = False, since: datetime | None = None
) -> dict[str, int]:
    """Counts by status, plus how many stored files are still to be read.

    *retry_failed* must match what the caller intends to run.  A count of
    files with no row at all would say "nothing to do" to a caller that was
    about to re-read every unsupported file — which is exactly how a rebuilt
    extractor silently did nothing.
    """
    counts = dict(
        session.execute(
            select(DocumentText.status, func.count(DocumentText.id)).group_by(DocumentText.status)
        ).all()
    )
    summary = {status: int(counts.get(status, 0)) for status in (s.value for s in ExtractionStatus)}
    summary["pending"] = (
        session.scalar(
            select(func.count(AttachmentBlob.id)).where(~_already_done(retry_failed, since))
        )
        or 0
    )
    return summary


@dataclass
class UnreadableGroup:
    """Files sharing an extension that the extractor could not turn into text."""

    status: str
    extension: str
    mime_type: str | None
    files: int
    copies: int
    bytes_total: int
    example: str | None
    error: str | None


def unreadable_documents(session: Session) -> list[UnreadableGroup]:
    """What could not be read, grouped by extension, commonest first.

    Deciding whether OCR or another format is worth building is a question
    about which files were skipped, not how many — sixteen signature images
    and sixteen court filings are the same number and not the same problem.
    """
    rows = session.execute(
        select(
            DocumentText.status,
            DocumentText.error,
            AttachmentBlob.id,
            AttachmentBlob.mime_type,
            AttachmentBlob.size_bytes,
        )
        .join(AttachmentBlob, AttachmentBlob.id == DocumentText.blob_id)
        .where(DocumentText.status.in_(UNREADABLE))
    ).all()

    grouped: dict[tuple[str, str], dict] = {}
    for status, error, blob_id, mime_type, size_bytes in rows:
        names = session.execute(
            select(Attachment.filename, func.count(Attachment.id))
            .where(Attachment.blob_id == blob_id)
            .group_by(Attachment.filename)
            .order_by(func.count(Attachment.id).desc())
        ).all()
        filename = names[0][0] if names else None
        copies = sum(count for _name, count in names) or 1
        extension = Path(filename).suffix.lower() if filename else ""

        entry = grouped.setdefault(
            (status, extension or "(no extension)"),
            {
                "status": status,
                "extension": extension or "(no extension)",
                "mime_type": mime_type,
                "files": 0,
                "copies": 0,
                "bytes_total": 0,
                "example": filename,
                "error": error,
            },
        )
        entry["files"] += 1
        entry["copies"] += copies
        entry["bytes_total"] += size_bytes or 0
        entry["error"] = entry["error"] or error

    return sorted(
        (UnreadableGroup(**entry) for entry in grouped.values()),
        key=lambda group: (-group.copies, group.extension),
    )


def get_document_text(session: Session, attachment_id: uuid.UUID) -> DocumentText | None:
    """Extracted text for an attachment, via its blob."""
    attachment = session.get(Attachment, attachment_id)
    if attachment is None or attachment.blob_id is None:
        return None
    return session.scalar(select(DocumentText).where(DocumentText.blob_id == attachment.blob_id))
