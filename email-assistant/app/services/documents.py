"""Running extraction over stored files and recording the result.

Extraction is deliberately a separate pass from sync.  Parsing a large PDF is
slow, and a sync that has to finish quickly should not wait for it; a blob
that failed to parse should also be retryable without re-fetching mail.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import Attachment, AttachmentBlob, AuditLog, DocumentText
from app.services.extraction import ExtractionStatus, extract
from app.services.storage import AttachmentStorage

log = get_logger(__name__)

# Statuses worth trying again: a transient failure, or OCR arriving later.
RETRYABLE = {ExtractionStatus.FAILED.value}


@dataclass
class ExtractionRunStats:
    considered: int = 0
    extracted: int = 0
    empty: int = 0
    needs_ocr: int = 0
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
            case ExtractionStatus.UNSUPPORTED:
                self.unsupported += 1
            case ExtractionStatus.FAILED:
                self.failed += 1


def pending_blobs(
    session: Session, limit: int | None = None, retry_failed: bool = False
) -> list[AttachmentBlob]:
    """Stored files with no usable extraction result yet."""
    already_done = exists().where(DocumentText.blob_id == AttachmentBlob.id)
    if retry_failed:
        already_done = exists().where(
            DocumentText.blob_id == AttachmentBlob.id,
            DocumentText.status.notin_(list(RETRYABLE)),
        )

    stmt = (
        select(AttachmentBlob)
        .where(~already_done)
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
    return _upsert(
        session,
        blob,
        status=result.status,
        method=result.method,
        text=result.text,
        page_count=result.page_count,
        truncated=result.truncated,
        error=result.error,
    )


def _upsert(
    session: Session,
    blob: AttachmentBlob,
    status: ExtractionStatus,
    method: str,
    text: str,
    page_count: int | None = None,
    truncated: bool = False,
    error: str | None = None,
) -> DocumentText:
    record = session.scalar(
        select(DocumentText).where(DocumentText.blob_id == blob.id)
    )
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
    record.extracted_at = datetime.now(UTC)
    session.flush()
    return record


def extract_pending(
    session: Session,
    storage: AttachmentStorage,
    limit: int | None = 100,
    retry_failed: bool = False,
) -> ExtractionRunStats:
    """Extract every stored file that has no result yet."""
    stats = ExtractionRunStats()
    blobs = pending_blobs(session, limit=limit, retry_failed=retry_failed)

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
                    f"{stats.needs_ocr} need OCR, {stats.unsupported} unsupported, "
                    f"{stats.failed} failed"
                ),
                details={
                    "considered": stats.considered,
                    "extracted": stats.extracted,
                    "needs_ocr": stats.needs_ocr,
                    "unsupported": stats.unsupported,
                    "failed": stats.failed,
                    "characters": stats.characters,
                },
                automatic=True,
            )
        )
        session.flush()
    return stats


def extraction_summary(session: Session) -> dict[str, int]:
    """Counts by status, plus how many stored files still have no result."""
    counts = dict(
        session.execute(
            select(DocumentText.status, func.count(DocumentText.id)).group_by(
                DocumentText.status
            )
        ).all()
    )
    summary = {status: int(counts.get(status, 0)) for status in (s.value for s in ExtractionStatus)}
    summary["pending"] = (
        session.scalar(
            select(func.count(AttachmentBlob.id)).where(
                ~exists().where(DocumentText.blob_id == AttachmentBlob.id)
            )
        )
        or 0
    )
    return summary


def get_document_text(session: Session, attachment_id: uuid.UUID) -> DocumentText | None:
    """Extracted text for an attachment, via its blob."""
    attachment = session.get(Attachment, attachment_id)
    if attachment is None or attachment.blob_id is None:
        return None
    return session.scalar(
        select(DocumentText).where(DocumentText.blob_id == attachment.blob_id)
    )
