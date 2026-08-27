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
import sys
import uuid
from datetime import date

from sqlalchemy import func, select

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
    issue_oauth_state,
    list_api_keys,
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
    if account is None:
        raise SystemExit(f"No mailbox matches {identifier!r}. Try: python -m app.cli accounts")
    return account


def cmd_check(_args: argparse.Namespace) -> int:
    settings = get_settings()
    print(f"environment : {settings.app_env}")
    print(f"database    : {settings.database_url.split('@')[-1]}")
    print(f"start date  : {settings.sync_start_date}")
    print(f"attachments : {settings.attachment_backend}")
    print(f"scopes      : {', '.join(settings.gmail_scopes)}")

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


def cmd_auth_url(_args: argparse.Namespace) -> int:
    from app.gmail.oauth import OAuthNotConfiguredError, build_authorisation_url

    # The state is recorded in the database so the callback recognises this
    # flow as ours; without that it would — correctly — reject it.
    with session_scope() as session:
        try:
            state = issue_oauth_state(session)
            url, _ = build_authorisation_url(state=state)
        except OAuthNotConfiguredError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    print("Open this URL in a browser and approve access:\n")
    print(url)
    print(f"\nValid for {int(OAUTH_STATE_TTL.total_seconds() // 60)} minutes.")
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


def cmd_sync(args: argparse.Namespace) -> int:
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
        print(
            f"{run.kind} sync {run.status}: "
            f"{run.messages_created} new, {run.messages_updated} updated, "
            f"{run.messages_skipped} unchanged, "
            f"{run.attachments_created} attachments, "
            f"{run.threads_touched} threads"
        )
        if run.error:
            print(f"error: {run.error}")
            return 1
        if run.status == "partial":
            print("Run hit the per-run limit; run again to continue.")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="Email AI Assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="verify configuration and database").set_defaults(func=cmd_check)
    sub.add_parser("keygen", help="generate a TOKEN_ENCRYPTION_KEY").set_defaults(func=cmd_keygen)
    sub.add_parser("auth-url", help="print the Google consent URL").set_defaults(func=cmd_auth_url)
    sub.add_parser("accounts", help="list connected mailboxes").set_defaults(func=cmd_accounts)

    sync_parser = sub.add_parser("sync", help="synchronise a mailbox")
    sync_parser.add_argument("account", help="mailbox e-mail address or id")
    sync_parser.add_argument("--mode", choices=["auto", "initial", "incremental"], default="auto")
    sync_parser.add_argument("--start-date", help="override the start date (YYYY-MM-DD)")
    sync_parser.add_argument("--no-attachments", action="store_true", help="store metadata only")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
