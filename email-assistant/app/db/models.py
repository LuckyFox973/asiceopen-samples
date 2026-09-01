"""Domain model for the Email AI Assistant (MVP 1 core).

Only tables that MVP 1 actually reads or writes live here.  The full target
model — clients, matters, tasks, follow-ups, semantic memory, pending actions,
AI usage — is specified in ``docs/DATA_MODEL.md`` and lands in later phases,
so no dead schema is created up front.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

# ---------------------------------------------------------------------------
# Enumerations (stored as short strings — readable in SQL, cheap to extend)
# ---------------------------------------------------------------------------


class Direction(enum.StrEnum):
    INBOUND = "inbound"  # someone wrote to me
    OUTBOUND = "outbound"  # I wrote to someone
    INTERNAL = "internal"  # from one of my addresses to another of mine
    UNKNOWN = "unknown"


class ParticipantKind(enum.StrEnum):
    FROM = "from"
    TO = "to"
    CC = "cc"
    BCC = "bcc"
    REPLY_TO = "reply_to"
    DELIVERED_TO = "delivered_to"


class SyncKind(enum.StrEnum):
    INITIAL = "initial"
    INCREMENTAL = "incremental"
    BACKFILL = "backfill"


class SyncStatus(enum.StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class DownloadStatus(enum.StrEnum):
    PENDING = "pending"
    DOWNLOADED = "downloaded"
    SKIPPED_TOO_LARGE = "skipped_too_large"
    FAILED = "failed"


class AuditResult(enum.StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"


# The full-text expression, kept verbatim in migration 0001.  Subject weighs
# most, then the sender, then the body.
SEARCH_VECTOR_EXPRESSION = (
    "setweight(to_tsvector('public.sk_unaccent', coalesce(subject, '')), 'A') "
    "|| setweight(to_tsvector('public.sk_unaccent', coalesce(from_address, '')), 'B') "
    "|| setweight(to_tsvector('public.sk_unaccent', coalesce(body_text, '')), 'C')"
)


# Document text carries no subject or sender, so it is a single unweighted
# field.  Margin comments are indexed with the body — in a negotiation they
# often carry the substance.  Text struck out by a tracked change is not: a
# figure someone removed must never surface as if the document still said it.
# Kept verbatim in the migration that last changed it.
DOCUMENT_SEARCH_VECTOR_EXPRESSION = (
    "to_tsvector('public.sk_unaccent', coalesce(text, '') || ' ' || coalesce(comment_text, ''))"
)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


# ---------------------------------------------------------------------------
# Mailbox / identity
# ---------------------------------------------------------------------------


class MailboxAccount(Base, TimestampMixin):
    """One authorised Gmail mailbox.

    A Workspace user with several aliases is a single account with several
    :class:`MailboxAddress` rows.  Several *users* in the same Workspace are
    several accounts, each with its own OAuth grant.
    """

    __tablename__ = "mailbox_account"

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String(255))
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)

    # OAuth material — encrypted at rest with TOKEN_ENCRYPTION_KEY.
    oauth_refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    oauth_access_token_enc: Mapped[str | None] = mapped_column(Text)
    oauth_token_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    oauth_scopes: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    authorised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Per-account override of the global SYNC_START_DATE.
    sync_start_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    addresses: Mapped[list[MailboxAddress]] = relationship(
        back_populates="account", cascade="all, delete-orphan", lazy="selectin"
    )
    sync_state: Mapped[SyncState | None] = relationship(
        back_populates="account", cascade="all, delete-orphan", uselist=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MailboxAccount {self.email}>"


class MailboxAddress(Base, TimestampMixin):
    """An address that belongs to me — used to tell my mail from theirs."""

    __tablename__ = "mailbox_address"
    __table_args__ = (
        UniqueConstraint("account_id", "address", name="uq_mailbox_address_account_address"),
        Index("ix_mailbox_address_address", "address"),
    )

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailbox_account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # primary | send_as | manual — where we learned about this address
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")

    account: Mapped[MailboxAccount] = relationship(back_populates="addresses")


# ---------------------------------------------------------------------------
# Sync bookkeeping
# ---------------------------------------------------------------------------


class SyncState(Base, TimestampMixin):
    """Resumable cursor for one mailbox.

    Deliberately separate from :class:`MailboxAccount`: resetting a sync must
    never risk touching stored credentials, and this row is write-hot.
    """

    __tablename__ = "sync_state"

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailbox_account.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    last_history_id: Mapped[int | None] = mapped_column(BigInteger)
    initial_sync_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Gmail page token, so an interrupted initial sync resumes where it stopped.
    initial_sync_page_token: Mapped[str | None] = mapped_column(Text)
    total_messages_synced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    account: Mapped[MailboxAccount] = relationship(back_populates="sync_state")


class SyncRun(Base):
    """One execution of the sync engine — the operational history."""

    __tablename__ = "sync_run"
    __table_args__ = (Index("ix_sync_run_account_started", "account_id", "started_at"),)

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailbox_account.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SyncStatus.RUNNING.value
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    messages_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    messages_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    threads_touched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attachments_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    start_history_id: Mapped[int | None] = mapped_column(BigInteger)
    end_history_id: Mapped[int | None] = mapped_column(BigInteger)

    error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------


class Contact(Base, TimestampMixin):
    """A person or a role behind one or more e-mail addresses."""

    __tablename__ = "contact"
    __table_args__ = (
        Index(
            "ix_contact_name_trgm",
            "display_name",
            postgresql_using="gin",
            postgresql_ops={"display_name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    display_name: Mapped[str | None] = mapped_column(String(255))
    primary_address: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    domain: Mapped[str | None] = mapped_column(String(255), index=True)
    is_own: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    emails: Mapped[list[ContactEmail]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )


class ContactEmail(Base, TimestampMixin):
    """Additional addresses mapped onto the same contact (identity merging)."""

    __tablename__ = "contact_email"

    id: Mapped[uuid.UUID] = _pk()
    contact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("contact.id", ondelete="CASCADE"), nullable=False, index=True
    )
    address: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)

    contact: Mapped[Contact] = relationship(back_populates="emails")


# ---------------------------------------------------------------------------
# Mail
# ---------------------------------------------------------------------------


class EmailThread(Base, TimestampMixin):
    """A Gmail conversation."""

    __tablename__ = "email_thread"
    __table_args__ = (
        UniqueConstraint("account_id", "gmail_thread_id", name="uq_email_thread_account_gmail_id"),
        Index("ix_email_thread_last_message_at", "account_id", "last_message_at"),
        Index(
            "ix_email_thread_subject_trgm",
            "subject",
            postgresql_using="gin",
            postgresql_ops={"subject": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailbox_account.id", ondelete="CASCADE"), nullable=False
    )
    gmail_thread_id: Mapped[str] = mapped_column(String(64), nullable=False)

    subject: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    first_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_direction: Mapped[str | None] = mapped_column(String(16))

    messages: Mapped[list[EmailMessage]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )


class EmailMessage(Base, TimestampMixin):
    """A single Gmail message, stored verbatim enough to never need a re-fetch."""

    __tablename__ = "email_message"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "gmail_message_id", name="uq_email_message_account_gmail_id"
        ),
        Index("ix_email_message_sent_at", "account_id", "sent_at"),
        Index("ix_email_message_thread", "thread_id", "sent_at"),
        Index("ix_email_message_from_address", "from_address"),
        Index("ix_email_message_rfc822", "rfc822_message_id"),
        # Declared here as well as in migration 0001 so autogenerate never
        # proposes dropping them.
        Index("ix_email_message_search", "search_vector", postgresql_using="gin"),
        Index("ix_email_message_labels", "labels", postgresql_using="gin"),
        CheckConstraint(
            "direction IN ('inbound','outbound','internal','unknown')",
            name="direction_valid",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailbox_account.id", ondelete="CASCADE"), nullable=False
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("email_thread.id", ondelete="CASCADE"), nullable=False
    )

    # Gmail identifiers
    gmail_message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    gmail_thread_id: Mapped[str] = mapped_column(String(64), nullable=False)
    history_id: Mapped[int | None] = mapped_column(BigInteger)

    # RFC 5322 threading headers
    rfc822_message_id: Mapped[str | None] = mapped_column(Text)
    in_reply_to: Mapped[str | None] = mapped_column(Text)
    references: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    subject: Mapped[str | None] = mapped_column(Text)
    from_address: Mapped[str | None] = mapped_column(String(320))
    from_name: Mapped[str | None] = mapped_column(String(255))

    # Which of *my* addresses this message arrived at / was sent from.
    account_address: Mapped[str | None] = mapped_column(String(320), index=True)
    direction: Mapped[str] = mapped_column(
        String(16), nullable=False, default=Direction.UNKNOWN.value
    )

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Gmail's own timestamp — authoritative for ordering, unlike a spoofable Date:.
    internal_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    body_text: Mapped[str | None] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)

    labels: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    size_estimate: Mapped[int | None] = mapped_column(Integer)
    has_attachments: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    raw_headers: Mapped[dict | None] = mapped_column(JSONB)
    # Hash over the fields we care about; lets incremental sync skip no-op writes.
    content_hash: Mapped[str | None] = mapped_column(String(64))

    # Maintained by PostgreSQL itself; declared here so the ORM knows to read
    # it and never to write it.  Must stay identical to migration 0001.
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(SEARCH_VECTOR_EXPRESSION, persisted=True),
        nullable=True,
    )

    thread: Mapped[EmailThread] = relationship(back_populates="messages")
    participants: Mapped[list[EmailParticipant]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class EmailParticipant(Base):
    """One address on one header line of one message."""

    __tablename__ = "email_participant"
    __table_args__ = (
        Index("ix_email_participant_address", "address"),
        Index("ix_email_participant_message_kind", "message_id", "kind"),
        CheckConstraint(
            "kind IN ('from','to','cc','bcc','reply_to','delivered_to')",
            name="kind_valid",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("email_message.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_own: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contact.id", ondelete="SET NULL"), index=True
    )

    message: Mapped[EmailMessage] = relationship(back_populates="participants")


# ---------------------------------------------------------------------------
# Attachments — metadata per message, bytes stored once per distinct content
# ---------------------------------------------------------------------------


class AttachmentBlob(Base, TimestampMixin):
    """The single canonical copy of a file, addressed by SHA-256.

    The same PDF sent twenty times is twenty :class:`Attachment` rows and one
    blob.  Extracted text and embeddings hang off the blob, so a document is
    parsed once no matter how often it circulates.
    """

    __tablename__ = "attachment_blob"

    id: Mapped[uuid.UUID] = _pk()
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255))

    storage_backend: Mapped[str] = mapped_column(String(16), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)

    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attachment(Base, TimestampMixin):
    """An attachment as it appeared in one specific message."""

    __tablename__ = "attachment"
    __table_args__ = (
        UniqueConstraint("message_id", "part_id", name="uq_attachment_message_part"),
        Index("ix_attachment_filename", "filename"),
        Index("ix_attachment_blob", "blob_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailbox_account.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("email_message.id", ondelete="CASCADE"), nullable=False
    )
    blob_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("attachment_blob.id", ondelete="RESTRICT")
    )

    # MIME part path ("1.2"): stable, unlike Gmail's rotating attachmentId.
    part_id: Mapped[str] = mapped_column(String(32), nullable=False)
    gmail_attachment_id: Mapped[str | None] = mapped_column(Text)

    filename: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    content_id: Mapped[str | None] = mapped_column(Text)
    is_inline: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    download_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=DownloadStatus.PENDING.value
    )
    download_error: Mapped[str | None] = mapped_column(Text)

    message: Mapped[EmailMessage] = relationship(back_populates="attachments")
    blob: Mapped[AttachmentBlob | None] = relationship()


class DocumentText(Base, TimestampMixin):
    """Text extracted from one stored file.

    Attached to the **blob**, not the attachment: a contract circulated twenty
    times is parsed once, and phase 3 will hang embeddings off the same row.
    """

    __tablename__ = "document_text"
    __table_args__ = (
        Index("ix_document_text_status", "status"),
        Index("ix_document_text_search", "search_vector", postgresql_using="gin"),
        CheckConstraint(
            "status IN ('extracted','empty','needs_ocr','encrypted','not_a_document',"
            "'unsupported','failed')",
            name="status_valid",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    blob_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("attachment_blob.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    status: Mapped[str] = mapped_column(String(16), nullable=False)
    method: Mapped[str | None] = mapped_column(String(32))
    text: Mapped[str | None] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int | None] = mapped_column(Integer)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Tracked changes and margin comments (Word).
    deleted_text: Mapped[str | None] = mapped_column(Text)
    comment_text: Mapped[str | None] = mapped_column(Text)
    revision_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    revision_authors: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    revision_summary: Mapped[str | None] = mapped_column(Text)

    # Maintained by PostgreSQL; must stay identical to migration 0005.
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(DOCUMENT_SEARCH_VECTOR_EXPRESSION, persisted=True),
        nullable=True,
    )

    blob: Mapped[AttachmentBlob] = relationship()


# ---------------------------------------------------------------------------
# Clients and matters — the "spis" (case file) layer
# ---------------------------------------------------------------------------


class MatterStatus(enum.StrEnum):
    OPEN = "open"
    PENDING = "pending"
    CLOSED = "closed"


class LinkTarget(enum.StrEnum):
    THREAD = "thread"
    MESSAGE = "message"
    ATTACHMENT = "attachment"
    CONTACT = "contact"


class Company(Base, TimestampMixin):
    """A legal entity. Its e-mail domains are the strongest routing signal."""

    __tablename__ = "company"
    __table_args__ = (
        Index(
            "ix_company_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    registration_number: Mapped[str | None] = mapped_column(String(32), index=True)
    domains: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    note: Mapped[str | None] = mapped_column(Text)


class Client(Base, TimestampMixin):
    """Someone you act for — a company, or a natural person via a contact."""

    __tablename__ = "client"
    __table_args__ = (
        Index(
            "ix_client_name_trgm",
            "display_name",
            postgresql_using="gin",
            postgresql_ops={"display_name": "gin_trgm_ops"},
        ),
        CheckConstraint("status IN ('active','inactive','archived')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = _pk()
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(64), unique=True)
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("company.id", ondelete="SET NULL"), index=True
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contact.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")

    # GDPR: how long this client's data may be kept, and until when.
    retention_note: Mapped[str | None] = mapped_column(Text)
    retention_until: Mapped[date | None] = mapped_column(Date)

    company: Mapped[Company | None] = relationship()
    matters: Mapped[list[Matter]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )


class Matter(Base, TimestampMixin):
    """One case or file: the unit everything else is filed under."""

    __tablename__ = "matter"
    __table_args__ = (
        UniqueConstraint("client_id", "reference", name="uq_matter_client_reference"),
        Index(
            "ix_matter_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
        Index("ix_matter_client_status", "client_id", "status"),
        CheckConstraint("status IN ('open','pending','closed')", name="status_valid"),
    )

    id: Mapped[uuid.UUID] = _pk()
    client_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("client.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")

    opened_on: Mapped[date | None] = mapped_column(Date)
    closed_on: Mapped[date | None] = mapped_column(Date)

    client: Mapped[Client] = relationship(back_populates="matters")
    links: Mapped[list[MatterLink]] = relationship(
        back_populates="matter", cascade="all, delete-orphan"
    )


class MatterLink(Base):
    """Something filed under a matter, with how sure we are and why.

    A conversation may belong to several matters, so this is deliberately
    many-to-many rather than a column on the thread.
    """

    __tablename__ = "matter_link"
    __table_args__ = (
        UniqueConstraint("matter_id", "target_type", "target_id", name="uq_matter_link_target"),
        Index("ix_matter_link_target", "target_type", "target_id"),
        Index("ix_matter_link_review", "needs_review"),
        CheckConstraint(
            "target_type IN ('thread','message','attachment','contact')",
            name="target_type_valid",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_in_range"),
    )

    id: Mapped[uuid.UUID] = _pk()
    matter_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("matter.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)

    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    # Which rule decided this, so a wrong link can be traced to its cause.
    method: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    reason: Mapped[str | None] = mapped_column(Text)
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    matter: Mapped[Matter] = relationship(back_populates="links")


# ---------------------------------------------------------------------------
# Actions on the mailbox
# ---------------------------------------------------------------------------


class ActionType(enum.StrEnum):
    LABEL_ADD = "label_add"
    LABEL_REMOVE = "label_remove"
    ARCHIVE = "archive"
    UNARCHIVE = "unarchive"
    DRAFT_CREATE = "draft_create"
    DRAFT_UPDATE = "draft_update"
    TRASH = "trash"
    UNTRASH = "untrash"
    DELETE_PERMANENT = "delete_permanent"
    SEND = "send"
    DRIVE_UPLOAD = "drive_upload"
    TASK_CREATE = "task_create"


class ActionStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"
    EXPIRED = "expired"


class RiskTier(enum.StrEnum):
    AUTOMATIC = "automatic"  # safe, reversible, done without asking
    CONFIGURABLE = "configurable"  # automatic only if you switched it on
    APPROVAL = "approval"  # never without an explicit yes


class FilingFolder(Base, TimestampMixin):
    """A Drive folder that documents for one of my own companies go into.

    Filing is by the entity that was *billed*, not by who sent the invoice:
    Anthropic invoicing INFI files under INFI.  So the terms matched against
    are the recipient company's — its name as it is written on an invoice,
    and its registration numbers, which are the part that never varies.
    """

    __tablename__ = "filing_folder"
    __table_args__ = (
        UniqueConstraint("name", name="uq_filing_folder_name"),
        Index("ix_filing_folder_active", "is_active"),
    )

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    drive_folder_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # What to look for in a document's text.  Stored as given; matching folds
    # case and accents, so "Infinity Finance" finds "INFINITY FINANCE s.r.o."
    match_terms: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    note: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FilingFolder {self.name}>"


class PendingAction(Base):
    """Something the assistant wants to do to the mailbox.

    Every action that changes Gmail passes through this table, including the
    ones executed immediately — so "what did it do, and on whose say-so?" is
    always a query, never a reconstruction.
    """

    __tablename__ = "pending_action"
    __table_args__ = (
        Index("ix_pending_action_status", "status", "created_at"),
        Index("ix_pending_action_target", "target_type", "target_id"),
        CheckConstraint(
            "status IN ('pending','approved','rejected','executed','failed','expired')",
            name="status_valid",
        ),
        CheckConstraint(
            "risk_tier IN ('automatic','configurable','approval')",
            name="risk_tier_valid",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mailbox_account.id", ondelete="CASCADE"), nullable=False
    )

    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_tier: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ActionStatus.PENDING.value
    )

    # What it acts on: a message or a thread, by our id and by Gmail's.
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    gmail_target_id: Mapped[str | None] = mapped_column(String(64))

    # A sentence a person can judge without reading the payload.
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Why the assistant proposed it.
    reason: Mapped[str | None] = mapped_column(Text)
    # Everything the executor needs: label names, draft body, and so on.
    payload: Mapped[dict | None] = mapped_column(JSONB)

    requested_by: Mapped[str] = mapped_column(String(32), nullable=False, default="agent")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # A proposal nobody answered should lapse rather than fire days later.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(64))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    # What it would take to put this back, when that is possible at all.
    undo_hint: Mapped[str | None] = mapped_column(Text)

    def is_open(self, now: datetime) -> bool:
        if self.status != ActionStatus.PENDING.value:
            return False
        return self.expires_at is None or self.expires_at > now


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditLog(Base):
    """Every consequential thing the system does, in one append-only place."""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_occurred_at", "occurred_at"),
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(16), nullable=False)  # system | user | agent
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mailbox_account.id", ondelete="SET NULL")
    )
    summary: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict | None] = mapped_column(JSONB)
    result: Mapped[str] = mapped_column(
        String(16), nullable=False, default=AuditResult.SUCCESS.value
    )
    automatic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True)


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


class ApiKey(Base, TimestampMixin):
    """A revocable credential for one consumer of the API.

    Only the hash is stored; the key itself is shown once, at creation.
    """

    __tablename__ = "api_key"
    __table_args__ = (Index("ix_api_key_prefix", "prefix"),)

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)

    def is_usable(self, now: datetime) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > now


class OAuthState(Base):
    """A one-time state token issued when an authorisation flow starts.

    Verified on callback so a third party cannot drive the flow and bind their
    own mailbox to this installation.  Consumed on first use.
    """

    __tablename__ = "oauth_state"
    __table_args__ = (Index("ix_oauth_state_expires_at", "expires_at"),)

    id: Mapped[uuid.UUID] = _pk()
    state: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # The PKCE verifier belonging to this flow.  The authorisation URL carries
    # only its hash, so the exchange fails with "Missing code verifier" unless
    # the original travels with the state — and the URL is generated by a
    # different process from the one that handles the callback.
    code_verifier: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
