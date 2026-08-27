# Data model

Two rules govern this schema:

1. **Raw memory is never lossy.** What Gmail gave us is stored well enough that
   no feature ever needs to re-fetch a message to answer a question.
2. **No dead schema.** Only tables the code actually reads or writes exist.
   Everything below marked *planned* arrives in the migration that first uses it.

## Implemented (migration `0001_initial`)

### `mailbox_account` — one authorised Gmail mailbox

| Column | Notes |
|---|---|
| `id` | UUID |
| `email`, `display_name`, `google_sub` | identity of the consenting account |
| `oauth_refresh_token_enc` | **encrypted** (Fernet); the key lives outside the database |
| `oauth_access_token_enc`, `oauth_token_expiry`, `oauth_scopes` | refreshed automatically |
| `sync_start_date` | per-mailbox override of the global start date |
| `is_active` | deactivating stops syncs without deleting anything |

A Workspace user with several aliases is **one** account with several
`mailbox_address` rows. Several *users* are several accounts, each with its own
OAuth grant.

### `mailbox_address` — the addresses that are mine

`(account_id, address)` unique. `source` records where it came from:
`primary`, `send_as` (imported from Gmail settings) or `manual`. This table is
what makes "my message vs. theirs" decidable.

### `sync_state` — the resumable cursor, one row per mailbox

`last_history_id`, `initial_sync_completed_at`, `initial_sync_page_token`,
`last_sync_at`, `total_messages_synced`.

Deliberately separate from `mailbox_account`: resetting a sync must never risk
touching stored credentials, and this row is written on every batch.
Deleting the row means "resync from scratch".

### `sync_run` — the operational history

Kind (`initial`/`incremental`/`backfill`), status
(`running`/`completed`/`partial`/`failed`), timestamps, counts of messages seen,
created, updated, skipped, threads touched, attachments created, the history-id
span, and any error. A run is committed as `running` before work starts, so a
process that dies mid-sync leaves evidence.

### `email_thread` — a Gmail conversation

`(account_id, gmail_thread_id)` unique. Subject, snippet, message count, first
and last message timestamps, and the direction of the most recent message —
the raw material for "who owes whom a reply" in phase 4.

### `email_message` — one message

Gmail ids (`gmail_message_id` unique per account, `gmail_thread_id`,
`history_id`); RFC 5322 threading (`rfc822_message_id`, `in_reply_to`,
`references`); subject, sender name and address; `account_address` (which of my
addresses was used) and `direction`; `sent_at` (the `Date:` header) **and**
`internal_date` (Gmail's own timestamp, authoritative for ordering because a
`Date:` header can be wrong or forged); `body_text`, `body_html`, `snippet`;
`labels`; `size_estimate`; `has_attachments`; `raw_headers` (JSONB, the headers
worth keeping); `content_hash`.

`search_vector` is a **generated column** maintained by PostgreSQL:

```sql
setweight(to_tsvector('public.sk_unaccent', coalesce(subject, '')),      'A')
|| setweight(to_tsvector('public.sk_unaccent', coalesce(from_address,'')),'B')
|| setweight(to_tsvector('public.sk_unaccent', coalesce(body_text, '')),  'C')
```

It cannot drift from the row it describes, because the database computes it.

### `email_participant` — one address on one header line

`kind` ∈ `from`, `to`, `cc`, `bcc`, `reply_to`, `delivered_to`; address,
display name, position, `is_own`, optional `contact_id`. Normalised into rows
rather than kept as header text so "every message involving this person" is an
index lookup.

### `contact` / `contact_email` — people

`contact` holds a canonical address, display name, domain, `is_own`,
first/last seen, message count, `needs_review`. `contact_email` maps further
addresses onto the same person, so identity merging in phase 3 does not need a
migration.

### `attachment` / `attachment_blob` — files

`attachment` is the file *as it appeared in one message*: `part_id` (the MIME
path — stable, unlike Gmail's rotating `attachmentId`), filename, MIME type,
size, `content_id`, `is_inline`, `download_status`
(`pending`/`downloaded`/`skipped_too_large`/`failed`).

`attachment_blob` is the bytes, once, keyed by `sha256`: size, MIME type,
storage backend and key. Extracted text, summary and embeddings will hang off
the *blob* in phase 3, so a document circulated twenty times is parsed once.

### `audit_log` — append-only

When, who (`system`/`user`/`agent`), what action, which entity, which mailbox,
a human-readable summary, JSONB details, result, whether it was automatic, and
a correlation id. Every sync run writes one. From phase 4, so does every Gmail
action and every approval.

## Planned

### Phase 2 — matters and clients

- `company` — legal entity: name, registration number, domains.
- `client` — a company or person you act for; retention policy; status.
- `matter` — a case or file: reference, title, client, status, opened/closed.
- `matter_link` — many-to-many from a matter to threads, messages, attachments
  and contacts, each with `confidence` and `needs_review`. A conversation can
  belong to several matters; nothing is force-fitted into one.

Matters are proposed, not imposed: below a confidence threshold the link is
flagged `needs_review` and surfaced for confirmation rather than silently made.

### Phase 3 — memory, documents, AI accounting

- `memory` — durable structured knowledge: subject, client, matter, statement,
  evidence (message and attachment ids), confidence, valid-from, superseded-by.
  Facts are superseded, never overwritten, so history stays auditable.
- `note` — free-form human notes attached to any entity.
- `document_text` — extracted text per **blob**, with extraction status and
  method.
- `embedding` — `pgvector` vectors for messages and document chunks, with the
  model that produced them, so a model change can be re-indexed selectively.
- `ai_usage` — provider, model, input/output tokens, estimated cost, operation
  type, related entity. Written by the AI layer from its first call, so cost
  per day / per e-mail / per briefing is a query, not an estimate.

### Phase 4 — tasks, follow-ups, actions

- `task` — title, description, client, matter, due date, status, source.
- `follow_up` — remind me about this thread on this date; status and note.
- `event` — deadlines and dates extracted from communication.
- `pending_action` — `action_type`, description, target, requested_by,
  `created_at`, status (`pending`/`approved`/`rejected`/`executed`/`failed`),
  result. Nothing that sends, deletes or materially modifies mail happens
  without a row here moving to `approved`.

## Conventions

- UUID primary keys everywhere — safe to merge across environments, and they
  do not leak volume.
- All timestamps `TIMESTAMP WITH TIME ZONE`, stored in UTC. Presentation
  converts to Europe/Bratislava.
- Enumerations are short strings with `CHECK` constraints: readable in SQL,
  extendable without a type migration.
- Deletes cascade from `mailbox_account` downward, so removing a mailbox
  removes its mail. `attachment.blob_id` is `RESTRICT`: shared bytes are never
  orphaned by deleting one message — blob reclamation is a deliberate,
  audited GDPR operation.
