# Architecture

## The principle

Claude is not where the memory lives. The database is. Every AI feature added
later reads from and writes to that database, so the assistant's knowledge is
independent of any conversation, context window, or model version.

```
Google Workspace / Gmail
        │  Gmail API (read-only in phase 1)
        ▼
Email Assistant backend  ──────────────┐
  FastAPI + sync engine                │
        │                              │
        ▼                              ▼
PostgreSQL                        Object storage
  mail, threads, contacts,          attachment bytes,
  attachments metadata,             one copy per distinct
  audit, (later) memory             file (SHA-256)
        │
        ▼
AI layer (phase 3)  ── AIProvider abstraction, model routing, usage accounting
        │
        ▼
MCP server / HTTP API (phase 5)  ──▶  Claude, Cowork, future UI
```

The proposed flow in the brief was right; the one change worth making is that
**object storage sits beside the database rather than inside it**. Attachments
in PostgreSQL would make backups enormous and restores slow, for no benefit.

## Chosen stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11 | Requested; best Gmail/AI library support |
| API | FastAPI + Pydantic v2 | Requested; schema validation is the same tool used for AI structured output later |
| Database | PostgreSQL 16 | Requested. One engine covers relational data, full-text and (with `pgvector`) semantic search — no separate search cluster |
| ORM / migrations | SQLAlchemy 2.0 + Alembic | Requested; migrations are the only way schema is ever created, including in tests |
| Blob storage | Content-addressed store, local in dev, GCS in production | Deduplication and a single place to delete or export a file |
| Background work | A local loop now; Cloud Scheduler → authenticated HTTP endpoint later | No Redis, no Celery, no broker to operate |
| Runtime | Your machine now, Cloud Run when it earns it | Defers the bill; the code is identical either way |
| Backups | Encrypted archives to disk or Google Drive | The account already exists and has space |
| Secrets | `.env` in dev, Secret Manager in production | Nothing secret is ever committed |
| Tests | pytest against real PostgreSQL | The schema uses generated columns and GIN indexes; SQLite would test a fiction |

### What was deliberately *not* used

- **Redis / Celery** — the only recurring work is "sync every N minutes" and
  "produce a briefing each morning". A scheduler calling an HTTP endpoint does
  that with nothing to run out of memory at 3am.
- **Kubernetes** — one stateless container.
- **A separate search engine** — PostgreSQL full-text handles phase 1, and
  `pgvector` handles phase 3 in the same database and the same transaction.
- **A message queue for ingest** — Gmail's own `historyId` cursor already gives
  at-least-once delivery with resumability. Adding a queue would duplicate it.

### Hosting: local first, deliberately

The system runs on your own machine for now. That defers every cloud cost until
it has proved itself, and nothing about it is a dead end: the backend is a
container, the database is plain PostgreSQL, and the scheduler is a loop that a
Cloud Scheduler job replaces one-for-one. Moving to a server is configuration,
not a rewrite.

What local costs you, stated plainly: **it only runs while the machine is on.**
Overnight synchronisation and an early-morning briefing cannot work on a
sleeping laptop. Everything else — ingest, search, memory, backups — is
unaffected. When that limitation starts to bite, the comparison below is the
decision to make.

### Hosting: the decision, for when it is time

Two viable options, both plain PostgreSQL so the code is identical:

| | Cloud SQL (PostgreSQL 16) | Supabase |
|---|---|---|
| Data location | Your GCP project, EU region of choice | Supabase EU region |
| Processors involved | Google only | Google + Supabase |
| Rough cost | ~€9/mo (`db-f1-micro`) to ~€25/mo (`db-g1-small`) | Free tier, or ~$25/mo Pro |
| pgvector | Available | Pre-installed |
| Effort | Configure a private IP or the Cloud SQL connector | Connection string and done |

For mail that includes privileged client communication, keeping everything in a
single GCP project you control is the cleaner story for a data-processing
record — one processor, one region, one audit trail. Supabase is materially
easier and cheaper to start with.

Two facts worth carrying into that decision. Storage is **not** the cost:
measured on real ingest, a message costs about 7 KB of database, so 10 000
messages is ~67 MB — a rounding error against either plan's included storage.
And a GCP project will be needed regardless once Gmail push notifications are
wanted, because `users.watch` publishes only to Google Cloud Pub/Sub.

Recommended region either way: `europe-west1` or `europe-central2` — EU data
residency and low latency from Bratislava.

## How a sync works

### Initial

1. `users.messages.list` with `after:<start date> -in:chats`.
2. For each id: `users.messages.get(format=full)`.
3. Parse MIME → subject, participants, bodies, attachment parts.
4. Resolve direction against the mailbox's known addresses.
5. Drop anything older than the start date, even if Gmail returned it.
6. Upsert thread → message → participants → attachments → contacts.
7. **Commit after each page** and store the next page token, so an interruption
   costs one page, not the whole run.
8. When the last page is done, record the mailbox's current `historyId` as the
   cursor for incremental syncs.

### Incremental

1. `users.history.list(startHistoryId=<cursor>)`.
2. Collect message ids from `messagesAdded`, `labelsAdded`, `labelsRemoved`,
   de-duplicated — one message touched three ways is fetched once.
3. Ingest exactly as above.
4. Advance the cursor.

If Gmail has aged the cursor out (HTTP 404 — it keeps roughly a week), the
engine falls back to a date-bounded full pass automatically. Because ingest is
idempotent, this costs time and nothing else.

### Why re-ingest is safe

Every message carries a `content_hash` over the fields that are persisted. On
re-ingest:

- hash unchanged → nothing is written;
- hash changed → the row is updated in place;
- message unknown → inserted.

`(account_id, gmail_message_id)` is unique, so even a concurrent double-run
cannot produce a duplicate.

### Failure isolation

Each message is ingested inside its own **savepoint**. One malformed message
rolls back alone; the rest of the batch commits. Every failure is counted and
recorded on the sync run rather than silently dropped.

## Identity: whose message is this?

The assistant must never confuse your words with the other side's.

- The mailbox's addresses come from `users.settings.sendAs` and are refreshed
  on every run, so a newly added domain is picked up automatically.
- Matching is alias-aware: `peter+dane@foxgroup.sk` matches `peter@foxgroup.sk`;
  dots and `googlemail.com` are folded **only** for consumer Gmail, because
  dots are significant in a Workspace custom domain.
- Direction: sender is mine and at least one recipient is not → `outbound`;
  everyone is mine → `internal`; sender is not mine → `inbound`.
- The receiving alias is taken from `Delivered-To` first (the reliable signal),
  then `To`/`Cc`/`Bcc`. If nothing matches — a BCC or a list — the alias is
  recorded as unknown rather than guessed.

## Attachments

An attachment row records the file *as it appeared in one message*: name, MIME
type, size, part id, inline flag. The bytes live once, keyed by SHA-256. The
same PDF circulated twenty times is twenty rows and one stored object.

This is a cost decision and a GDPR one: there is exactly one place to look for,
export, or erase a given file. Originals are written once and never modified —
stored files are made read-only.

## Search

Phase 1 ships two deterministic modes, no LLM involved:

- **Structured**: sender, participant, direction, label, date range, has
  attachments, subject substring.
- **Full-text**: a generated `tsvector` column maintained by PostgreSQL itself,
  so it can never drift from the row. Subject is weighted above sender above
  body.

Slovak has no PostgreSQL stemmer, so the text search configuration
(`public.sk_unaccent`) folds diacritics without pretending to stem: searching
`kasacna staznost` finds *Kasačná sťažnosť*. Queries go through
`websearch_to_tsquery`, which supports quoted phrases, `OR` and `-exclusion`,
and never raises on malformed input.

Phase 3 adds `pgvector` embeddings for genuinely semantic recall. It ranks
*alongside* these modes rather than replacing them — exact lookups must stay
exact.

## Backups

Archives are encrypted before they leave the machine — chunked AES-256-GCM with
a per-archive key, so a truncated or reordered archive fails to decrypt rather
than yielding partial data. The database goes into every archive; attachment
bytes do not by default, because they are still in Gmail and re-fetchable.
Google Drive is the default remote, using the `drive.file` scope, which sees
only files this application created. See [BACKUP.md](BACKUP.md).

## Scheduling

`python -m app.cli daemon` runs sync on an interval and one backup a day, with
no broker and no worker pool. Each mailbox syncs in its own session, so one
failure never rolls back another's committed work, and a failed backup does not
mark the day done — the next cycle retries.

On a server this loop is replaced by Cloud Scheduler calling
`/api/v1/jobs/sync-all`. Same work, same code, different trigger.

## Production topology (when it is time)

```
Cloud Scheduler ──(OIDC)──▶ Cloud Run ──▶ Cloud SQL / Supabase
                               │
Gmail ──▶ Pub/Sub ──(push)─────┘──▶ Cloud Storage (attachments)
                               └───▶ Secret Manager (OAuth secret, token key)
```

Cloud Scheduler covers the baseline (every N minutes). Gmail push via Pub/Sub
is an optimisation for latency, added in phase 2; the scheduled pull remains as
the safety net, because a missed push must never mean missed mail.
