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
- [ ] `company`, `client`, `matter`, `matter_link` with confidence scores
- [ ] Automatic matter proposals — never silent creation; low confidence sets
      `needs_review`
- [ ] Client-scoped export and erasure endpoints
- [ ] Backups and a restore drill that is actually performed

## Phase 3 — documents, memory, AI

- [ ] Text extraction per blob: PDF, DOCX, XLSX, TXT; OCR for scans
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
- [ ] `pending_action` approval workflow
- [ ] Gmail write scopes and actions, by risk tier:
      - automatic: safe labels, drafts, internal tasks and memory
      - configurable: archive
      - always requires your approval: send, delete, permanent delete, any
        material modification of existing correspondence

## Phase 5 — access from Claude and Cowork

- [ ] MCP server exposing: `search_memory`, `search_emails`, `get_thread`,
      `get_matter`, `get_client`, `get_tasks`, `create_task`, `create_followup`,
      `get_daily_briefing`, `search_documents`, `get_attachment_text`,
      `draft_email`, `request_archive`, `request_delete`
- [ ] Read tools return data; write tools create pending actions, never act
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
