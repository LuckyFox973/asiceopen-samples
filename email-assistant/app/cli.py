"""Operational command line.

    python -m app.cli check
    python -m app.cli keygen
    python -m app.cli auth-url
    python -m app.cli accounts
    python -m app.cli sync peter@foxgroup.sk --mode initial
    python -m app.cli search "kasačná sťažnosť"
    python -m app.cli stats

Everything here is a thin shell over the same services the HTTP API uses,
so behaviour cannot drift between the two.
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
import uuid
from datetime import date
from urllib.parse import urlparse

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.core.crypto import generate_key
from app.core.logging import configure_logging
from app.core.startup import verify_configuration
from app.db.models import (
    Attachment,
    AttachmentBlob,
    Contact,
    EmailMessage,
    EmailThread,
    MailboxAccount,
)
from app.db.session import session_scope
from app.services.access import (
    OAUTH_STATE_TTL,
    create_api_key,
    list_api_keys,
    record_oauth_state,
    revoke_api_key,
)
from app.services.accounts import get_account_by_email, list_accounts
from app.services.maintenance import find_unreferenced_blobs, prune_orphan_contacts
from app.services.runner import run_sync
from app.services.search import MessageSearchQuery, search_messages


def _resolve_account(session, identifier: str) -> MailboxAccount:
    try:
        account = session.get(MailboxAccount, uuid.UUID(identifier))
    except ValueError:
        account = get_account_by_email(session, identifier)
    if account is not None:
        return account
    # Naming the alternatives costs one query and saves the round trip through
    # `accounts` — a mistyped or invented address is the usual reason to land here.
    known = [record.email for record in list_accounts(session)]
    if not known:
        raise SystemExit(
            f"No mailbox matches {identifier!r} — none are connected yet.\n"
            "Connect one: python -m app.cli auth-url"
        )
    listed = "\n".join(f"  {email}" for email in known)
    raise SystemExit(f"No mailbox matches {identifier!r}. Connected mailboxes:\n{listed}")


def cmd_check(_args: argparse.Namespace) -> int:
    settings = get_settings()
    print(f"environment : {settings.app_env}")
    print(f"database    : {settings.database_url.split('@')[-1]}")
    print(f"start date  : {settings.sync_start_date}")
    print(f"attachments : {settings.attachment_backend}")
    print(f"backups     : {settings.backup_backend if settings.backup_enabled else 'off'}")

    print("\nMailbox permissions:")
    print(f"  write actions     : {'yes' if settings.gmail_write_enabled else 'no (read-only)'}")
    print(f"  archive w/o asking: {'yes' if settings.gmail_auto_archive else 'no'}")
    print(
        f"  permanent delete  : "
        f"{'ENABLED — irreversible' if settings.gmail_allow_permanent_delete else 'no (bin only)'}"
    )

    # Printed one per line so they can be pasted straight into the Google
    # consent screen, which is where a mismatch costs a re-authorisation.
    print("\nOAuth scopes this application will request:")
    for scope in settings.gmail_scopes:
        print(f"  {scope}")
    print("These must match the consent screen exactly — see docs/GOOGLE_CLOUD.md.")

    problems = verify_configuration(settings)
    if problems:
        print("\nConfiguration problems:")
        for problem in problems:
            print(f"  ! {problem}")
    else:
        print("\nConfiguration OK.")

    try:
        with session_scope() as session:
            session.execute(select(func.count(MailboxAccount.id)))
        print("Database    : reachable")
    except Exception as exc:  # noqa: BLE001 - this command exists to report
        print(f"Database    : UNREACHABLE ({exc})")
        return 1
    return 1 if problems else 0


def cmd_keygen(_args: argparse.Namespace) -> int:
    print(generate_key())
    return 0


LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def unreachable_callback(redirect_uri: str) -> str | None:
    """Describe why Google's redirect would land nowhere, or None if it lands.

    Only loopback addresses are checked. A remote deployment may be perfectly
    healthy and still unreachable from wherever this command happens to run,
    and a false alarm there would be worse than no check at all.
    """
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or ""
    if host not in LOOPBACK_HOSTS:
        return None
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        socket.create_connection((host, port), timeout=0.5).close()
    except OSError as exc:
        return f"{host}:{port} — {exc.strerror or exc}"
    return None


def cmd_auth_url(args: argparse.Namespace) -> int:
    from app.gmail.oauth import OAuthNotConfiguredError, build_authorisation_url

    settings = get_settings()

    # Google redirects the browser to the callback the moment consent is
    # given. If nothing is listening there the sign-in is lost and the state
    # token is spent, so it is worth one connection attempt to find out first.
    if not args.no_check:
        problem = unreachable_callback(settings.google_oauth_redirect_uri)
        if problem is not None:
            print(f"error: nothing is listening on {problem}", file=sys.stderr)
            print(
                "\nGoogle sends the browser there after you approve, so the sign-in\n"
                "would fail at the last step. Start the server first, in another\n"
                "Terminal window:\n\n"
                "    ./.venv/bin/uvicorn app.main:app --port 8000\n\n"
                "then run this command again.  (--no-check skips this test.)",
                file=sys.stderr,
            )
            return 1

    # The state is recorded in the database so the callback recognises this
    # flow as ours; without that it would — correctly — reject it.
    with session_scope() as session:
        try:
            url, state, code_verifier = build_authorisation_url(settings)
            record_oauth_state(session, state, code_verifier)
        except OAuthNotConfiguredError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print("Open this URL in a browser and approve access:\n")
    print(url)
    print(f"\nValid for {int(OAUTH_STATE_TTL.total_seconds() // 60)} minutes.")
    print("When the browser shows the confirmation page, run: python -m app.cli accounts")
    return 0


def cmd_api_key(args: argparse.Namespace) -> int:
    with session_scope() as session:
        if args.action == "create":
            issued = create_api_key(session, args.name, expires_in_days=args.expires_in_days)
            print("API key created. It is shown once and cannot be recovered:\n")
            print(f"  {issued.key}\n")
            print(f"name    : {issued.record.name}")
            print(f"prefix  : {issued.record.prefix}")
            print(f"expires : {issued.record.expires_at or 'never'}")
            print("\nUse it as:  Authorization: Bearer <key>")
            return 0

        if args.action == "list":
            keys = list_api_keys(session, include_revoked=args.all)
            if not keys:
                print("No API keys. Create one: python -m app.cli api-key create <name>")
                return 0
            for key in keys:
                state = "revoked" if key.revoked_at else "active"
                used = key.last_used_at.strftime("%Y-%m-%d %H:%M") if key.last_used_at else "never"
                print(f"{key.prefix}…  {key.name:<24} [{state}]  last used: {used}")
            return 0

        record = revoke_api_key(session, args.name)
        if record is None:
            print(f"No API key matches {args.name!r}", file=sys.stderr)
            return 1
        print(f"Revoked {record.name!r} ({record.prefix}…)")
        return 0


def cmd_prune_contacts(args: argparse.Namespace) -> int:
    with session_scope() as session:
        count = prune_orphan_contacts(session, dry_run=args.dry_run)
        verb = "would remove" if args.dry_run else "removed"
        print(f"{verb} {count} contact(s) no message refers to")

        blobs = find_unreferenced_blobs(session)
        if blobs:
            total = sum(b.size_bytes for b in blobs)
            print(
                f"\n{len(blobs)} stored file(s) ({total:,} bytes) are no longer "
                "referenced by any message."
            )
            print("They are reported, not deleted — erasing a document is your decision.")
            for blob in blobs[:10]:
                print(f"  {blob.sha256[:16]}…  {blob.size_bytes:>10,} B  {blob.mime_type}")
    return 0


def cmd_accounts(_args: argparse.Namespace) -> int:
    with session_scope() as session:
        accounts = list_accounts(session)
        if not accounts:
            print("No mailboxes connected yet. Run: python -m app.cli auth-url")
            return 0
        for account in accounts:
            addresses = ", ".join(a.address for a in account.addresses)
            state = "active" if account.is_active else "inactive"
            print(f"{account.id}  {account.email}  [{state}]")
            print(f"    addresses: {addresses or '(none)'}")
            print(f"    start date: {account.sync_start_date or get_settings().sync_start_date}")
    return 0


def _sync_once(args: argparse.Namespace) -> tuple[str, str | None, dict[str, int]]:
    """One pass, in its own transaction, so progress survives an interruption."""
    with session_scope() as session:
        account = _resolve_account(session, args.account)
        if args.start_date:
            account.sync_start_date = date.fromisoformat(args.start_date)
            session.flush()
        run = run_sync(
            session,
            account,
            mode=args.mode,
            download_attachments=not args.no_attachments,
        )
        return (
            run.status,
            run.error,
            {
                "new": run.messages_created,
                "updated": run.messages_updated,
                "unchanged": run.messages_skipped,
                "attachments": run.attachments_created,
                "threads": run.threads_touched,
            },
        )


def cmd_sync(args: argparse.Namespace) -> int:
    """Synchronise until the mailbox is caught up.

    A single pass stops at the per-run message limit and reports ``partial``,
    which used to mean the operator ran the same command over and over. That
    is a loop, and a loop belongs in the program.
    """
    totals = {"new": 0, "updated": 0, "unchanged": 0, "attachments": 0, "threads": 0}
    passes = 0

    while True:
        status, error, counts = _sync_once(args)
        passes += 1
        for key, value in counts.items():
            totals[key] += value

        print(
            f"pass {passes}: {counts['new']} new, {counts['updated']} updated, "
            f"{counts['unchanged']} unchanged, {counts['attachments']} attachments"
            f"   (total so far: {totals['new']} new)"
        )

        if error:
            print(f"\nStopped: {error}", file=sys.stderr)
            print("Nothing already fetched is lost. Run the same command to resume.")
            return 1
        if status == "completed":
            break
        if args.once:
            print("Not finished yet — run the same command again to continue.")
            return 0
        if not any(counts[key] for key in ("new", "updated", "unchanged")):
            # `partial` while fetching nothing would repeat forever.
            print("\nStopped: a pass fetched nothing but the mailbox is not complete.")
            print("Run the same command again later, or report this output.")
            return 1

    print(
        f"\nDone. {totals['new']} messages, {totals['attachments']} attachments, "
        f"{totals['threads']} threads, in {passes} "
        f"{'pass' if passes == 1 else 'passes'}."
    )
    if totals["new"]:
        print("Next: python -m app.cli extract")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    with session_scope() as session:
        results = search_messages(
            session,
            MessageSearchQuery(
                text_query=args.query,
                direction=args.direction,
                limit=args.limit,
            ),
        )
        print(f"{results.total} match(es); showing {len(results.hits)}\n")
        for hit in results.hits:
            message = hit.message
            when = message.internal_date or message.sent_at
            stamp = when.strftime("%Y-%m-%d %H:%M") if when else "?"
            arrow = {"inbound": "<-", "outbound": "->", "internal": "<>"}.get(
                message.direction, "??"
            )
            print(f"{stamp}  {arrow}  {message.from_address}")
            print(f"    {message.subject or '(no subject)'}")
            if message.snippet:
                print(f"    {message.snippet[:110]}")
            print()
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    with session_scope() as session:
        counts = {
            "mailboxes": session.scalar(select(func.count(MailboxAccount.id))),
            "threads": session.scalar(select(func.count(EmailThread.id))),
            "messages": session.scalar(select(func.count(EmailMessage.id))),
            "attachments": session.scalar(select(func.count(Attachment.id))),
            "distinct files": session.scalar(select(func.count(AttachmentBlob.id))),
            "contacts": session.scalar(select(func.count(Contact.id))),
        }
        width = max(len(k) for k in counts)
        for key, value in counts.items():
            print(f"{key:<{width}} : {value}")

        stored = session.scalar(select(func.coalesce(func.sum(AttachmentBlob.size_bytes), 0)))
        print(f"{'stored bytes':<{width}} : {stored:,}")

        by_direction = session.execute(
            select(EmailMessage.direction, func.count(EmailMessage.id)).group_by(
                EmailMessage.direction
            )
        ).all()
        if by_direction:
            print("\nby direction:")
            for direction, count in by_direction:
                print(f"  {direction:<9}: {count}")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    from pathlib import Path

    from app.services.backup import (
        BackupError,
        create_backup,
        list_backups,
        prune_backups,
        restore_archive,
        verify_archive,
    )

    settings = get_settings()
    with session_scope() as session:
        try:
            if args.action == "run":
                artifact = create_backup(
                    session,
                    settings,
                    include_attachments=args.include_attachments or None,
                )
                print(f"{artifact.name}  {artifact.size_bytes:,} bytes  -> {artifact.location}")
                removed = prune_backups(session, settings)
                if removed:
                    print(f"pruned {len(removed)} archive(s) beyond retention")
                return 0

            if args.action == "list":
                artifacts = list_backups(session, settings)
                if not artifacts:
                    print(
                        f"No backups in {settings.backup_local_path}"
                        if settings.backup_backend == "local"
                        else f"No backups in Drive folder {settings.backup_gdrive_folder!r}"
                    )
                    return 0
                for artifact in artifacts:
                    kind = "db+attachments" if artifact.included_attachments else "db only"
                    stamp = artifact.created_at.strftime("%Y-%m-%d %H:%M")
                    print(f"{stamp}  {artifact.size_bytes:>12,} B  {kind:<15} {artifact.name}")
                return 0

            if args.action == "verify":
                target = Path(args.path)
                size = verify_archive(target, settings)
                print(f"{target.name}: decrypts cleanly, {size:,} bytes of payload")
                return 0

            # restore
            source, destination = Path(args.path), Path(args.into)
            restore_archive(source, destination, settings)
            print(f"Decrypted to {destination}")
            print(
                "\nThis is the pg_dump payload, not a restored database — "
                "overwriting a live database is your call:\n"
                f"  createdb email_assistant_restored\n"
                f"  pg_restore --no-owner --dbname email_assistant_restored {destination}"
            )
            return 0
        except BackupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1


def cmd_daemon(args: argparse.Namespace) -> int:
    from app.services.scheduler import Scheduler

    settings = get_settings()
    scheduler = Scheduler(settings)
    scheduler.install_signal_handlers()
    print(
        f"Running: sync every {settings.scheduler_sync_interval_minutes} min"
        + (
            f", backup daily at {settings.scheduler_backup_hour:02d}:00 {settings.timezone}"
            if settings.backup_enabled
            else ", backups disabled"
        )
        + ".  Ctrl-C to stop."
    )
    stats = scheduler.run_forever(max_cycles=args.max_cycles)
    print(
        f"\n{stats.cycles} cycle(s): {stats.syncs_run} sync(s), "
        f"{stats.messages_created} new message(s), {stats.backups_run} backup(s), "
        f"{stats.sync_failures + stats.backup_failures} failure(s)"
    )
    return 1 if stats.errors else 0


def _extract_batch(args: argparse.Namespace) -> tuple[dict[str, int], int, list[str]]:
    """One batch, committed on its own. Returns its counts, what is left, and errors."""
    from app.services.documents import extract_pending, extraction_summary
    from app.services.storage import build_storage

    with session_scope() as session:
        stats = extract_pending(
            session,
            build_storage(),
            limit=args.limit,
            retry_failed=args.retry_failed,
        )
        counts = {
            "considered": stats.considered,
            "extracted": stats.extracted,
            "characters": stats.characters,
            "needs_ocr": stats.needs_ocr,
            "encrypted": stats.encrypted,
            "not_a_document": stats.not_a_document,
            "unsupported": stats.unsupported,
            "empty": stats.empty,
            "failed": stats.failed,
        }
        session.flush()
        remaining = extraction_summary(session, retry_failed=args.retry_failed)["pending"]
        return counts, remaining, list(stats.errors)


def _report_unreadable() -> int:
    """What could not be read, grouped by extension, so it can be judged."""
    from app.services.documents import unreadable_documents

    with session_scope() as session:
        groups = unreadable_documents(session)

    if not groups:
        print("Every stored file was read.")
        return 0

    label = {
        "needs_ocr": "scan, no text layer",
        "encrypted": "password protected",
        "not_a_document": "not a document",
        "unsupported": "format not supported",
        "failed": "could not be parsed",
    }
    print(f"{'files':>5} {'copies':>7}  {'type':<22} {'size':>9}  why")
    for group in groups:
        megabytes = group.bytes_total / 1_048_576
        print(
            f"{group.files:>5} {group.copies:>7}  {group.extension:<22} "
            f"{megabytes:>7.1f} MB  {label.get(group.status, group.status)}"
        )
        if group.example:
            print(f"{'':>14}e.g. {group.example}")
        if group.error:
            print(f"{'':>14}     {group.error[:120]}")
    print("\n`copies` counts how often the file arrived; `files` counts distinct ones.")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    """Extract text from every stored file, in batches, until none are left."""
    from app.services.documents import extraction_summary

    if args.summary:
        with session_scope() as session:
            for key, value in extraction_summary(session).items():
                print(f"{key:<12}: {value}")
        return 0

    if args.problems:
        return _report_unreadable()

    with session_scope() as session:
        remaining = extraction_summary(session, retry_failed=args.retry_failed)["pending"]
    if not remaining:
        print("Nothing to extract — every stored file already has a result.")
        return 0

    totals = dict.fromkeys(
        (
            "considered",
            "extracted",
            "characters",
            "needs_ocr",
            "encrypted",
            "not_a_document",
            "unsupported",
            "empty",
            "failed",
        ),
        0,
    )
    seen_errors: list[str] = []

    while remaining:
        counts, still_pending, errors = _extract_batch(args)
        for key, value in counts.items():
            totals[key] += value
        seen_errors.extend(errors)

        print(
            f"{totals['considered']} of {totals['considered'] + still_pending} file(s) done "
            f"— {totals['extracted']} extracted ({totals['characters']:,} chars)"
        )

        # A file whose extraction raises writes no result row, so it stays
        # pending and would be picked up again for as long as this ran.
        if still_pending >= remaining:
            print(f"\nStopped: {still_pending} file(s) cannot be processed.", file=sys.stderr)
            for error in seen_errors[:5]:
                print(f"  ! {error}", file=sys.stderr)
            return 1
        remaining = still_pending
        if args.once:
            break

    print(
        f"\nDone. {totals['extracted']} extracted ({totals['characters']:,} characters), "
        f"{totals['needs_ocr']} need OCR, {totals['encrypted']} password protected, "
        f"{totals['unsupported']} unsupported, {totals['empty']} empty, "
        f"{totals['failed']} failed. "
        f"{totals['not_a_document']} were signature blocks or decoration, not documents."
    )
    if totals["needs_ocr"]:
        print("Scans need OCR, which is not built yet — their text is not searchable.")
    if totals["encrypted"]:
        print("Password-protected files are listed by: python -m app.cli extract --problems")
    for error in seen_errors[:5]:
        print(f"  ! {error}")
    return 0


def cmd_find(args: argparse.Namespace) -> int:
    """Search inside documents rather than message bodies."""
    from app.services.search import search_documents

    with session_scope() as session:
        hits, total = search_documents(session, None, args.query, limit=args.limit)
        print(f"{total} document(s) match; showing {len(hits)}\n")
        for hit in hits:
            pages = f", {hit.document.page_count} pages" if hit.document.page_count else ""
            print(f"{hit.attachment.filename or '(unnamed)'}{pages}")
            if hit.headline:
                print(f"    {hit.headline}")
            print()
    return 0


def cmd_client(args: argparse.Namespace) -> int:
    from app.db.models import Client
    from app.services.matters import create_client, upsert_company

    with session_scope() as session:
        if args.action == "list":
            clients = session.scalars(select(Client).order_by(Client.display_name)).all()
            if not clients:
                print("No clients yet. Add one: python -m app.cli client add <name>")
                return 0
            for client in clients:
                ref = f"  [{client.reference}]" if client.reference else ""
                print(f"{client.id}  {client.display_name}{ref}  ({client.status})")
            return 0

        company = None
        if args.domains:
            company = upsert_company(session, args.company or args.name, domains=args.domains)
        client = create_client(session, args.name, company=company, reference=args.reference)
        domains = ", ".join(company.domains) if company and company.domains else "none"
        print(f"Created client {client.display_name} ({client.id})")
        print(f"  domains: {domains}")
        return 0


def cmd_matter(args: argparse.Namespace) -> int:
    from app.db.models import Client, Matter
    from app.services.matters import create_matter, matter_contents

    with session_scope() as session:
        if args.action == "list":
            rows = session.execute(
                select(Matter, Client)
                .join(Client, Matter.client_id == Client.id)
                .order_by(Client.display_name, Matter.title)
            ).all()
            if not rows:
                print("No matters yet. Add one: python -m app.cli matter add <client-id> <title>")
                return 0
            for matter, client in rows:
                ref = f"  [{matter.reference}]" if matter.reference else ""
                counts = matter_contents(session, matter.id)
                print(f"{client.display_name} / {matter.title}{ref}  ({matter.status})")
                print(
                    f"    {matter.id}  "
                    f"{counts['thread']} thread(s), "
                    f"{counts['messages_in_threads']} message(s)"
                )
            return 0

        client = session.get(Client, uuid.UUID(args.client))
        if client is None:
            print(f"No client {args.client}", file=sys.stderr)
            return 1
        matter = create_matter(session, client, args.title, reference=args.reference)
        print(f"Created matter {matter.title} ({matter.id}) for {client.display_name}")
        return 0


def cmd_file(args: argparse.Namespace) -> int:
    """Run the assignment pass, or work the review queue."""
    from app.db.models import EmailThread, Matter
    from app.services.matters import (
        assign_threads,
        confirm_link,
        links_needing_review,
        reject_link,
    )

    with session_scope() as session:
        if args.action == "review":
            queue = links_needing_review(session)
            if not queue:
                print("Nothing waiting for review.")
                return 0
            for link in queue:
                matter = session.get(Matter, link.matter_id)
                thread = session.get(EmailThread, link.target_id)
                print(f"{link.id}  confidence {link.confidence:.2f}  via {link.method}")
                print(f"    thread : {thread.subject if thread else link.target_id}")
                print(f"    matter : {matter.title if matter else link.matter_id}")
                print(f"    reason : {link.reason or '-'}")
                print()
            print("Confirm: python -m app.cli file confirm <link-id>")
            print("Reject : python -m app.cli file reject <link-id>")
            return 0

        if args.action == "confirm":
            link = confirm_link(session, uuid.UUID(args.link_id))
            if link is None:
                print(f"No link {args.link_id}", file=sys.stderr)
                return 1
            print("Confirmed.")
            return 0

        if args.action == "reject":
            if not reject_link(session, uuid.UUID(args.link_id)):
                print(f"No link {args.link_id}", file=sys.stderr)
                return 1
            print("Rejected and removed.")
            return 0

        stats = assign_threads(session, limit=args.limit, dry_run=args.dry_run)
        verb = "would file" if args.dry_run else "filed"
        print(
            f"{stats.threads_considered} thread(s) considered: "
            f"{verb} {stats.linked}, {stats.flagged_for_review} need review, "
            f"{stats.unmatched} unmatched, {stats.already_linked} already filed"
        )
        for suggestion in stats.proposals[:10]:
            if suggestion.matter_id is None:
                print(
                    f"  ? {suggestion.client_name}: {suggestion.reason} "
                    f"({suggestion.confidence:.2f})"
                )
        return 0


def cmd_versions(args: argparse.Namespace) -> int:
    """Show what changed in documents that arrived more than once."""
    from app.db.models import Attachment
    from app.services.versions import (
        diff_versions,
        documents_with_revisions,
        families_with_multiple_versions,
        version_history,
    )

    with session_scope() as session:
        if args.revised:
            flagged = documents_with_revisions(session, limit=args.limit)
            if not flagged:
                print("No documents with tracked changes or comments.")
                return 0
            for document in flagged:
                name = session.scalar(
                    select(Attachment.filename).where(Attachment.blob_id == document.blob_id)
                )
                print(f"{name or '(unnamed)'}")
                print(f"    {document.revision_summary}")
                if document.deleted_text:
                    print(f"    removed: {document.deleted_text[:100]}")
            return 0

        if not args.filename:
            families = families_with_multiple_versions(session, limit=args.limit)
            if not families:
                print("No document has been seen with more than one version.")
                return 0
            print("Documents seen in more than one version:\n")
            for family, count in families:
                print(f"  {family}  ({count} versions)")
            print("\nDetail: python -m app.cli versions <filename>")
            return 0

        attachment = session.scalar(
            select(Attachment).where(Attachment.filename.ilike(f"%{args.filename}%")).limit(1)
        )
        if attachment is None:
            print(f"No attachment matches {args.filename!r}", file=sys.stderr)
            return 1

        history = version_history(session, attachment.id)
        print(f"Document family: {history.family}  ({history.count} version(s))\n")
        for index, version in enumerate(history.versions, start=1):
            stamp = (
                version.received_at.strftime("%Y-%m-%d %H:%M")
                if version.received_at
                else "unknown date"
            )
            print(f"  v{index}  {stamp}  {version.filename}")
            print(f"      {version.char_count:,} chars, sha {(version.sha256 or '?')[:12]}")
            if version.revision_summary:
                print(f"      tracked changes: {version.revision_summary}")

        if history.count > 1:
            older, newer = history.versions[-2], history.versions[-1]
            diff = diff_versions(session, older.attachment_id, newer.attachment_id)
            if diff is not None:
                print(f"\nLast change: {diff.summary()}")
                for line in diff.removed_lines[:5]:
                    print(f"  - {line[:110]}")
                for line in diff.added_lines[:5]:
                    print(f"  + {line[:110]}")
    return 0


def cmd_action(args: argparse.Namespace) -> int:
    """Review and decide what the assistant wants to do to the mailbox."""
    from app.db.models import ActionType, PendingAction
    from app.services.actions import (
        ActionError,
        ActionRequest,
        approve,
        describe_target,
        execute,
        history,
        pending,
        propose_and_maybe_execute,
        reject,
        risk_tier,
    )
    from app.services.runner import build_actions

    settings = get_settings()
    with session_scope() as session:
        if args.action in {"list", "history"}:
            items = pending(session) if args.action == "list" else history(session)
            if not items:
                print("Nothing waiting." if args.action == "list" else "No actions yet.")
                return 0
            for item in items:
                print(
                    f"{item.created_at:%Y-%m-%d %H:%M}  {item.action_type:<18}"
                    f"{item.status:<10}[{item.risk_tier}]"
                )
                print(f"    {item.description}")
                print(f"    target: {describe_target(session, item)}")
                if item.reason:
                    print(f"    reason: {item.reason}")
                if item.error:
                    print(f"    error : {item.error[:160]}")
                print(f"    id={item.id}")
            if args.action == "list":
                print("\nApprove: python -m app.cli action approve <id>")
                print("Reject : python -m app.cli action reject <id>")
            return 0

        if args.action in {"approve", "reject"}:
            if not args.action_id:
                print("Give an action id.", file=sys.stderr)
                return 1
            try:
                target = uuid.UUID(args.action_id)
                if args.action == "reject":
                    reject(session, target, note=args.note)
                    print("Rejected. Nothing was changed in the mailbox.")
                    return 0

                item = approve(session, target)
                gmail = build_actions(session, session.get(MailboxAccount, item.account_id))
                done = execute(session, item, gmail, settings)
                if done.status == "executed":
                    print(f"Done: {done.description}")
                    if done.undo_hint:
                        print(f"Undo: {done.undo_hint}")
                else:
                    print(f"Failed: {done.error}", file=sys.stderr)
                    return 1
            except (ActionError, PermissionError, ValueError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            return 0

        # `action draft` — the one proposal worth having a shortcut for.
        account = _resolve_account(session, args.account) if args.account else None
        if account is None:
            accounts = list_accounts(session, active_only=True)
            if not accounts:
                print("No connected mailbox.", file=sys.stderr)
                return 1
            account = accounts[0]

        try:
            gmail = build_actions(session, account, settings)
        except PermissionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        item = propose_and_maybe_execute(
            session,
            account,
            ActionRequest(
                action_type=ActionType.DRAFT_CREATE,
                description=f"Draft a reply to {args.to}",
                gmail_target_id=None,
                payload={
                    "to": [a.strip() for a in args.to.split(",") if a.strip()],
                    "subject": args.subject,
                    "body": args.body,
                    "thread_id": args.thread_id or None,
                },
                requested_by="user",
            ),
            gmail=gmail,
            settings=settings,
        )
        tier = risk_tier(ActionType.DRAFT_CREATE).value
        print(f"{item.status} [{tier}] — {item.description}")
        if item.result:
            print(f"    {item.result}")
        assert isinstance(item, PendingAction)
        return 0


def cmd_import_credentials(args: argparse.Namespace) -> int:
    """Read the OAuth client file Google gave you and fill in .env."""
    from pathlib import Path

    from app.services.credentials import (
        CredentialsError,
        check_redirect_uris,
        find_download,
        parse,
        write_env,
    )

    settings = get_settings()
    try:
        path = find_download(args.path or None)
        client = parse(path)
    except CredentialsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Found {path.name}")
    print(f"  client id : {client.masked_id}")
    print(f"  type      : {client.kind}")
    if client.project_id:
        print(f"  project   : {client.project_id}")

    problems = check_redirect_uris(client, settings.google_oauth_redirect_uri)

    env_path = Path(args.env or ".env")
    changed = write_env(
        env_path,
        {
            "GOOGLE_CLIENT_ID": client.client_id,
            "GOOGLE_CLIENT_SECRET": client.client_secret,
        },
        template=Path(".env.example"),
    )
    if changed:
        print(f"\nWrote {', '.join(changed)} to {env_path} (now readable only by you).")
    else:
        print(f"\n{env_path} already had these values.")

    if problems:
        print("\nFix before authorising:")
        for problem in problems:
            print(f"  ! {problem}")
        return 1

    print("\nRedirect URIs look right.")
    print(f"\nThe downloaded file still holds your client secret. Delete it:\n  rm {path}")
    print("\nNext:  python -m app.cli check")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="Email AI Assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify configuration and database").set_defaults(func=cmd_check)
    sub.add_parser("keygen", help="generate a TOKEN_ENCRYPTION_KEY").set_defaults(func=cmd_keygen)
    auth_parser = sub.add_parser("auth-url", help="print the Google consent URL")
    auth_parser.add_argument(
        "--no-check",
        action="store_true",
        help="print the URL even if the callback address is not listening",
    )
    auth_parser.set_defaults(func=cmd_auth_url)
    sub.add_parser("accounts", help="list connected mailboxes").set_defaults(func=cmd_accounts)

    sync_parser = sub.add_parser("sync", help="synchronise a mailbox")
    sync_parser.add_argument("account", help="mailbox e-mail address or id")
    sync_parser.add_argument("--mode", choices=["auto", "initial", "incremental"], default="auto")
    sync_parser.add_argument("--start-date", help="override the start date (YYYY-MM-DD)")
    sync_parser.add_argument("--no-attachments", action="store_true", help="store metadata only")
    sync_parser.add_argument(
        "--once",
        action="store_true",
        help="do a single pass instead of running until the mailbox is caught up",
    )
    sync_parser.set_defaults(func=cmd_sync)

    search_parser = sub.add_parser("search", help="full-text search stored mail")
    search_parser.add_argument("query")
    search_parser.add_argument(
        "--direction", choices=["inbound", "outbound", "internal", "unknown"]
    )
    search_parser.add_argument("--limit", type=int, default=20)
    search_parser.set_defaults(func=cmd_search)

    sub.add_parser("stats", help="show what is stored").set_defaults(func=cmd_stats)

    key_parser = sub.add_parser("api-key", help="manage API keys")
    key_parser.add_argument("action", choices=["create", "list", "revoke"])
    key_parser.add_argument(
        "name", nargs="?", default="", help="key name (create) or name/prefix (revoke)"
    )
    key_parser.add_argument("--expires-in-days", type=int, default=None)
    key_parser.add_argument("--all", action="store_true", help="include revoked keys when listing")
    key_parser.set_defaults(func=cmd_api_key)

    prune_parser = sub.add_parser("prune-contacts", help="remove contacts no message refers to")
    prune_parser.add_argument("--dry-run", action="store_true")
    prune_parser.set_defaults(func=cmd_prune_contacts)

    backup_parser = sub.add_parser("backup", help="encrypted backups")
    backup_parser.add_argument("action", choices=["run", "list", "verify", "restore"])
    backup_parser.add_argument("path", nargs="?", default="", help="archive path")
    backup_parser.add_argument(
        "--into", default="./restored.dump", help="where to write a restored dump"
    )
    backup_parser.add_argument("--include-attachments", action="store_true")
    backup_parser.set_defaults(func=cmd_backup)

    daemon_parser = sub.add_parser("daemon", help="run sync (and backups) on a schedule, locally")
    daemon_parser.add_argument(
        "--max-cycles", type=int, default=None, help="stop after N cycles (testing)"
    )
    daemon_parser.set_defaults(func=cmd_daemon)

    extract_parser = sub.add_parser("extract", help="pull text out of stored attachments")
    extract_parser.add_argument("--limit", type=int, default=100)
    extract_parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="read again every file that produced no text — use after new formats are added",
    )
    extract_parser.add_argument(
        "--summary", action="store_true", help="show counts instead of extracting"
    )
    extract_parser.add_argument(
        "--once", action="store_true", help="do a single batch instead of finishing the queue"
    )
    extract_parser.add_argument(
        "--problems", action="store_true", help="list the files that could not be read"
    )
    extract_parser.set_defaults(func=cmd_extract)

    find_parser = sub.add_parser("find", help="search inside document text")
    find_parser.add_argument("query")
    find_parser.add_argument("--limit", type=int, default=10)
    find_parser.set_defaults(func=cmd_find)

    client_parser = sub.add_parser("client", help="manage clients")
    client_parser.add_argument("action", choices=["add", "list"])
    client_parser.add_argument("name", nargs="?", default="")
    client_parser.add_argument("--reference")
    client_parser.add_argument("--company", help="company name, if different")
    client_parser.add_argument(
        "--domains", nargs="*", default=[], help="e-mail domains that identify this client"
    )
    client_parser.set_defaults(func=cmd_client)

    matter_parser = sub.add_parser("matter", help="manage matters (case files)")
    matter_parser.add_argument("action", choices=["add", "list"])
    matter_parser.add_argument("client", nargs="?", default="", help="client id")
    matter_parser.add_argument("title", nargs="?", default="")
    matter_parser.add_argument("--reference", help="file number, e.g. KOV-2026-01")
    matter_parser.set_defaults(func=cmd_matter)

    file_parser = sub.add_parser("file", help="file threads under matters")
    file_parser.add_argument(
        "action", nargs="?", default="run", choices=["run", "review", "confirm", "reject"]
    )
    file_parser.add_argument("link_id", nargs="?", default="")
    file_parser.add_argument("--limit", type=int, default=200)
    file_parser.add_argument("--dry-run", action="store_true")
    file_parser.set_defaults(func=cmd_file)

    versions_parser = sub.add_parser(
        "versions", help="documents that arrived more than once, and what changed"
    )
    versions_parser.add_argument(
        "filename", nargs="?", default="", help="show detail for one document"
    )
    versions_parser.add_argument(
        "--revised", action="store_true", help="list documents with tracked changes"
    )
    versions_parser.add_argument("--limit", type=int, default=50)
    versions_parser.set_defaults(func=cmd_versions)

    action_parser = sub.add_parser("action", help="review and decide Gmail actions")
    action_parser.add_argument("action", choices=["list", "history", "approve", "reject", "draft"])
    action_parser.add_argument("action_id", nargs="?", default="")
    action_parser.add_argument("--note", help="why it was rejected")
    action_parser.add_argument("--account", help="mailbox e-mail or id")
    action_parser.add_argument("--to", default="", help="recipients, comma separated")
    action_parser.add_argument("--subject", default="")
    action_parser.add_argument("--body", default="")
    action_parser.add_argument("--thread-id", default="", help="Gmail thread to reply in")
    action_parser.set_defaults(func=cmd_action)

    creds_parser = sub.add_parser(
        "import-credentials",
        help="read Google's downloaded client_secret*.json into .env",
    )
    creds_parser.add_argument(
        "path", nargs="?", default="", help="path to the JSON (default: newest download)"
    )
    creds_parser.add_argument("--env", default="", help="which .env to write (default: ./.env)")
    creds_parser.set_defaults(func=cmd_import_credentials)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except OperationalError as exc:
        # A stopped database is the commonest way any of these commands fails,
        # and a hundred lines of SQLAlchemy traceback says none of that.
        print(f"error: the database is not reachable.\n\n  {_first_line(exc)}\n", file=sys.stderr)
        print("If it is running on this Mac, start it with:", file=sys.stderr)
        print("    brew services start postgresql@16", file=sys.stderr)
        print("\nThen run: python -m app.cli check", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped. Nothing already stored is lost — run the same command to resume.")
        return 130


def _first_line(exc: Exception) -> str:
    """The sentence in a driver error that actually says what went wrong."""
    text = str(exc).strip()
    if not text:
        return exc.__class__.__name__
    # SQLAlchemy prefixes the driver's own message with its exception class,
    # which is noise in front of the part a person can act on.
    return re.sub(r"^\([\w.]+\)\s*", "", text.splitlines()[0])


if __name__ == "__main__":
    raise SystemExit(main())
