"""Tracking successive versions of the same document.

Deduplication is by content hash, which is right: identical bytes are one
stored file.  But it means a *revised* Word document is a different blob —
correctly parsed on its own, and easy to miss as a new version of something
already on file.

This module answers two questions a lawyer actually asks:

* Which versions of this document do we have, and when did each arrive?
* What changed between two of them?
"""

from __future__ import annotations

import difflib
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Attachment, AttachmentBlob, DocumentText, EmailMessage

# Suffixes people add when they save a revised copy.  Stripped so
# "Zmluva_v2.docx" and "Zmluva (final).docx" belong to the same family.
VERSION_NOISE = re.compile(
    r"[\s_\-]*(\(|\[)?\s*"
    r"(v\.?\s*\d+|verzia\s*\d+|ver\s*\d+|\d{1,2}|final|finalna|finálna|cistopis|"
    r"čistopis|draft|navrh|návrh|rev\.?\s*\d*|kopia|kópia|copy|"
    r"upraven[éae]|zmeny|posledn[áya])\s*(\)|\])?",
    re.IGNORECASE,
)
DATE_NOISE = re.compile(r"[\s_\-]*\d{2,4}[-_.]\d{1,2}[-_.]\d{1,4}")
SEPARATORS = re.compile(r"[\s_\-]+")


def normalise_filename(filename: str | None) -> str:
    """A family key: the document's identity without version decoration."""
    if not filename:
        return ""
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    stem = DATE_NOISE.sub(" ", stem)
    # Applied repeatedly: "Zmluva_v2_final" carries two markers.
    for _ in range(3):
        reduced = VERSION_NOISE.sub(" ", stem)
        if reduced == stem:
            break
        stem = reduced
    return SEPARATORS.sub(" ", stem).strip().lower()


@dataclass(slots=True)
class DocumentVersion:
    attachment_id: uuid.UUID
    message_id: uuid.UUID
    blob_id: uuid.UUID | None
    filename: str | None
    sha256: str | None
    received_at: datetime | None
    char_count: int
    revision_count: int
    revision_summary: str | None

    @property
    def has_revisions(self) -> bool:
        return self.revision_count > 0


@dataclass
class VersionHistory:
    family: str
    versions: list[DocumentVersion] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.versions)

    @property
    def has_multiple(self) -> bool:
        return len({v.sha256 for v in self.versions if v.sha256}) > 1


@dataclass(slots=True)
class VersionDiff:
    added_lines: list[str]
    removed_lines: list[str]
    unified: str
    similarity: float

    @property
    def is_identical(self) -> bool:
        return not self.added_lines and not self.removed_lines

    def summary(self) -> str:
        if self.is_identical:
            return "No textual difference."
        return (
            f"{len(self.added_lines)} line(s) added, "
            f"{len(self.removed_lines)} removed "
            f"({self.similarity:.0%} similar)"
        )


def version_history(
    session: Session, attachment_id: uuid.UUID, account_scoped: bool = True
) -> VersionHistory:
    """Every attachment whose file name belongs to the same document family."""
    attachment = session.get(Attachment, attachment_id)
    if attachment is None:
        return VersionHistory(family="")

    family = normalise_filename(attachment.filename)
    if not family:
        return VersionHistory(family="")

    stmt = (
        select(Attachment, AttachmentBlob, DocumentText, EmailMessage)
        .outerjoin(AttachmentBlob, Attachment.blob_id == AttachmentBlob.id)
        .outerjoin(DocumentText, DocumentText.blob_id == AttachmentBlob.id)
        .join(EmailMessage, Attachment.message_id == EmailMessage.id)
        .where(Attachment.filename.isnot(None))
    )
    if account_scoped:
        stmt = stmt.where(Attachment.account_id == attachment.account_id)

    versions: list[DocumentVersion] = []
    for other, blob, document, message in session.execute(stmt).all():
        if normalise_filename(other.filename) != family:
            continue
        versions.append(
            DocumentVersion(
                attachment_id=other.id,
                message_id=other.message_id,
                blob_id=other.blob_id,
                filename=other.filename,
                sha256=blob.sha256 if blob else None,
                received_at=message.internal_date or message.sent_at,
                char_count=document.char_count if document else 0,
                revision_count=document.revision_count if document else 0,
                revision_summary=document.revision_summary if document else None,
            )
        )

    versions.sort(key=lambda v: (v.received_at is None, v.received_at))
    return VersionHistory(family=family, versions=versions)


def diff_versions(
    session: Session, older_attachment_id: uuid.UUID, newer_attachment_id: uuid.UUID
) -> VersionDiff | None:
    """What changed between the extracted text of two versions."""
    older = _text_for(session, older_attachment_id)
    newer = _text_for(session, newer_attachment_id)
    if older is None or newer is None:
        return None

    old_lines = older.splitlines()
    new_lines = newer.splitlines()

    added = [
        line[2:]
        for line in difflib.ndiff(old_lines, new_lines)
        if line.startswith("+ ") and line[2:].strip()
    ]
    removed = [
        line[2:]
        for line in difflib.ndiff(old_lines, new_lines)
        if line.startswith("- ") and line[2:].strip()
    ]
    unified = "\n".join(
        difflib.unified_diff(
            old_lines, new_lines, fromfile="previous", tofile="current", lineterm="", n=1
        )
    )
    similarity = difflib.SequenceMatcher(None, older, newer).ratio()
    return VersionDiff(
        added_lines=added, removed_lines=removed, unified=unified, similarity=similarity
    )


def _text_for(session: Session, attachment_id: uuid.UUID) -> str | None:
    row = session.execute(
        select(DocumentText.text)
        .join(Attachment, Attachment.blob_id == DocumentText.blob_id)
        .where(Attachment.id == attachment_id)
        .limit(1)
    ).first()
    return row[0] if row and row[0] is not None else None


def documents_with_revisions(session: Session, limit: int = 50) -> list[DocumentText]:
    """Files carrying tracked changes or comments — worth a person's eye."""
    return list(
        session.scalars(
            select(DocumentText)
            .where(DocumentText.revision_count > 0)
            .order_by(DocumentText.extracted_at.desc().nullslast())
            .limit(limit)
        ).all()
    )


def families_with_multiple_versions(
    session: Session, account_id: uuid.UUID | None = None, limit: int = 50
) -> list[tuple[str, int]]:
    """Document families seen with more than one distinct content hash."""
    stmt = select(Attachment.filename, AttachmentBlob.sha256).join(
        AttachmentBlob, Attachment.blob_id == AttachmentBlob.id
    )
    if account_id is not None:
        stmt = stmt.where(Attachment.account_id == account_id)

    seen: dict[str, set[str]] = {}
    for filename, sha in session.execute(stmt).all():
        family = normalise_filename(filename)
        if family:
            seen.setdefault(family, set()).add(sha)

    families = [(family, len(hashes)) for family, hashes in seen.items() if len(hashes) > 1]
    families.sort(key=lambda item: item[1], reverse=True)
    return families[:limit]


def latest_version(session: Session, attachment_id: uuid.UUID) -> DocumentVersion | None:
    history = version_history(session, attachment_id)
    return history.versions[-1] if history.versions else None


def count_versions(session: Session, filename: str | None) -> int:
    """How many distinct contents this document family has been seen with."""
    family = normalise_filename(filename)
    if not family:
        return 0
    rows = session.execute(
        select(Attachment.filename, AttachmentBlob.sha256).join(
            AttachmentBlob, Attachment.blob_id == AttachmentBlob.id
        )
    ).all()
    return len({sha for name, sha in rows if normalise_filename(name) == family})
