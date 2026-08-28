"""Clients, matters, and the filing of conversations under them."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import SessionDep
from app.db.models import Client, Company, EmailThread, Matter, MatterLink
from app.schemas.common import (
    AssignmentResultOut,
    ClientCreateIn,
    ClientOut,
    CompanyOut,
    MatterCreateIn,
    MatterDetailOut,
    MatterLinkOut,
    MatterOut,
    Page,
    SuggestionOut,
)
from app.services.matters import (
    assign_threads,
    confirm_link,
    create_client,
    create_matter,
    links_needing_review,
    matter_contents,
    reject_link,
    suggest_for_thread,
    upsert_company,
)

router = APIRouter(tags=["matters"])


# --- clients ---------------------------------------------------------------


@router.get("/clients", response_model=Page[ClientOut])
def list_clients(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = SessionDep,
) -> Page[ClientOut]:
    stmt = select(Client).order_by(Client.display_name)
    if status_filter:
        stmt = stmt.where(Client.status == status_filter)
    total = len(session.scalars(stmt).all())
    rows = session.scalars(stmt.limit(limit).offset(offset)).all()
    return Page(
        items=[ClientOut.model_validate(c) for c in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/clients", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
def add_client(payload: ClientCreateIn, session: Session = SessionDep) -> ClientOut:
    company = None
    if payload.company_name:
        company = upsert_company(
            session,
            payload.company_name,
            domains=payload.domains,
            registration_number=payload.registration_number,
        )
    client = create_client(
        session, payload.display_name, company=company, reference=payload.reference
    )
    return ClientOut.model_validate(client)


@router.get("/companies", response_model=list[CompanyOut])
def list_companies(session: Session = SessionDep) -> list[CompanyOut]:
    return [
        CompanyOut.model_validate(c)
        for c in session.scalars(select(Company).order_by(Company.name)).all()
    ]


# --- matters ---------------------------------------------------------------


@router.get("/matters", response_model=Page[MatterOut])
def list_matters(
    client_id: uuid.UUID | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = SessionDep,
) -> Page[MatterOut]:
    stmt = select(Matter).order_by(Matter.opened_on.desc().nullslast(), Matter.title)
    if client_id:
        stmt = stmt.where(Matter.client_id == client_id)
    if status_filter:
        stmt = stmt.where(Matter.status == status_filter)
    total = len(session.scalars(stmt).all())
    rows = session.scalars(stmt.limit(limit).offset(offset)).all()
    return Page(
        items=[MatterOut.model_validate(m) for m in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/matters", response_model=MatterOut, status_code=status.HTTP_201_CREATED)
def add_matter(payload: MatterCreateIn, session: Session = SessionDep) -> MatterOut:
    client = session.get(Client, payload.client_id)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client not found")
    matter = create_matter(
        session,
        client,
        payload.title,
        reference=payload.reference,
        description=payload.description,
    )
    return MatterOut.model_validate(matter)


@router.get("/matters/{matter_id}", response_model=MatterDetailOut)
def get_matter(matter_id: uuid.UUID, session: Session = SessionDep) -> MatterDetailOut:
    matter = session.get(Matter, matter_id)
    if matter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Matter not found")
    client = session.get(Client, matter.client_id)
    detail = MatterDetailOut.model_validate(matter)
    detail.client_name = client.display_name if client else None
    detail.contents = matter_contents(session, matter_id)
    return detail


@router.get("/matters/{matter_id}/links", response_model=list[MatterLinkOut])
def matter_links(matter_id: uuid.UUID, session: Session = SessionDep) -> list[MatterLinkOut]:
    if session.get(Matter, matter_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Matter not found")
    rows = session.scalars(
        select(MatterLink)
        .where(MatterLink.matter_id == matter_id)
        .order_by(MatterLink.created_at.desc())
    ).all()
    return [MatterLinkOut.model_validate(link) for link in rows]


# --- filing ----------------------------------------------------------------


@router.post("/matters/assign", response_model=AssignmentResultOut)
def run_assignment(
    account_id: uuid.UUID | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    dry_run: bool = Query(default=False, description="Report what would be filed, change nothing"),
    session: Session = SessionDep,
) -> AssignmentResultOut:
    """File unassigned threads under the matters the evidence supports.

    Never creates a matter: where the client is clear but the matter is not,
    the thread is reported as unmatched rather than given a new file.
    """
    stats = assign_threads(session, account_id, limit=limit, dry_run=dry_run)
    return AssignmentResultOut(
        threads_considered=stats.threads_considered,
        linked=stats.linked,
        flagged_for_review=stats.flagged_for_review,
        unmatched=stats.unmatched,
        already_linked=stats.already_linked,
        dry_run=dry_run,
    )


@router.get("/matters/suggestions/{thread_id}", response_model=SuggestionOut | None)
def suggestion_for_thread(
    thread_id: uuid.UUID, session: Session = SessionDep
) -> SuggestionOut | None:
    thread = session.get(EmailThread, thread_id)
    if thread is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Thread not found")
    suggestion = suggest_for_thread(session, thread)
    if suggestion is None:
        return None
    return SuggestionOut(
        thread_id=thread.id,
        thread_subject=thread.subject,
        matter_id=suggestion.matter_id,
        matter_title=suggestion.matter_title,
        client_id=suggestion.client_id,
        client_name=suggestion.client_name,
        confidence=suggestion.confidence,
        method=suggestion.method,
        reason=suggestion.reason,
    )


@router.get("/matters/review/queue", response_model=list[MatterLinkOut])
def review_queue(
    limit: int = Query(default=50, ge=1, le=200), session: Session = SessionDep
) -> list[MatterLinkOut]:
    """Filings the system was not confident enough to make on its own."""
    return [MatterLinkOut.model_validate(link) for link in links_needing_review(session, limit)]


@router.post("/matters/links/{link_id}/confirm", response_model=MatterLinkOut)
def confirm(link_id: uuid.UUID, session: Session = SessionDep) -> MatterLinkOut:
    link = confirm_link(session, link_id)
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
    return MatterLinkOut.model_validate(link)


@router.delete("/matters/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def reject(link_id: uuid.UUID, session: Session = SessionDep) -> None:
    if not reject_link(session, link_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Link not found")
