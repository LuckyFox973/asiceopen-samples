"""Measure what a mailbox actually costs in database bytes.

Generates realistic messages, pushes them through the production ingest path,
then reads the true on-disk size per table.  The point is to size a database
plan from a measurement rather than a guess.

    python scripts/measure_storage.py --messages 2000
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db.models import MailboxAccount, MailboxAddress
from app.db.session import session_scope
from app.gmail.addresses import OwnedAddressSet
from app.gmail.parser import parse_and_resolve
from app.services.ingest import MessageIngestor
from tests.fixtures import gmail_message, multipart, text_part

BENCH_EMAIL = "bench@example.invalid"
BASE_MS = int(datetime(2026, 1, 2, 8, 0, tzinfo=UTC).timestamp() * 1000)

# A plausible mix for a working legal mailbox.
LEAD = [
    "Dobrý deň, v nadväznosti na našu komunikáciu posielam",
    "Vážený pán kolega, k Vášmu podaniu uvádzame",
    "Dobrý deň, správca dane vo svojom rozhodnutí konštatoval, že",
    "Potvrdzujeme prijatie Vašej žiadosti vo veci",
    "V prílohe zasielame vyjadrenie k výzve zo dňa",
]
BODY = [
    "predložené doklady preukazujú uskutočnenie zdaniteľného plnenia",
    "lehota na podanie kasačnej sťažnosti uplynie dňom",
    "prepravu tovaru preukazujú CMR listy, ktoré neboli duplicitné",
    "daňová kontrola DPH za zdaňovacie obdobie roku 2025",
    "žiadame o predĺženie lehoty na vyjadrenie o tridsať dní",
    "odvolací orgán sa nevysporiadal s námietkami účastníka konania",
    "z obsahu administratívneho spisu vyplýva, že",
]


def make_body(rng: random.Random, paragraphs: int) -> str:
    out = [rng.choice(LEAD)]
    for _ in range(paragraphs):
        out.append(" ".join(rng.choice(BODY) for _ in range(rng.randint(4, 12))) + ".")
    out.append("S pozdravom\nJUDr. Vzor Vzorový\nadvokát")
    return "\n\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--messages", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    rng = random.Random(args.seed)

    with session_scope() as session:
        session.execute(text("DELETE FROM mailbox_account WHERE email = :e"), {"e": BENCH_EMAIL})
        account = MailboxAccount(
            email=BENCH_EMAIL, display_name="Bench", sync_start_date=date(2026, 1, 1)
        )
        session.add(account)
        session.flush()
        session.add(
            MailboxAddress(
                account_id=account.id, address=BENCH_EMAIL, is_primary=True, source="primary"
            )
        )
        session.flush()
        session.refresh(account)

        owned = OwnedAddressSet([BENCH_EMAIL])
        ingestor = MessageIngestor(session, account, owned=owned)

        threads = max(1, args.messages // 3)
        for i in range(args.messages):
            thread = f"bench-t{i % threads}"
            inbound = i % 3 != 1
            paragraphs = rng.choices([1, 3, 6, 12], weights=[35, 40, 20, 5])[0]
            body = make_body(rng, paragraphs)
            # Most real mail carries an HTML alternative as well.
            payload = (
                multipart(
                    "multipart/alternative",
                    [
                        text_part(body, part_id="0"),
                        text_part(
                            "<html><body>"
                            + "".join(f"<p>{p}</p>" for p in body.split("\n\n"))
                            + "</body></html>",
                            mime_type="text/html",
                            part_id="1",
                        ),
                    ],
                )
                if rng.random() < 0.8
                else text_part(body)
            )
            raw = gmail_message(
                message_id=f"bench-{i}",
                thread_id=thread,
                subject=f"{'Re: ' if i % 3 else ''}Vec {i % threads} – {rng.choice(BODY)[:40]}",
                from_=(
                    f"Protistrana {i % 97} <p{i % 97}@example.sk>"
                    if inbound
                    else f"Bench <{BENCH_EMAIL}>"
                ),
                to=BENCH_EMAIL if inbound else f"p{i % 97}@example.sk",
                internal_date_ms=str(BASE_MS + i * 600_000),
                payload=payload,
            )
            ingestor.ingest(parse_and_resolve(raw, owned), download_attachments=False)
            if i % 500 == 499:
                session.commit()
                print(f"  ingested {i + 1}/{args.messages}")
        session.commit()

        rows = session.execute(
            text("""
            SELECT relname,
                   pg_total_relation_size(c.oid) AS total,
                   pg_relation_size(c.oid)       AS heap,
                   pg_indexes_size(c.oid)        AS idx
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY pg_total_relation_size(c.oid) DESC
        """)
        ).all()

        total = sum(r.total for r in rows)
        count = session.execute(text("SELECT count(*) FROM email_message")).scalar_one()

        print(f"\n{'table':<20}{'total':>12}{'heap':>12}{'indexes':>12}")
        print("-" * 56)
        for relname, tot, heap, idx in rows:
            if tot == 0:
                continue
            print(f"{relname:<20}{tot / 1024:>10.0f} K{heap / 1024:>10.0f} K{idx / 1024:>10.0f} K")
        print("-" * 56)
        print(f"{'TOTAL':<20}{total / 1024 / 1024:>10.1f} M")
        print(f"\nmessages stored : {count}")
        print(f"bytes / message : {total / max(count, 1):,.0f}")
        print(f"per 10 000 msgs : {total / max(count, 1) * 10_000 / 1024 / 1024:,.0f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
