"""Persisting parsed Gmail messages.

Every operation here is idempotent: re-ingesting the same message must never
create a second row, and must never lose data that is already stored.  That
property is what lets the sync engine retry freely after any failure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import (
    Attachment,
    AttachmentBlob,
    Contact,
    DownloadStatus,
    EmailMessage,
    EmailParticipant,
    EmailThread,
    MailboxAccount,
)
from app.gmail.addresses import OwnedAddressSet, domain_of
from app.gmail.parser import ParsedAttachment, ParsedMessage, all_participants
from app.services.storage import AttachmentStorage, sha256_hex

log = get_logger(__name__)


@dataclass(slots=True)
class IngestResult:
    message_id: uuid.UUID
    thread_id: uuid.UUID
    created: bool
    updated: bool
    attachments_created: int = 0
    blobs_created: int = 0

    @property
    def unchanged(self) -> bool:
        return not self.created and not self.updated


class AttachmentFetcher:
    """What the ingestor needs in order to download attachment bytes."""

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:  # pragma: no cover
        raise NotImplementedError


class MessageIngestor:
    def __init__(
        self,
        session: Session,
        account: MailboxAccount,
        owned: OwnedAddressSet | None = None,
        storage: AttachmentStorage | None = None,
        fetcher: AttachmentFetcher | None = None,
        max_attachment_bytes: int = 25 * 1024 * 1024,
    ) -> None:
        self.session = session
        self.account = account
        self.owned = owned or OwnedAddressSet([a.address for a in account.addresses])
        self.storage = storage
        self.fetcher = fetcher
        self.max_attachment_bytes = max_attachment_bytes

    # --- public API ---------------------------------------------------------

    def ingest(self, parsed: ParsedMessage, download_attachments: bool = False) -> IngestResult:
        thread = self._upsert_thread(parsed)
        existing = self.session.scalar(
            select(EmailMessage).where(
                EmailMessage.account_id == self.account.id,
                EmailMessage.gmail_message_id == parsed.gmail_message_id,
            )
        )
        content_hash = parsed.content_hash()

        if existing is None:
            message = self._insert_message(parsed, thread, content_hash)
            created, updated = True, False
        elif existing.content_hash != content_hash:
            message = self._update_message(existing, parsed, content_hash)
            created, updated = False, True
        else:
            # Nothing changed; still refresh the thread aggregates cheaply in
            # case a sibling message moved.
            self._refresh_thread(thread)
            return IngestResult(existing.id, thread.id, created=False, updated=False)

        self._sync_participants(message, parsed)
        attachments_created, blobs_created = self._sync_attachments(
            message, parsed, download=download_attachments
        )
        self._touch_contacts(parsed)
        self._refresh_thread(thread)

        return IngestResult(
            message_id=message.id,
            thread_id=thread.id,
            created=created,
            updated=updated,
            attachments_created=attachments_created,
            blobs_created=blobs_created,
        )

    # --- threads ------------------------------------------------------------

    def _upsert_thread(self, parsed: ParsedMessage) -> EmailThread:
        thread = self.session.scalar(
            select(EmailThread).where(
                EmailThread.account_id == self.account.id,
                EmailThread.gmail_thread_id == parsed.gmail_thread_id,
            )
        )
        if thread is None:
            thread = EmailThread(
                account_id=self.account.id,
                gmail_thread_id=parsed.gmail_thread_id,
                subject=parsed.subject,
                snippet=parsed.snippet,
            )
            self.session.add(thread)
            self.session.flush()
        return thread

    def _refresh_thread(self, thread: EmailThread) -> None:
        """Recompute thread aggregates from its messages."""
        row = self.session.execute(
            select(
                func.count(EmailMessage.id),
                func.min(func.coalesce(EmailMessage.internal_date, EmailMessage.sent_at)),
                func.max(func.coalesce(EmailMessage.internal_date, EmailMessage.sent_at)),
            ).where(EmailMessage.thread_id == thread.id)
        ).one()
        thread.message_count = row[0] or 0
        thread.first_message_at = row[1]
        thread.last_message_at = row[2]

        last = self.session.scalar(
            select(EmailMessage)
            .where(EmailMessage.thread_id == thread.id)
            .order_by(
                func.coalesce(EmailMessage.internal_date, EmailMessage.sent_at).desc(),
                EmailMessage.gmail_message_id.desc(),
            )
            .limit(1)
        )
        if last is not None:
            thread.last_message_direction = last.direction
            thread.snippet = last.snippet
            # Thread subject follows the *first* message; a "Re:" chain should
            # not rename the whole conversation.
            if not thread.subject:
                thread.subject = last.subject
        self.session.flush()

    # --- messages -----------------------------------------------------------

    def _insert_message(
        self, parsed: ParsedMessage, thread: EmailThread, content_hash: str
    ) -> EmailMessage:
        message = EmailMessage(
            account_id=self.account.id,
            thread_id=thread.id,
            gmail_message_id=parsed.gmail_message_id,
            gmail_thread_id=parsed.gmail_thread_id,
            history_id=parsed.history_id,
            rfc822_message_id=parsed.rfc822_message_id,
            in_reply_to=parsed.in_reply_to,
            references=parsed.references or None,
            subject=parsed.subject,
            from_address=parsed.from_address,
            from_name=parsed.from_name,
            account_address=parsed.account_address,
            direction=parsed.direction,
            sent_at=parsed.sent_at,
            internal_date=parsed.internal_date,
            body_text=parsed.body_text,
            body_html=parsed.body_html,
            snippet=parsed.snippet,
            labels=parsed.labels or None,
            size_estimate=parsed.size_estimate,
            has_attachments=bool(parsed.attachments),
            raw_headers=parsed.raw_headers or None,
            content_hash=content_hash,
        )
        self.session.add(message)
        self.session.flush()
        return message

    def _update_message(
        self, message: EmailMessage, parsed: ParsedMessage, content_hash: str
    ) -> EmailMessage:
        message.history_id = parsed.history_id or message.history_id
        message.subject = parsed.subject
        message.from_address = parsed.from_address
        message.from_name = parsed.from_name
        message.account_address = parsed.account_address
        message.direction = parsed.direction
        message.sent_at = parsed.sent_at
        message.internal_date = parsed.internal_date
        message.body_text = parsed.body_text
        message.body_html = parsed.body_html
        message.snippet = parsed.snippet
        message.labels = parsed.labels or None
        message.size_estimate = parsed.size_estimate
        message.has_attachments = bool(parsed.attachments)
        message.raw_headers = parsed.raw_headers or None
        message.content_hash = content_hash
        self.session.flush()
        return message

    # --- participants -------------------------------------------------------

    def _sync_participants(self, message: EmailMessage, parsed: ParsedMessage) -> None:
        """Replace the participant rows for this message.

        Replacing rather than merging keeps the rows an exact mirror of the
        headers even when a message is re-fetched after an edit upstream.
        """
        self.session.query(EmailParticipant).filter(
            EmailParticipant.message_id == message.id
        ).delete(synchronize_session=False)

        for kind, name, address, position in all_participants(parsed):
            self.session.add(
                EmailParticipant(
                    message_id=message.id,
                    kind=kind,
                    address=address,
                    display_name=name or None,
                    position=position,
                    is_own=address in self.owned,
                )
            )
        self.session.flush()

    # --- contacts -----------------------------------------------------------

    def _touch_contacts(self, parsed: ParsedMessage) -> None:
        """Record every address seen, with first/last-seen and a message count."""
        seen_at = parsed.internal_date or parsed.sent_at or datetime.now(UTC)
        addresses = {address for _, _, address, _ in all_participants(parsed)}

        for address in addresses:
            if not address or "@" not in address:
                continue
            display_name = self._best_name_for(parsed, address)
            stmt = (
                pg_insert(Contact)
                .values(
                    id=uuid.uuid4(),
                    primary_address=address,
                    display_name=display_name,
                    domain=domain_of(address),
                    is_own=address in self.owned,
                    first_seen_at=seen_at,
                    last_seen_at=seen_at,
                    message_count=1,
                )
                .on_conflict_do_update(
                    index_elements=[Contact.primary_address],
                    set_={
                        "last_seen_at": func.greatest(Contact.last_seen_at, seen_at),
                        "first_seen_at": func.least(Contact.first_seen_at, seen_at),
                        "message_count": Contact.message_count + 1,
                        # Only fill a name in; never overwrite a curated one.
                        "display_name": func.coalesce(Contact.display_name, display_name),
                        "updated_at": func.now(),
                    },
                )
            )
            self.session.execute(stmt)
        self.session.flush()

    @staticmethod
    def _best_name_for(parsed: ParsedMessage, address: str) -> str | None:
        for _, name, candidate, _ in all_participants(parsed):
            if candidate == address and name:
                return name[:255]
        return None

    # --- attachments --------------------------------------------------------

    def _sync_attachments(
        self, message: EmailMessage, parsed: ParsedMessage, download: bool
    ) -> tuple[int, int]:
        created = 0
        blobs_created = 0

        existing_by_part = {
            attachment.part_id: attachment
            for attachment in self.session.scalars(
                select(Attachment).where(Attachment.message_id == message.id)
            )
        }

        for part in parsed.attachments:
            attachment = existing_by_part.get(part.part_id)
            if attachment is None:
                attachment = Attachment(
                    account_id=self.account.id,
                    message_id=message.id,
                    part_id=part.part_id,
                    filename=part.filename,
                    mime_type=part.mime_type,
                    size_bytes=part.size_bytes,
                    gmail_attachment_id=part.gmail_attachment_id,
                    content_id=part.content_id,
                    is_inline=part.is_inline,
                    download_status=DownloadStatus.PENDING.value,
                )
                self.session.add(attachment)
                self.session.flush()
                created += 1
            else:
                # Gmail's attachmentId rotates; keep the freshest one so a
                # later download attempt still works.
                attachment.gmail_attachment_id = (
                    part.gmail_attachment_id or attachment.gmail_attachment_id
                )

            if download and attachment.blob_id is None:
                blobs_created += self._store_blob(attachment, part, parsed)

        self.session.flush()
        return created, blobs_created

    def _store_blob(
        self, attachment: Attachment, part: ParsedAttachment, parsed: ParsedMessage
    ) -> int:
        """Download and store the bytes. Returns 1 if a *new* blob was created."""
        if self.storage is None:
            return 0

        size = part.size_bytes or 0
        if size > self.max_attachment_bytes:
            attachment.download_status = DownloadStatus.SKIPPED_TOO_LARGE.value
            return 0

        try:
            data = part.inline_data
            if data is None:
                if self.fetcher is None or not part.gmail_attachment_id:
                    return 0
                data = self.fetcher.get_attachment(
                    parsed.gmail_message_id, part.gmail_attachment_id
                )
        except Exception as exc:  # noqa: BLE001 - one bad attachment must not stop a sync
            log.warning(
                "attachment.download_failed",
                message_id=parsed.gmail_message_id,
                part_id=part.part_id,
                error=str(exc),
            )
            attachment.download_status = DownloadStatus.FAILED.value
            attachment.download_error = str(exc)[:1000]
            return 0

        if len(data) > self.max_attachment_bytes:
            attachment.download_status = DownloadStatus.SKIPPED_TOO_LARGE.value
            return 0

        digest = sha256_hex(data)
        blob = self.session.scalar(select(AttachmentBlob).where(AttachmentBlob.sha256 == digest))
        is_new = blob is None
        if blob is None:
            key = self.storage.put(data, digest, part.mime_type)
            blob = AttachmentBlob(
                sha256=digest,
                size_bytes=len(data),
                mime_type=part.mime_type,
                storage_backend=self.storage.backend,
                storage_key=key,
                first_seen_at=parsed.internal_date or datetime.now(UTC),
            )
            self.session.add(blob)
            self.session.flush()

        attachment.blob_id = blob.id
        attachment.size_bytes = attachment.size_bytes or len(data)
        attachment.download_status = DownloadStatus.DOWNLOADED.value
        attachment.download_error = None
        return 1 if is_new else 0
