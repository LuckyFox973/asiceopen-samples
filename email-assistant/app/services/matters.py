"""Filing conversations under clients and matters.

Two principles from the brief drive this module:

*Do not create matters aggressively.* Nothing here ever invents a matter. It
links to matters that already exist, and where it cannot, it produces a
*proposal* a person accepts or ignores. A wrong auto-created file is far more
expensive to unpick than an unfiled thread.

*Say how sure you are, and why.* Every link records a confidence, the rule that
produced it, and a human-readable reason. Below the confidence threshold a link
is still made but flagged ``needs_review``, so nothing disappears silently and
nothing is asserted more strongly than the evidence supports.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.db.models import (
    AuditLog,
    Client,
    Company,
    Contact,
    EmailMessage,
    EmailParticipant,
    EmailThread,
    LinkTarget,
    Matter,
    MatterLink,
)
from app.gmail.addresses import domain_of

log = get_logger(__name__)

# At or above this, a link stands on its own; below it, a person is asked.
AUTO_LINK_THRESHOLD = 0.75
# Below this the evidence is too thin to record even as a review item.
MIN_USEFUL_CONFIDENCE = 0.35

# Reply and forward prefixes in the languages this mailbox actually sees.
SUBJECT_PREFIX = re.compile(
    r"^\s*((re|aw|fw|fwd|odp|odpoveď|odpoved|vec|pred|fs)\s*(\[\d+\])?\s*:\s*)+",
    re.IGNORECASE,
)
WHITESPACE = re.compile(r"\s+")


def normalise_subject(subject: str | None) -> str:
    """Strip reply/forward prefixes so a chain compares as one subject."""
    if not subject:
        return ""
    text = SUBJECT_PREFIX.sub("", subject)
    return WHITESPACE.sub(" ", text).strip().lower()


@dataclass(slots=True)
class MatterSuggestion:
    """One candidate filing decision, with its evidence."""

    matter_id: uuid.UUID | None
    matter_title: str | None
    client_id: uuid.UUID | None
    client_name: str | None
    confidence: float
    method: str
    reason: str

    @property
    def is_confident(self) -> bool:
        return self.confidence >= AUTO_LINK_THRESHOLD


@dataclass
class AssignmentStats:
    threads_considered: int = 0
    linked: int = 0
    flagged_for_review: int = 0
    unmatched: int = 0
    already_linked: int = 0
    proposals: list[MatterSuggestion] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reading the graph
# ---------------------------------------------------------------------------


def external_domains(session: Session, thread: EmailThread) -> set[str]:
    """Domains of everyone on the thread who is not me."""
    rows = session.execute(
        select(EmailParticipant.address)
        .join(EmailMessage, EmailParticipant.message_id == EmailMessage.id)
        .where(EmailMessage.thread_id == thread.id, EmailParticipant.is_own.is_(False))
    ).scalars()
    return {d for d in (domain_of(address) for address in rows) if d}


def clients_for_domains(session: Session, domains: set[str]) -> list[Client]:
    """Clients whose company claims any of these domains."""
    if not domains:
        return []
    return list(
        session.scalars(
            select(Client)
            .join(Company, Client.company_id == Company.id)
            .where(Company.domains.overlap(list(domains)), Client.status == "active")
            .distinct()
        ).all()
    )


def open_matters_for_client(session: Session, client_id: uuid.UUID) -> list[Matter]:
    return list(
        session.scalars(
            select(Matter)
            .where(Matter.client_id == client_id, Matter.status.in_(["open", "pending"]))
            .order_by(Matter.opened_on.desc().nullslast())
        ).all()
    )


def existing_link(
    session: Session, target_type: LinkTarget, target_id: uuid.UUID
) -> MatterLink | None:
    return session.scalar(
        select(MatterLink).where(
            MatterLink.target_type == target_type.value,
            MatterLink.target_id == target_id,
        )
    )


# ---------------------------------------------------------------------------
# The rules, strongest first
# ---------------------------------------------------------------------------


def suggest_for_thread(session: Session, thread: EmailThread) -> MatterSuggestion | None:
    """Best filing suggestion for a thread, or None if there is no evidence."""
    for rule in (
        _rule_reference_in_subject,
        _rule_sibling_thread,
        _rule_single_open_matter_for_client,
        _rule_subject_similarity,
        _rule_client_without_a_clear_matter,
    ):
        suggestion = rule(session, thread)
        if suggestion is not None and suggestion.confidence >= MIN_USEFUL_CONFIDENCE:
            return suggestion
    return None


def _rule_reference_in_subject(session: Session, thread: EmailThread) -> MatterSuggestion | None:
    """An explicit matter reference in the subject settles it.

    People quote file numbers precisely because they want the mail filed there.
    """
    subject = thread.subject or ""
    if not subject.strip():
        return None

    for matter in session.scalars(
        select(Matter).where(Matter.reference.isnot(None), Matter.status != "closed")
    ):
        reference = (matter.reference or "").strip()
        # Short references would match by accident inside ordinary words.
        if len(reference) < 4:
            continue
        if re.search(rf"(?<!\w){re.escape(reference)}(?!\w)", subject, re.IGNORECASE):
            return _suggestion(
                session,
                matter,
                0.95,
                "subject_reference",
                f"Subject quotes the matter reference {reference!r}",
            )
    return None


def _rule_sibling_thread(session: Session, thread: EmailThread) -> MatterSuggestion | None:
    """The same normalised subject with the same people is the same business."""
    normalised = normalise_subject(thread.subject)
    if len(normalised) < 8:
        return None

    domains = external_domains(session, thread)
    candidates = session.execute(
        select(EmailThread, MatterLink)
        .join(
            MatterLink,
            (MatterLink.target_id == EmailThread.id)
            & (MatterLink.target_type == LinkTarget.THREAD.value),
        )
        .where(EmailThread.id != thread.id, EmailThread.account_id == thread.account_id)
    ).all()

    for sibling, link in candidates:
        if normalise_subject(sibling.subject) != normalised:
            continue
        matter = session.get(Matter, link.matter_id)
        if matter is None:
            continue
        shares_people = bool(domains & external_domains(session, sibling))
        confidence = 0.9 if shares_people else 0.7
        return _suggestion(
            session,
            matter,
            confidence,
            "sibling_thread",
            (
                "Another thread with the same subject is already filed here"
                + (" and shares participants" if shares_people else "")
            ),
        )
    return None


def _rule_single_open_matter_for_client(
    session: Session, thread: EmailThread
) -> MatterSuggestion | None:
    """One client by domain, and exactly one open matter — the obvious home."""
    clients = clients_for_domains(session, external_domains(session, thread))
    if len(clients) != 1:
        return None

    matters = open_matters_for_client(session, clients[0].id)
    if len(matters) != 1:
        return None

    return _suggestion(
        session,
        matters[0],
        0.8,
        "single_open_matter",
        f"{clients[0].display_name} has exactly one open matter",
    )


def _rule_subject_similarity(session: Session, thread: EmailThread) -> MatterSuggestion | None:
    """Subject close to a matter's title, scored by trigram similarity."""
    normalised = normalise_subject(thread.subject)
    if len(normalised) < 8:
        return None

    clients = clients_for_domains(session, external_domains(session, thread))
    stmt = select(
        Matter, func.similarity(func.lower(Matter.title), normalised).label("score")
    ).where(Matter.status != "closed")
    if clients:
        stmt = stmt.where(Matter.client_id.in_([c.id for c in clients]))

    row = session.execute(
        stmt.order_by(func.similarity(func.lower(Matter.title), normalised).desc()).limit(1)
    ).first()
    if row is None:
        return None

    matter, score = row
    score = float(score or 0.0)
    if score < 0.3:
        return None

    # Similarity alone is suggestive, never conclusive: cap it below the
    # auto-link threshold unless the client also matches.
    confidence = min(0.7, 0.35 + score) if clients else min(0.6, 0.25 + score)
    return _suggestion(
        session,
        matter,
        confidence,
        "subject_similarity",
        f"Subject resembles the matter title (similarity {score:.2f})",
    )


def _rule_client_without_a_clear_matter(
    session: Session, thread: EmailThread
) -> MatterSuggestion | None:
    """The client is clear but the matter is not — worth saying so."""
    clients = clients_for_domains(session, external_domains(session, thread))
    if len(clients) != 1:
        return None
    return MatterSuggestion(
        matter_id=None,
        matter_title=None,
        client_id=clients[0].id,
        client_name=clients[0].display_name,
        confidence=0.5,
        method="client_only",
        reason=(f"Participants belong to {clients[0].display_name}, but which matter is unclear"),
    )


def _suggestion(
    session: Session, matter: Matter, confidence: float, method: str, reason: str
) -> MatterSuggestion:
    client = session.get(Client, matter.client_id)
    return MatterSuggestion(
        matter_id=matter.id,
        matter_title=matter.title,
        client_id=matter.client_id,
        client_name=client.display_name if client else None,
        confidence=confidence,
        method=method,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Applying decisions
# ---------------------------------------------------------------------------


def link_target(
    session: Session,
    matter_id: uuid.UUID,
    target_type: LinkTarget,
    target_id: uuid.UUID,
    confidence: float = 1.0,
    method: str = "manual",
    reason: str | None = None,
) -> MatterLink:
    """File something under a matter. Idempotent per (matter, target)."""
    link = session.scalar(
        select(MatterLink).where(
            MatterLink.matter_id == matter_id,
            MatterLink.target_type == target_type.value,
            MatterLink.target_id == target_id,
        )
    )
    needs_review = confidence < AUTO_LINK_THRESHOLD

    if link is None:
        link = MatterLink(
            matter_id=matter_id,
            target_type=target_type.value,
            target_id=target_id,
            confidence=confidence,
            method=method,
            reason=reason,
            needs_review=needs_review,
            confirmed_at=datetime.now(UTC) if method == "manual" else None,
        )
        session.add(link)
    elif confidence > link.confidence and link.confirmed_at is None:
        # Better evidence arrived; never downgrade, and never override a
        # decision a person has confirmed.
        link.confidence = confidence
        link.method = method
        link.reason = reason
        link.needs_review = needs_review
    session.flush()
    return link


def confirm_link(session: Session, link_id: uuid.UUID) -> MatterLink | None:
    link = session.get(MatterLink, link_id)
    if link is None:
        return None
    link.needs_review = False
    link.confidence = 1.0
    link.method = "confirmed"
    link.confirmed_at = datetime.now(UTC)
    _audit(session, "matter.link_confirmed", link, "Filing confirmed")
    session.flush()
    return link


def reject_link(session: Session, link_id: uuid.UUID) -> bool:
    """Remove a wrong filing. The audit entry survives the link."""
    link = session.get(MatterLink, link_id)
    if link is None:
        return False
    _audit(session, "matter.link_rejected", link, "Filing rejected")
    session.delete(link)
    session.flush()
    return True


def links_needing_review(session: Session, limit: int = 50) -> list[MatterLink]:
    return list(
        session.scalars(
            select(MatterLink)
            .where(MatterLink.needs_review.is_(True), MatterLink.confirmed_at.is_(None))
            .order_by(MatterLink.confidence.desc(), MatterLink.created_at.desc())
            .limit(limit)
        ).all()
    )


def assign_threads(
    session: Session,
    account_id: uuid.UUID | None = None,
    limit: int = 200,
    dry_run: bool = False,
) -> AssignmentStats:
    """Suggest a matter for every thread that has none, and file the clear ones."""
    stats = AssignmentStats()

    stmt = select(EmailThread).order_by(EmailThread.last_message_at.desc().nullslast())
    if account_id is not None:
        stmt = stmt.where(EmailThread.account_id == account_id)

    for thread in session.scalars(stmt.limit(limit)):
        stats.threads_considered += 1
        if existing_link(session, LinkTarget.THREAD, thread.id) is not None:
            stats.already_linked += 1
            continue

        suggestion = suggest_for_thread(session, thread)
        if suggestion is None:
            stats.unmatched += 1
            continue

        stats.proposals.append(suggestion)
        if suggestion.matter_id is None:
            # A client without a matter is a proposal only: creating one is a
            # decision for a person, never a side effect of a sync.
            stats.unmatched += 1
            continue

        if dry_run:
            # Count what would happen, change nothing.
            if suggestion.is_confident:
                stats.linked += 1
            else:
                stats.flagged_for_review += 1
            continue

        link_target(
            session,
            suggestion.matter_id,
            LinkTarget.THREAD,
            thread.id,
            confidence=suggestion.confidence,
            method=suggestion.method,
            reason=suggestion.reason,
        )
        if suggestion.is_confident:
            stats.linked += 1
        else:
            stats.flagged_for_review += 1

    if not dry_run and (stats.linked or stats.flagged_for_review):
        session.add(
            AuditLog(
                occurred_at=datetime.now(UTC),
                actor="system",
                action="matter.threads_assigned",
                entity_type="matter_link",
                account_id=account_id,
                summary=(
                    f"Filed {stats.linked} thread(s); "
                    f"{stats.flagged_for_review} need review, "
                    f"{stats.unmatched} unmatched"
                ),
                details={
                    "considered": stats.threads_considered,
                    "linked": stats.linked,
                    "needs_review": stats.flagged_for_review,
                    "unmatched": stats.unmatched,
                },
                automatic=True,
            )
        )
        session.flush()
    return stats


def matter_contents(session: Session, matter_id: uuid.UUID) -> dict[str, int]:
    """What is filed under a matter, by kind — the 'spis' at a glance."""
    rows = session.execute(
        select(MatterLink.target_type, func.count(MatterLink.id))
        .where(MatterLink.matter_id == matter_id)
        .group_by(MatterLink.target_type)
    ).all()
    counts = {target.value: 0 for target in LinkTarget}
    counts.update({str(t): int(c) for t, c in rows})

    thread_ids = list(
        session.scalars(
            select(MatterLink.target_id).where(
                MatterLink.matter_id == matter_id,
                MatterLink.target_type == LinkTarget.THREAD.value,
            )
        ).all()
    )
    counts["messages_in_threads"] = (
        session.scalar(
            select(func.count(EmailMessage.id)).where(EmailMessage.thread_id.in_(thread_ids))
        )
        or 0
        if thread_ids
        else 0
    )
    return counts


def _audit(session: Session, action: str, link: MatterLink, summary: str) -> None:
    session.add(
        AuditLog(
            occurred_at=datetime.now(UTC),
            actor="user",
            action=action,
            entity_type="matter_link",
            entity_id=str(link.id),
            summary=f"{summary} ({link.target_type} → matter {link.matter_id})",
            details={
                "matter_id": str(link.matter_id),
                "target_type": link.target_type,
                "target_id": str(link.target_id),
                "method": link.method,
                "confidence": link.confidence,
            },
            automatic=False,
        )
    )


def upsert_company(
    session: Session,
    name: str,
    domains: list[str] | None = None,
    registration_number: str | None = None,
) -> Company:
    company = session.scalar(select(Company).where(func.lower(Company.name) == name.lower()))
    if company is None:
        company = Company(name=name)
        session.add(company)
    if domains:
        merged = set(company.domains or []) | {d.lower().strip() for d in domains if d}
        company.domains = sorted(merged)
    if registration_number:
        company.registration_number = registration_number
    session.flush()
    return company


def create_client(
    session: Session,
    display_name: str,
    company: Company | None = None,
    contact: Contact | None = None,
    reference: str | None = None,
) -> Client:
    client = Client(
        display_name=display_name,
        company_id=company.id if company else None,
        contact_id=contact.id if contact else None,
        reference=reference,
    )
    session.add(client)
    session.flush()
    return client


def create_matter(
    session: Session,
    client: Client,
    title: str,
    reference: str | None = None,
    description: str | None = None,
) -> Matter:
    matter = Matter(
        client_id=client.id,
        title=title,
        reference=reference,
        description=description,
        opened_on=datetime.now(UTC).date(),
    )
    session.add(matter)
    session.flush()
    return matter
