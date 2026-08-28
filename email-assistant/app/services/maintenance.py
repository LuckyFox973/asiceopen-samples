"""Housekeeping that keeps stored personal data no larger than it must be."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import AttachmentBlob, AuditLog, Contact, EmailParticipant

log = get_logger(__name__)


def prune_orphan_contacts(session: Session, dry_run: bool = False) -> int:
    """Remove contacts no message refers to any more.

    Contacts are deliberately global — one person may write to several of your
    mailboxes — so they are not cascade-deleted with a mailbox.  That leaves
    personal data behind when a mailbox is removed, which this reclaims.
    """
    orphan_ids = list(
        session.scalars(
            select(Contact.id).where(
                ~exists().where(EmailParticipant.contact_id == Contact.id),
                ~exists().where(EmailParticipant.address == Contact.primary_address),
            )
        ).all()
    )
    if not orphan_ids or dry_run:
        return len(orphan_ids)

    session.execute(delete(Contact).where(Contact.id.in_(orphan_ids)))
    session.add(
        AuditLog(
            occurred_at=datetime.now(UTC),
            actor="system",
            action="maintenance.prune_contacts",
            entity_type="contact",
            summary=f"Removed {len(orphan_ids)} contacts no message refers to",
            details={"count": len(orphan_ids)},
            automatic=True,
        )
    )
    session.flush()
    return len(orphan_ids)


def find_unreferenced_blobs(session: Session) -> list[AttachmentBlob]:
    """Blobs no attachment row points at any more.

    Deliberately *reported*, not deleted: erasing a stored document is a GDPR
    decision, so it is surfaced for a person to act on rather than performed
    as a side effect of housekeeping.
    """
    from app.db.models import Attachment

    return list(
        session.scalars(
            select(AttachmentBlob).where(~exists().where(Attachment.blob_id == AttachmentBlob.id))
        ).all()
    )
