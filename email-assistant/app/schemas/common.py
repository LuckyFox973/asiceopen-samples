"""Response schemas shared across the API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


class HealthResponse(BaseModel):
    status: str
    environment: str
    database: str
    version: str


class AccountOut(ORMModel):
    id: uuid.UUID
    email: str
    display_name: str | None
    is_active: bool
    sync_start_date: date | None
    authorised_at: datetime | None
    addresses: list[str] = Field(default_factory=list)


class SyncStateOut(ORMModel):
    last_history_id: int | None
    initial_sync_completed_at: datetime | None
    last_sync_at: datetime | None
    total_messages_synced: int
    initial_sync_pending: bool


class SyncRunOut(ORMModel):
    id: uuid.UUID
    kind: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    messages_seen: int
    messages_created: int
    messages_updated: int
    messages_skipped: int
    threads_touched: int
    attachments_created: int
    error: str | None


class SyncStatusOut(BaseModel):
    account: AccountOut
    state: SyncStateOut | None
    last_runs: list[SyncRunOut]
    counts: dict[str, int]


class ParticipantOut(ORMModel):
    kind: str
    address: str
    display_name: str | None
    is_own: bool


class AttachmentOut(ORMModel):
    id: uuid.UUID
    filename: str | None
    mime_type: str | None
    size_bytes: int | None
    is_inline: bool
    download_status: str
    sha256: str | None = None
    text_status: str | None = None
    text_chars: int | None = None
    revision_count: int = 0
    revision_summary: str | None = None
    version_count: int = 1


class DocumentTextOut(ORMModel):
    status: str
    method: str | None
    char_count: int
    page_count: int | None
    truncated: bool
    error: str | None
    extracted_at: datetime | None
    revision_count: int = 0
    revision_authors: list[str] | None = None
    revision_summary: str | None = None
    deleted_text: str | None = None
    comment_text: str | None = None


class DocumentHitOut(BaseModel):
    attachment_id: uuid.UUID
    message_id: uuid.UUID
    filename: str | None
    mime_type: str | None
    page_count: int | None
    rank: float | None = None
    highlight: str | None = None


class ExtractionSummaryOut(BaseModel):
    extracted: int
    empty: int
    needs_ocr: int
    encrypted: int = 0
    unsupported: int
    failed: int
    pending: int


class DocumentVersionOut(BaseModel):
    attachment_id: uuid.UUID
    message_id: uuid.UUID
    filename: str | None
    sha256: str | None
    received_at: datetime | None
    char_count: int
    revision_count: int
    revision_summary: str | None


class VersionHistoryOut(BaseModel):
    family: str
    count: int
    has_multiple: bool
    versions: list[DocumentVersionOut]


class VersionDiffOut(BaseModel):
    identical: bool
    similarity: float
    added_lines: list[str]
    removed_lines: list[str]
    unified: str
    summary: str


class MessageOut(ORMModel):
    id: uuid.UUID
    gmail_message_id: str
    gmail_thread_id: str
    thread_id: uuid.UUID
    subject: str | None
    from_address: str | None
    from_name: str | None
    account_address: str | None
    direction: str
    sent_at: datetime | None
    internal_date: datetime | None
    snippet: str | None
    labels: list[str] | None
    has_attachments: bool
    rank: float | None = None
    highlight: str | None = None


class MessageDetailOut(MessageOut):
    body_text: str | None
    body_html: str | None
    rfc822_message_id: str | None
    in_reply_to: str | None
    references: list[str] | None
    participants: list[ParticipantOut] = Field(default_factory=list)
    attachments: list[AttachmentOut] = Field(default_factory=list)


class ThreadOut(ORMModel):
    id: uuid.UUID
    gmail_thread_id: str
    subject: str | None
    snippet: str | None
    message_count: int
    first_message_at: datetime | None
    last_message_at: datetime | None
    last_message_direction: str | None


class ThreadDetailOut(ThreadOut):
    messages: list[MessageDetailOut] = Field(default_factory=list)


class AuthStartOut(BaseModel):
    authorisation_url: str
    state: str
    instructions: str


class CompanyOut(ORMModel):
    id: uuid.UUID
    name: str
    registration_number: str | None
    domains: list[str] | None


class ClientOut(ORMModel):
    id: uuid.UUID
    display_name: str
    reference: str | None
    status: str
    company_id: uuid.UUID | None
    retention_until: date | None


class MatterOut(ORMModel):
    id: uuid.UUID
    client_id: uuid.UUID
    title: str
    reference: str | None
    description: str | None
    status: str
    opened_on: date | None
    closed_on: date | None


class MatterDetailOut(MatterOut):
    client_name: str | None = None
    contents: dict[str, int] = Field(default_factory=dict)


class MatterLinkOut(ORMModel):
    id: uuid.UUID
    matter_id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    confidence: float
    method: str
    reason: str | None
    needs_review: bool
    confirmed_at: datetime | None


class SuggestionOut(BaseModel):
    thread_id: uuid.UUID
    thread_subject: str | None
    matter_id: uuid.UUID | None
    matter_title: str | None
    client_id: uuid.UUID | None
    client_name: str | None
    confidence: float
    method: str
    reason: str


class AssignmentResultOut(BaseModel):
    threads_considered: int
    linked: int
    flagged_for_review: int
    unmatched: int
    already_linked: int
    dry_run: bool


class ClientCreateIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=64)
    company_name: str | None = Field(default=None, max_length=255)
    domains: list[str] = Field(default_factory=list)
    registration_number: str | None = Field(default=None, max_length=32)


class MatterCreateIn(BaseModel):
    client_id: uuid.UUID
    title: str = Field(min_length=1, max_length=500)
    reference: str | None = Field(default=None, max_length=64)
    description: str | None = None


class StatsOut(BaseModel):
    accounts: int
    threads: int
    messages: int
    attachments: int
    attachment_blobs: int
    attachment_bytes: int
    contacts: int
    messages_by_direction: dict[str, int]
    oldest_message: datetime | None
    newest_message: datetime | None
