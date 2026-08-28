# Roadmap

Each phase ends with something that works and is tested. Nothing is marked done
before it runs.

## Phase 1 — durable Gmail ingest ✅ done

The foundation everything else stands on: get mail into our own store,
reliably, and prove it.

- [x] Project, configuration, structured logging, startup verification
- [x] PostgreSQL schema + Alembic migrations (`alembic check` clean)
- [x] Google OAuth 2.0, read-only scopes, tokens encrypted at rest
- [x] Multiple addresses per mailbox; send-as aliases imported automatically
- [x] Configurable start date, enforced as a hard floor
- [x] Initial sync: paged, checkpointed, resumable
- [x] Incremental sync via `historyId`, with automatic recovery when it expires
- [x] Idempotency via `content_hash` + unique constraints
- [x] Attachment metadata; bytes deduplicated by SHA-256
- [x] Full-text search that ignores Slovak diacritics, plus structured filters
- [x] Audit log for every sync run
- [x] REST API and operational CLI
- [x] 135 tests: MIME parsing, charsets, addresses, idempotency, pagination,
      attachment dedup, failure isolation, search, HTTP layer

**Not in phase 1, deliberately:** no AI, no sending, no deleting, no archiving.

## Phase 2 — production, matters, clients

*Running locally for now — cloud costs deferred until the system has earned
them. Everything below that is not a deployment step works today on your own
machine.*

- [x] **API-key authentication**, hashed at rest, revocable, audited; forced on
      outside development
- [x] **OAuth callback state verification** — one-time token, 15-minute life
- [x] Orphaned-contact reclamation; unreferenced blobs reported for review
- [x] **Encrypted backups** (AES-256-GCM) to disk or Google Drive, with
      retention, verification, and a restore proven by test
- [x] **Local scheduler** — sync on an interval, one backup a day, no broker
- [x] Docker Compose stack for running the whole thing on your own machine
- [ ] Deploy to Cloud Run; Cloud SQL or Supabase; GCS attachments; Secret Manager
- [ ] Cloud Scheduler → `/api/v1/jobs/sync-all`
- [ ] Gmail push via Pub/Sub, with the scheduled pull kept as the safety net
- [x] **`company`, `client`, `matter`, `matter_link`** with confidence scores
- [x] **Automatic filing** by five deterministic rules — never silent creation;
      low confidence sets `needs_review` and lands in a review queue
- [ ] Client-scoped export and erasure endpoints
- [ ] Backups and a restore drill that is actually performed

## Phase 3 — documents, memory, AI

- [x] **Text extraction per blob**: PDF, DOCX, XLSX, CSV, HTML, TXT —
      deterministic parsing, no model involved; scans flagged `needs_ocr`
- [x] **Full-text search inside documents**, with matching-passage highlights
- [x] **Word tracked changes and comments** read correctly — insertions kept,
      deletions recorded separately, comments indexed
- [x] **Document version families** with diffs, and an audit signal when a known
      file arrives with new content
- [ ] OCR for scanned documents (`needs_ocr` marks the queue)
- [ ] `AIProvider` abstraction; Claude as the primary provider
- [ ] Model routing — cheap models for classification and short summaries,
      the strong model only for real analysis
- [ ] `ai_usage` accounting from the very first call: provider, model, tokens,
      estimated cost, operation
- [ ] Structured outputs validated against schemas by the backend, never
      free-text parsing
- [ ] `pgvector` embeddings for messages and document chunks
- [ ] Hybrid search: structured + full-text + semantic
- [ ] `memory` — durable structured knowledge, superseded rather than
      overwritten, every fact carrying its evidence

## Phase 4 — assistant behaviour

- [ ] E-mail classification: type, importance, whether action is required
- [ ] "Waiting for reply" as a classification, not a rule — a newsletter or an
      automatic receipt is not an unanswered question
- [ ] `task`, `follow_up`, `event`
- [ ] Daily briefing, 08:00 Europe/Bratislava on working days:
      needs your reaction / waiting on the other side / follow-ups due today /
      new important documents / no action needed
- [x] **`pending_action` approval workflow** — every action proposed, decided
      and audited; proposals expire after 7 days
- [x] **Gmail write actions by risk tier** — automatic (labels, drafts,
      unarchive, restore), configurable (archive), approval (bin, permanent
      delete, send). No setting promotes an action out of the approval tier
- [x] System-label guard, so "apply a label" can never become an unaudited bin
- [ ] Sending: the action type exists in the approval tier, but no tool creates
      one yet

## Phase 5 — access from Claude

*Pulled forward: with an MCP server, Claude reasons over this memory using the
subscription you already pay for. No API key, no token bill. That makes it the
cheapest way to find out whether the system is useful at all.*

- [x] **MCP server with 14 read-only tools** — mail, threads, documents,
      versions, clients, matters, review queue, activity, sync, audit
- [x] Runs over stdio (Claude Code) or streamable HTTP (remote connector,
      reachable from a phone through a tunnel)
- [x] A test asserts no tool can send, delete, archive or draft
- [ ] Write tools (`create_task`, `create_followup`, `draft_email`,
      `request_archive`, `request_delete`) — these create pending actions and
      never act, so they ship with the approval workflow in phase 4
- [ ] Authentication on the HTTP transport, before any tunnel carries real mail
- [ ] Optional web UI for review and approval queues

## Open decisions

| Decision | Needed by | Notes |
|---|---|---|
| Cloud SQL vs Supabase | when moving off this machine | Cost vs. a single processor for privileged data. Deferred by choice: running locally until the system proves itself |
| GCP region | when moving off this machine | `europe-west1` or `europe-central2` |
| Which mailboxes and domains | before first real sync | Drives address configuration |
| Start date for real mail | before first real sync | Hard floor; earlier backfill is deliberate |
| Embedding provider | phase 3 | Multilingual quality vs. keeping content in fewer hands |
| Sending client mail to Anthropic | phase 3 | A data-processing decision to record before switching on |
