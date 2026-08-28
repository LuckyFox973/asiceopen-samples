"""Structured and full-text search over stored mail.

MVP 1 covers the two deterministic modes: exact/filtered lookup and
PostgreSQL full-text.  Semantic (vector) search is additive — it will rank
alongside these rather than replace them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.db.models import (
    Attachment,
    AttachmentBlob,
    DocumentText,
    EmailMessage,
    EmailParticipant,
    EmailThread,
)

# Must match the configuration created in migration 0001.
TS_CONFIG = "public.sk_unaccent"


@dataclass(slots=True)
class MessageSearchQuery:
    account_id: uuid.UUID | None = None
    text_query: str | None = None
    from_address: str | None = None
    participant: str | None = None
    direction: str | None = None
    label: str | None = None
    thread_id: uuid.UUID | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    has_attachments: bool | None = None
    subject_contains: str | None = None
    limit: int = 50
    offset: int = 0


@dataclass(slots=True)
class SearchHit:
    message: EmailMessage
    rank: float | None = None
    headline: str | None = None


@dataclass(slots=True)
class SearchResults:
    hits: list[SearchHit]
    total: int
    limit: int
    offset: int


def _apply_filters(stmt: Select, q: MessageSearchQuery) -> Select:
    conditions: list[Any] = []

    if q.account_id is not None:
        conditions.append(EmailMessage.account_id == q.account_id)
    if q.from_address:
        conditions.append(func.lower(EmailMessage.from_address) == q.from_address.lower())
    if q.direction:
        conditions.append(EmailMessage.direction == q.direction)
    if q.thread_id is not None:
        conditions.append(EmailMessage.thread_id == q.thread_id)
    if q.label:
        conditions.append(EmailMessage.labels.any(q.label))
    if q.date_from is not None:
        conditions.append(
            func.coalesce(EmailMessage.internal_date, EmailMessage.sent_at) >= q.date_from
        )
    if q.date_to is not None:
        conditions.append(
            func.coalesce(EmailMessage.internal_date, EmailMessage.sent_at) <= q.date_to
        )
    if q.has_attachments is not None:
        conditions.append(EmailMessage.has_attachments.is_(q.has_attachments))
    if q.subject_contains:
        conditions.append(EmailMessage.subject.ilike(f"%{q.subject_contains}%"))

    if q.participant:
        # Anyone on any header line of the message, including me.
        participant = q.participant.lower()
        conditions.append(
            or_(
                func.lower(EmailMessage.from_address) == participant,
                EmailMessage.id.in_(
                    select(EmailParticipant.message_id).where(
                        func.lower(EmailParticipant.address) == participant
                    )
                ),
            )
        )

    if conditions:
        stmt = stmt.where(and_(*conditions))
    return stmt


def search_messages(session: Session, q: MessageSearchQuery) -> SearchResults:
    """Run a combined structured + full-text query."""
    tsquery = None
    if q.text_query and q.text_query.strip():
        # websearch_to_tsquery understands quoted phrases, OR and -negation,
        # and never raises on malformed user input the way to_tsquery does.
        tsquery = func.websearch_to_tsquery(TS_CONFIG, q.text_query.strip())

    stmt = select(EmailMessage)
    if tsquery is not None:
        stmt = stmt.where(EmailMessage.search_vector.op("@@")(tsquery))
    stmt = _apply_filters(stmt, q)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = session.scalar(count_stmt) or 0

    if tsquery is not None:
        rank = func.ts_rank_cd(EmailMessage.search_vector, tsquery)
        stmt = stmt.add_columns(rank.label("rank")).order_by(
            rank.desc(),
            func.coalesce(EmailMessage.internal_date, EmailMessage.sent_at).desc(),
        )
    else:
        stmt = stmt.order_by(func.coalesce(EmailMessage.internal_date, EmailMessage.sent_at).desc())

    stmt = stmt.limit(max(1, min(q.limit, 200))).offset(max(0, q.offset))

    hits: list[SearchHit] = []
    if tsquery is not None:
        for message, rank_value in session.execute(stmt).all():
            hits.append(SearchHit(message=message, rank=float(rank_value or 0.0)))
    else:
        for message in session.scalars(stmt).all():
            hits.append(SearchHit(message=message))

    return SearchResults(hits=hits, total=total, limit=q.limit, offset=q.offset)


def highlight(session: Session, body: str | None, query: str, max_words: int = 30) -> str | None:
    """Produce a ``ts_headline`` snippet showing why a message matched."""
    if not body or not query.strip():
        return None
    stmt = select(
        func.ts_headline(
            TS_CONFIG,
            body,
            func.websearch_to_tsquery(TS_CONFIG, query.strip()),
            text(f"'MaxWords={max_words}, MinWords=10, ShortWord=2, MaxFragments=2'"),
        )
    )
    return session.scalar(stmt)


def search_threads(
    session: Session,
    account_id: uuid.UUID | None,
    text_query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[EmailThread], int]:
    """Find threads whose subject matches, or that contain a matching message."""
    stmt = select(EmailThread)
    if account_id is not None:
        stmt = stmt.where(EmailThread.account_id == account_id)

    if text_query and text_query.strip():
        tsquery = func.websearch_to_tsquery(TS_CONFIG, text_query.strip())
        stmt = stmt.where(
            or_(
                EmailThread.subject.ilike(f"%{text_query.strip()}%"),
                EmailThread.id.in_(
                    select(EmailMessage.thread_id).where(
                        EmailMessage.search_vector.op("@@")(tsquery)
                    )
                ),
            )
        )

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = (
        stmt.order_by(EmailThread.last_message_at.desc().nullslast())
        .limit(max(1, min(limit, 200)))
        .offset(max(0, offset))
    )
    return list(session.scalars(stmt).all()), total


def attachment_search(
    session: Session,
    account_id: uuid.UUID | None,
    filename_contains: str | None = None,
    mime_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Attachment], int]:
    stmt = select(Attachment)
    if account_id is not None:
        stmt = stmt.where(Attachment.account_id == account_id)
    if filename_contains:
        stmt = stmt.where(Attachment.filename.ilike(f"%{filename_contains}%"))
    if mime_type:
        stmt = stmt.where(Attachment.mime_type == mime_type)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    stmt = stmt.order_by(Attachment.created_at.desc()).limit(min(limit, 200)).offset(offset)
    return list(session.scalars(stmt).all()), total


@dataclass(slots=True)
class DocumentHit:
    attachment: Attachment
    document: DocumentText
    rank: float | None = None
    headline: str | None = None


def search_documents(
    session: Session,
    account_id: uuid.UUID | None,
    text_query: str,
    limit: int = 25,
    offset: int = 0,
    with_highlight: bool = True,
) -> tuple[list[DocumentHit], int]:
    """Full-text search inside extracted attachment text.

    Answers the question a filename cannot: "where did the tax authority claim
    the CMR notes were duplicates?"  Results carry the attachment they came
    from, so a hit is always traceable back to a message.
    """
    if not text_query or not text_query.strip():
        return [], 0

    tsquery = func.websearch_to_tsquery(TS_CONFIG, text_query.strip())
    rank = func.ts_rank_cd(DocumentText.search_vector, tsquery)

    stmt = (
        select(Attachment, DocumentText, rank.label("rank"))
        .join(AttachmentBlob, Attachment.blob_id == AttachmentBlob.id)
        .join(DocumentText, DocumentText.blob_id == AttachmentBlob.id)
        .where(DocumentText.search_vector.op("@@")(tsquery))
    )
    if account_id is not None:
        stmt = stmt.where(Attachment.account_id == account_id)

    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    stmt = (
        stmt.order_by(rank.desc(), Attachment.created_at.desc())
        .limit(max(1, min(limit, 100)))
        .offset(max(0, offset))
    )

    hits: list[DocumentHit] = []
    for attachment, document, rank_value in session.execute(stmt).all():
        headline = None
        if with_highlight and document.text:
            headline = session.scalar(
                select(
                    func.ts_headline(
                        TS_CONFIG,
                        document.text,
                        tsquery,
                        text("'MaxWords=35, MinWords=15, MaxFragments=2'"),
                    )
                )
            )
        hits.append(
            DocumentHit(
                attachment=attachment,
                document=document,
                rank=float(rank_value or 0.0),
                headline=headline,
            )
        )
    return hits, total
