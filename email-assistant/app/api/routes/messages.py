"""Reading and searching stored mail."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import SessionDep
from app.db.models import (
    Attachment,
    AttachmentBlob,
    Contact,
    DocumentText,
    EmailMessage,
    EmailThread,
    MailboxAccount,
)
from app.schemas.common import (
    AttachmentOut,
    DocumentHitOut,
    DocumentTextOut,
    ExtractionSummaryOut,
    MessageDetailOut,
    MessageOut,
    Page,
    ParticipantOut,
    StatsOut,
    ThreadDetailOut,
    ThreadOut,
)
from app.services.documents import extraction_summary, get_document_text
from app.services.search import (
    MessageSearchQuery,
    attachment_search,
    highlight,
    search_documents,
    search_messages,
    search_threads,
)

router = APIRouter(tags=["mail"])


def _message_out(message: EmailMessage, rank: float | None = None) -> MessageOut:
    data = MessageOut.model_validate(message)
    data.rank = rank
    return data


def _message_detail(session: Session, message: EmailMessage) -> MessageDetailOut:
    detail = MessageDetailOut.model_validate(message)
    detail.participants = [ParticipantOut.model_validate(p) for p in message.participants]
    detail.attachments = [_attachment_out(session, a) for a in message.attachments]
    return detail


def _attachment_out(session: Session, attachment: Attachment) -> AttachmentOut:
    out = AttachmentOut.model_validate(attachment)
    if attachment.blob_id:
        blob = session.get(AttachmentBlob, attachment.blob_id)
        out.sha256 = blob.sha256 if blob else None
        document = session.scalar(
            select(DocumentText).where(DocumentText.blob_id == attachment.blob_id)
        )
        if document is not None:
            out.text_status = document.status
            out.text_chars = document.char_count
    return out


@router.get("/messages/search", response_model=Page[MessageOut])
def search(
    q: str | None = Query(default=None, description="Full-text query"),
    account_id: uuid.UUID | None = None,
    from_address: str | None = None,
    participant: str | None = Query(default=None, description="Anyone on any header line"),
    direction: str | None = Query(default=None, pattern="^(inbound|outbound|internal|unknown)$"),
    label: str | None = None,
    subject_contains: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    has_attachments: bool | None = None,
    with_highlight: bool = Query(default=False, description="Include a matching snippet"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = SessionDep,
) -> Page[MessageOut]:
    results = search_messages(
        session,
        MessageSearchQuery(
            account_id=account_id,
            text_query=q,
            from_address=from_address,
            participant=participant,
            direction=direction,
            label=label,
            subject_contains=subject_contains,
            date_from=date_from,
            date_to=date_to,
            has_attachments=has_attachments,
            limit=limit,
            offset=offset,
        ),
    )
    items = []
    for hit in results.hits:
        out = _message_out(hit.message, hit.rank)
        if with_highlight and q:
            out.highlight = highlight(session, hit.message.body_text, q)
        items.append(out)
    return Page(items=items, total=results.total, limit=limit, offset=offset)


@router.get("/messages/{message_id}", response_model=MessageDetailOut)
def get_message(message_id: uuid.UUID, session: Session = SessionDep) -> MessageDetailOut:
    message = session.scalar(
        select(EmailMessage)
        .where(EmailMessage.id == message_id)
        .options(
            selectinload(EmailMessage.participants),
            selectinload(EmailMessage.attachments),
        )
    )
    if message is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Message not found")
    return _message_detail(session, message)


@router.get("/threads", response_model=Page[ThreadOut])
def list_threads(
    q: str | None = None,
    account_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = SessionDep,
) -> Page[ThreadOut]:
    threads, total = search_threads(session, account_id, q, limit, offset)
    return Page(
        items=[ThreadOut.model_validate(t) for t in threads],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/threads/{thread_id}", response_model=ThreadDetailOut)
def get_thread(thread_id: uuid.UUID, session: Session = SessionDep) -> ThreadDetailOut:
    thread = session.get(EmailThread, thread_id)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")

    messages = list(
        session.scalars(
            select(EmailMessage)
            .where(EmailMessage.thread_id == thread_id)
            .order_by(
                func.coalesce(EmailMessage.internal_date, EmailMessage.sent_at).asc(),
                EmailMessage.gmail_message_id.asc(),
            )
            .options(
                selectinload(EmailMessage.participants),
                selectinload(EmailMessage.attachments),
            )
        ).all()
    )
    detail = ThreadDetailOut.model_validate(thread)
    detail.messages = [_message_detail(session, m) for m in messages]
    return detail


@router.get("/attachments", response_model=Page[AttachmentOut])
def list_attachments(
    filename: str | None = Query(default=None, description="Substring of the file name"),
    mime_type: str | None = None,
    account_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = SessionDep,
) -> Page[AttachmentOut]:
    attachments, total = attachment_search(session, account_id, filename, mime_type, limit, offset)
    return Page(
        items=[_attachment_out(session, a) for a in attachments],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=StatsOut)
def stats(session: Session = SessionDep) -> StatsOut:
    by_direction = dict(
        session.execute(
            select(EmailMessage.direction, func.count(EmailMessage.id)).group_by(
                EmailMessage.direction
            )
        ).all()
    )
    span = session.execute(
        select(
            func.min(func.coalesce(EmailMessage.internal_date, EmailMessage.sent_at)),
            func.max(func.coalesce(EmailMessage.internal_date, EmailMessage.sent_at)),
        )
    ).one()
    return StatsOut(
        accounts=session.scalar(select(func.count(MailboxAccount.id))) or 0,
        threads=session.scalar(select(func.count(EmailThread.id))) or 0,
        messages=session.scalar(select(func.count(EmailMessage.id))) or 0,
        attachments=session.scalar(select(func.count(Attachment.id))) or 0,
        attachment_blobs=session.scalar(select(func.count(AttachmentBlob.id))) or 0,
        attachment_bytes=session.scalar(
            select(func.coalesce(func.sum(AttachmentBlob.size_bytes), 0))
        )
        or 0,
        contacts=session.scalar(select(func.count(Contact.id))) or 0,
        messages_by_direction={k: int(v) for k, v in by_direction.items()},
        oldest_message=span[0],
        newest_message=span[1],
    )


@router.get("/documents/search", response_model=Page[DocumentHitOut])
def search_document_text(
    q: str = Query(description="Words to find inside attachment text"),
    account_id: uuid.UUID | None = None,
    with_highlight: bool = True,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = SessionDep,
) -> Page[DocumentHitOut]:
    """Search inside documents, not just their file names."""
    hits, total = search_documents(
        session, account_id, q, limit=limit, offset=offset, with_highlight=with_highlight
    )
    return Page(
        items=[
            DocumentHitOut(
                attachment_id=hit.attachment.id,
                message_id=hit.attachment.message_id,
                filename=hit.attachment.filename,
                mime_type=hit.attachment.mime_type,
                page_count=hit.document.page_count,
                rank=hit.rank,
                highlight=hit.headline,
            )
            for hit in hits
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/attachments/{attachment_id}/text", response_model=DocumentTextOut)
def get_attachment_text(
    attachment_id: uuid.UUID,
    include_text: bool = Query(default=False, description="Return the full text too"),
    session: Session = SessionDep,
) -> DocumentTextOut:
    document = get_document_text(session, attachment_id)
    if document is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No extraction result: the attachment is unknown, its bytes were "
            "never downloaded, or extraction has not run yet.",
        )
    out = DocumentTextOut.model_validate(document)
    if include_text:
        # Returned as an extra field rather than always, so a listing never
        # drags whole documents across the wire.
        return JSONResponse(  # type: ignore[return-value]
            content={**out.model_dump(mode="json"), "text": document.text}
        )
    return out


@router.get("/documents/summary", response_model=ExtractionSummaryOut)
def documents_summary(session: Session = SessionDep) -> ExtractionSummaryOut:
    return ExtractionSummaryOut(**extraction_summary(session))
