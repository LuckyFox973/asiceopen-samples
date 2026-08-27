# Security and data protection

This system will hold privileged legal correspondence. The controls below are
built in from phase 1, not retrofitted.

## Authentication to Google

- **OAuth 2.0 authorisation code flow.** You consent in your own browser. The
  application never sees, requests or stores a Google password. Do not send
  credentials in chat to anyone, including me.
- **Minimum scopes.** Phase 1 requests read-only access:

  | Scope | Why |
  |---|---|
  | `gmail.readonly` | read messages, threads, attachments |
  | `gmail.settings.basic` | read send-as aliases, to know which addresses are yours |
  | `userinfo.email`, `openid` | identify which mailbox consented |

  There is **no** `gmail.send`, `gmail.modify` or `mail.google.com`. The
  application is not merely restrained from writing to your mailbox — it is not
  authorised to. A test asserts this, so a write scope cannot appear by
  accident.

- **Adding write scopes later** is a deliberate decision that ships together
  with the approval workflow (phase 4). Adding the scope alone changes nothing
  the assistant is permitted to do on its own.

## Secrets

| Secret | Development | Production |
|---|---|---|
| Google client secret | `.env` (gitignored) | Secret Manager |
| `TOKEN_ENCRYPTION_KEY` | `.env` | Secret Manager |
| Database password | `.env` | Secret Manager / IAM auth |
| `JOB_AUTH_TOKEN` | unset (endpoints local only) | Secret Manager, required |

`.gitignore` excludes `.env`, `*.pem`, `*.key`, `client_secret*.json`,
`service-account*.json`, `credentials.json` and `token.json`. **No secret has
been or will be committed.**

Startup verification refuses to be quiet in production about a missing
encryption key, a passphrase used where a real key belongs, an unset job token,
plain-HTTP redirects, local attachment storage, or a localhost database.

## Tokens at rest

Refresh and access tokens are encrypted with Fernet (AES-128-CBC +
HMAC-SHA256) before they touch the database. The key lives outside the
database, so a database dump alone does not grant mailbox access.

In development a passphrase is accepted and derived into a key, to keep local
friction low. In production that is reported as a problem — use
`python -m app.core.crypto keygen`.

Rotating the key invalidates stored tokens; each mailbox re-runs the consent
flow. Stored mail is unaffected.

## Least privilege in operation

- Phase 1 can only read. Sending, deleting and archiving are not implemented
  and not authorised.
- The scheduler endpoint `/api/v1/jobs/sync-all` requires `X-Job-Token`. In
  production an unset token is a startup error, never an open door.
- Cloud Run should be deployed `--no-allow-unauthenticated`, with the scheduler
  calling it as an authenticated service account.
- The database should be reachable only over a private IP or the Cloud SQL
  connector — never a public address with a password.

## Audit

`audit_log` is append-only and records what happened, when, on whose behalf,
against which entity, whether it was automatic, and the outcome. Every sync run
writes an entry. From phase 4 every Gmail action and every approval decision
does too.

The design rule: **the assistant never makes a consequential change quietly.**
Anything that leaves the system or alters your mailbox is either explicitly
approved by you or recorded in a way you can review afterwards — and, for
sending and deletion, both.

## GDPR

**Where the data is.** One PostgreSQL database and one object store, both in an
EU region you choose. No third-party search index, no vector SaaS, no copies in
a cache you cannot enumerate. From phase 3, e-mail content is also sent to
Anthropic for analysis — that is a data-processing decision to record before it
is switched on, and it is why the AI layer is a separate phase behind an
explicit toggle.

**Minimising duplicates.** Attachment bytes are content-addressed: one copy per
distinct file, referenced by every message that carried it. There is exactly
one object to export or erase.

**Erasure.** Deletes cascade from `mailbox_account` through threads, messages,
participants and attachment rows. `attachment.blob_id` is `RESTRICT` on
purpose: shared bytes are never orphaned by deleting one message. Reclaiming an
unreferenced blob is a deliberate, audited operation, not a side effect.

Client- and matter-scoped erasure and export arrive with those entities in
phase 2 — the model is built so that "delete everything for this client" and
"export everything for this matter" are queries, not archaeology.

**Retention.** Retention policy per client is part of the phase 2 model. Until
then the policy is explicit and simple: nothing before `SYNC_START_DATE` is
ever fetched.

**Access.** Today: whoever holds the database credentials. Before this holds
real client mail, add authentication to the read API — it is deliberately
listed as a phase 2 prerequisite in the roadmap rather than left implicit.

## Known limitations in phase 1

Stated plainly rather than buried:

1. **The read API has no user authentication.** Fine on localhost; not fine on
   a public URL. Deploy `--no-allow-unauthenticated` until phase 2 adds it.
2. **No encryption at rest beyond the platform's own.** Cloud SQL and GCS
   encrypt by default; message bodies are not separately encrypted, because
   full-text search requires readable text. Customer-managed keys (CMEK) are
   the right answer if the threat model requires more.
3. **No rate limiting** on the API.
4. **`raw_headers` keeps routing headers** — sender IPs and mail paths. This is
   deliberate for forensics but is personal data; it is covered by the same
   deletion cascade.
