# Email AI Assistant

A personal, long-lived assistant over Google Workspace mail. It keeps its own
durable copy of the mailboxes it is given — messages, threads, participants and
attachments — so that knowledge survives independently of any single AI
conversation or context window.

**Status: Phase 1 (MVP 1) complete and tested.** Gmail ingest, persistence,
search and audit work end to end. There is no AI layer yet, and by design no
ability to send, delete or modify mail — the OAuth scopes requested are
read-only.

## What works today

| Capability | Detail |
|---|---|
| Google OAuth 2.0 | Read-only Gmail scopes, refresh token encrypted at rest |
| Multiple addresses | Send-as aliases imported automatically; `+tag` and Gmail dot forms recognised |
| Start date | Nothing before the configured date is ever fetched or stored |
| Initial sync | Paged, checkpointed, resumable after interruption |
| Incremental sync | Gmail `historyId` cursor, with automatic recovery when the cursor expires |
| Idempotency | Re-running a sync creates nothing new; changed messages are updated in place |
| Threads | Conversations reconstructed with per-message direction |
| Direction | Inbound / outbound / internal, plus which of *your* addresses was used |
| Attachments | Metadata always; bytes stored once per distinct file (SHA-256) |
| Search | Structured filters + PostgreSQL full-text that ignores Slovak diacritics |
| Audit | Every sync run recorded with counts, errors and outcome |

## Quick start

```bash
make install          # venv + dependencies
make dev-db           # local PostgreSQL databases + extensions
cp .env.example .env
python -m app.core.crypto keygen     # paste into TOKEN_ENCRYPTION_KEY
make migrate
make test             # 135 tests
make seed             # load demo mail through the real pipeline
make run              # API on http://localhost:8000  (docs at /docs)
```

Connecting a real mailbox needs a Google Cloud project and an OAuth client —
see [`docs/SETUP.md`](docs/SETUP.md).

## Command line

```bash
python -m app.cli check                     # configuration + database
python -m app.cli auth-url                  # Google consent URL
python -m app.cli accounts                  # connected mailboxes
python -m app.cli sync you@example.com --mode initial
python -m app.cli search "kasačná sťažnosť"
python -m app.cli stats
```

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the stack, why each piece was chosen, how a sync flows
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — tables that exist now and the ones planned
- [`docs/SETUP.md`](docs/SETUP.md) — local setup and Google Cloud configuration
- [`docs/SECURITY.md`](docs/SECURITY.md) — scopes, secrets, GDPR, deletion and export
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — phases, what is done and what comes next

## Layout

```
app/
  core/       configuration, encryption, logging, startup checks
  db/         SQLAlchemy models and session handling
  gmail/      OAuth, API client, MIME parser, address logic
  services/   sync engine, ingest, storage, search, accounts
  api/        FastAPI routes and schemas
  cli.py      operational commands
alembic/      migrations (the only way the schema is ever created)
tests/        unit + integration, run against real PostgreSQL
```
