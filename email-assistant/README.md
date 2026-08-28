# Email AI Assistant

A personal, long-lived assistant over Google Workspace mail. It keeps its own
durable copy of the mailboxes it is given — messages, threads, participants and
attachments — so that knowledge survives independently of any single AI
conversation or context window.

**Status: runs locally, end to end.** Gmail ingest, persistence, search, audit,
API authentication, a local scheduler and encrypted backups all work and are
tested. There is no AI layer yet, and by design no ability to send, delete or
modify mail — the OAuth scopes requested are read-only.

It runs on your own machine for now, deliberately: no cloud bill until the
system has proved itself. The one honest cost of that is that **nothing runs
while the machine is off** — no overnight sync, no early-morning briefing.
Moving to a server later is a change of `DATABASE_URL` and a scheduler, not a
rewrite.

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
| Access control | Hashed, revocable API keys; forced on outside development |
| OAuth safety | One-time state token verified on callback |
| Scheduling | Local daemon: sync on an interval, backup once a day |
| Backups | Encrypted (AES-256-GCM) to disk or Google Drive, with retention |
| Document text | PDF, DOCX, XLSX, CSV, HTML, TXT parsed deterministically — no LLM |
| Document search | Full-text *inside* attachments, diacritics-insensitive |
| Tracked changes | Word revisions read correctly — insertions kept, deletions recorded, comments indexed |
| Document versions | A revised file is recognised as a new version, with a diff |
| MCP server | 24 tools, so Claude can query and act — no API key, no token bill |
| Mailbox actions | Labels, archive, drafts, bin — in risk tiers, all audited, destructive ones always ask |
| Clients & matters | Conversations filed under case files, with confidence and a review queue |

## Quick start

```bash
make install          # venv + dependencies
make dev-db           # local PostgreSQL databases + extensions
cp .env.example .env
python -m app.core.crypto keygen     # paste into TOKEN_ENCRYPTION_KEY
make migrate
make test             # 446 tests
make seed             # load demo mail through the real pipeline
make run              # API on http://localhost:8000  (docs at /docs)
```

Connecting a real mailbox needs a Google Cloud project and an OAuth client.
**Do that once, following [`docs/GOOGLE_SETUP.md`](docs/GOOGLE_SETUP.md)** —
it decides the OAuth scopes up front, because adding one later means
re-authorising every mailbox.

## Command line

```bash
python -m app.cli check                     # configuration + database
python -m app.cli auth-url                  # Google consent URL
python -m app.cli accounts                  # connected mailboxes
python -m app.cli sync you@example.com --mode initial
python -m app.cli search "kasačná sťažnosť"
python -m app.cli stats

python -m app.cli api-key create mcp-server   # shown once, stored hashed
python -m app.cli api-key list
python -m app.cli prune-contacts              # reclaim orphaned personal data

python -m app.cli backup run                  # encrypted; refuses without a key
python -m app.cli backup verify <archive>
python -m app.cli daemon                      # sync + extraction + daily backup

python -m app.cli extract                     # parse stored attachments
python -m app.cli extract --summary
python -m app.cli find "CMR duplicitné"       # search inside documents

python -m app.cli client add "KOVACO" --domains kovaco.sk
python -m app.cli matter add <client-id> "Kasačná sťažnosť" --reference KOV-2026-01
python -m app.cli file run --dry-run          # what would be filed where
python -m app.cli file run
python -m app.cli file review                 # the uncertain ones

python -m app.cli versions                    # documents seen more than once
python -m app.cli versions Zmluva             # what changed between versions
python -m app.cli versions --revised          # files with tracked changes
```

## Using it from Claude

No API key, no per-token cost — Claude queries the database as a tool over MCP:

```bash
claude mcp add email-assistant \
  -- /full/path/to/email-assistant/.venv/bin/python -m app.mcp.server
```

Then ask it things: *"Where did the tax authority claim the CMR notes were
duplicates?"*, *"What changed in the KOVACO contract between versions?"*.
See [`docs/MCP.md`](docs/MCP.md) — including how to reach it from an iPhone.

## Running the whole stack

With Docker (PostgreSQL, API and scheduler together):

```bash
make up        # starts the database, migrates, then brings up api + scheduler
make logs
make down      # stops; volumes are kept
```

Ports are bound to `127.0.0.1` only — the API holds mailbox contents and must
not be reachable from the local network.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the stack, why each piece was chosen, how a sync flows
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — tables that exist now and the ones planned
- [`docs/SETUP.md`](docs/SETUP.md) — local setup and Google Cloud configuration
- [`docs/SECURITY.md`](docs/SECURITY.md) — scopes, secrets, GDPR, deletion and export
- [`docs/BACKUP.md`](docs/BACKUP.md) — what is backed up, encryption, and how to restore
- [`docs/MATTERS.md`](docs/MATTERS.md) — how conversations get filed, and why it never guesses hard
- [`docs/DOCUMENTS.md`](docs/DOCUMENTS.md) — extraction, tracked changes, and document versions
- [`docs/GOOGLE_SETUP.md`](docs/GOOGLE_SETUP.md) — **the Google Cloud setup, start here**
- [`docs/ACTIONS.md`](docs/ACTIONS.md) — what the assistant may do to the mailbox, and what it must ask first
- [`docs/MCP.md`](docs/MCP.md) — using it from Claude Code, Desktop, or your phone
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — phases, what is done and what comes next

## Layout

```
app/
  core/       configuration, encryption, logging, startup checks
  db/         SQLAlchemy models and session handling
  gmail/      OAuth, API client, MIME parser, address logic
  services/   sync engine, ingest, storage, search, accounts,
              access control, backups, local scheduler
  api/        FastAPI routes and schemas
  cli.py      operational commands
alembic/      migrations (the only way the schema is ever created)
tests/        unit + integration, run against real PostgreSQL
```
