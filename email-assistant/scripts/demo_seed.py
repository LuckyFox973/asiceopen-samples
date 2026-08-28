"""Load a few sample messages through the real pipeline.

Only the Gmail transport is replaced by a fixture; the parser, the ingest
layer, the attachment store and the search index are the production ones.
Use it to see the system working before the Google project exists.

    python scripts/demo_seed.py          # load
    python scripts/demo_seed.py --reset  # remove the demo mailbox first
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.db.models import MailboxAccount, MailboxAddress
from app.db.session import session_scope
from app.services.documents import extract_pending
from app.services.storage import build_storage
from app.services.sync import SyncEngine
from tests.fixtures import attachment_part, gmail_message, multipart, text_part
from tests.fixtures.documents import make_docx, make_docx_with_revisions, make_pdf
from tests.fixtures.fake_gmail import FakeGmailClient

DEMO_EMAIL = "demo@example.invalid"
BASE = int(datetime(2026, 8, 20, 8, 0, tzinfo=UTC).timestamp() * 1000)


# A genuine PDF, so the demo exercises text extraction and document search
# rather than only the mail pipeline.
DEMO_PDF = make_pdf(
    [
        "Rozhodnutie o danovej kontrole DPH za rok 2025. Spravca dane "
        "konstatoval, ze predlozene CMR listy boli duplicitne.",
        "Proti tomuto rozhodnutiu je pripustne odvolanie do 15 dni.",
    ]
)


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# The same contract twice: a clean draft, then the other side's revision.
# This is what makes version tracking visible in the demo.
DEMO_DOCX_V1 = make_docx(["Zmluva o dielo", "Zmluvna pokuta je 5000 EUR.", "Lehota: 30 dni."])
DEMO_DOCX_V2 = make_docx_with_revisions(
    before="Zmluvna pokuta je ",
    deleted="5000 EUR",
    inserted="2000 EUR",
    after=". Lehota: 30 dni.",
    delete_author="Protistrana",
    insert_author="Protistrana",
    comment=("Advokat", "Znizenie pokuty neakceptujeme, trvame na povodnej sume."),
)


def hours(n: int) -> str:
    return str(BASE + n * 3600_000)


def build_messages():
    podklady = multipart(
        "multipart/mixed",
        [
            text_part(
                "Dobrý deň,\n\nv prílohe posielam rozhodnutie správcu dane o daňovej "
                "kontrole DPH za rok 2025. Prosím o vyjadrenie do konca mesiaca.\n\n"
                "S pozdravom\nJán Novák, ABC s.r.o.",
                part_id="0",
            ),
            attachment_part(
                "Rozhodnutie.pdf",
                size=len(DEMO_PDF),
                attachment_id="att-rozhodnutie",
                part_id="1",
            ),
        ],
    )
    return [
        gmail_message(
            message_id="demo-1",
            thread_id="demo-t1",
            subject="Daňová kontrola DPH 2025 – podklady",
            from_="Ján Novák <jan.novak@abc.sk>",
            to=DEMO_EMAIL,
            internal_date_ms=hours(0),
            payload=podklady,
            labels=["INBOX", "UNREAD"],
        ),
        gmail_message(
            message_id="demo-2",
            thread_id="demo-t1",
            subject="Re: Daňová kontrola DPH 2025 – podklady",
            from_=f"Demo <{DEMO_EMAIL}>",
            to="jan.novak@abc.sk",
            internal_date_ms=hours(3),
            payload=text_part(
                "Dobrý deň,\n\npodklady som prevzal. Vyjadrenie pripravím do 31.08.2026.\n"
            ),
            labels=["SENT"],
        ),
        gmail_message(
            message_id="demo-3",
            thread_id="demo-t2",
            subject="KOVACO – kasačná sťažnosť",
            from_="Podateľňa <podatelna@justice.example>",
            to=DEMO_EMAIL,
            internal_date_ms=hours(26),
            payload=text_part(
                "Správca dane v odôvodnení tvrdil, že predložené CMR listy boli "
                "duplicitné a nepreukazujú prepravu tovaru.",
                charset="windows-1250",
            ),
            labels=["INBOX"],
        ),
        gmail_message(
            message_id="demo-5",
            thread_id="demo-t4",
            subject="Zmluva o dielo – navrh",
            from_="Protistrana <pravnik@kovaco.example>",
            to=DEMO_EMAIL,
            internal_date_ms=hours(4),
            payload=multipart(
                "multipart/mixed",
                [
                    text_part("Posielam navrh zmluvy.", part_id="0"),
                    attachment_part(
                        "Zmluva.docx",
                        mime_type=DOCX_MIME,
                        size=len(DEMO_DOCX_V1),
                        attachment_id="att-zmluva-v1",
                        part_id="1",
                    ),
                ],
            ),
            labels=["INBOX"],
        ),
        gmail_message(
            message_id="demo-6",
            thread_id="demo-t4",
            subject="Re: Zmluva o dielo – navrh",
            from_="Protistrana <pravnik@kovaco.example>",
            to=DEMO_EMAIL,
            internal_date_ms=hours(52),
            payload=multipart(
                "multipart/mixed",
                [
                    text_part("V prilohe nase pripomienky v sledovanych zmenach.", part_id="0"),
                    attachment_part(
                        "Zmluva_v2.docx",
                        mime_type=DOCX_MIME,
                        size=len(DEMO_DOCX_V2),
                        attachment_id="att-zmluva-v2",
                        part_id="1",
                    ),
                ],
            ),
            labels=["INBOX"],
        ),
        gmail_message(
            message_id="demo-4",
            thread_id="demo-t3",
            subject="Newsletter – zmeny v daňovom poriadku",
            from_="noreply@newsletter.example",
            to=DEMO_EMAIL,
            internal_date_ms=hours(30),
            payload=text_part("Prehľad legislatívnych zmien účinných od 01.01.2027."),
            labels=["INBOX", "CATEGORY_PROMOTIONS"],
        ),
    ]


def reset(session) -> None:
    account = session.scalar(select(MailboxAccount).where(MailboxAccount.email == DEMO_EMAIL))
    if account is not None:
        session.delete(account)
        session.flush()
        print(f"removed demo mailbox {DEMO_EMAIL}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    with session_scope() as session:
        if args.reset:
            reset(session)

        account = session.scalar(select(MailboxAccount).where(MailboxAccount.email == DEMO_EMAIL))
        if account is None:
            account = MailboxAccount(
                email=DEMO_EMAIL,
                display_name="Demo mailbox",
                sync_start_date=date(2026, 8, 1),
            )
            session.add(account)
            session.flush()
            session.add(
                MailboxAddress(
                    account_id=account.id,
                    address=DEMO_EMAIL,
                    is_primary=True,
                    source="primary",
                )
            )
            session.flush()
            session.refresh(account)

        engine = SyncEngine(
            session=session,
            account=account,
            client=FakeGmailClient(
                build_messages(),
                attachments={
                    "att-rozhodnutie": DEMO_PDF,
                    "att-zmluva-v1": DEMO_DOCX_V1,
                    "att-zmluva-v2": DEMO_DOCX_V2,
                },
            ),
            storage=build_storage(),
            default_start_date=date(2026, 8, 1),
            download_attachments=True,
        )
        run = engine.initial_sync()
        print(
            f"{run.kind} sync {run.status}: {run.messages_created} new, "
            f"{run.messages_updated} updated, {run.messages_skipped} unchanged, "
            f"{run.attachments_created} attachments"
        )
        stats = extract_pending(session, build_storage())
        print(f"extraction: {stats.extracted} document(s), {stats.characters:,} characters")
        print(f"mailbox id: {account.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
