# Setup

## 1. Local development

Requirements: Python 3.11+, PostgreSQL 16, and `uv` (or plain `pip`).

```bash
git clone <this repository>
cd email-assistant

make install                    # virtualenv + dependencies
make dev-db                     # roles, databases, extensions

cp .env.example .env
python -m app.core.crypto keygen        # paste output into TOKEN_ENCRYPTION_KEY

make migrate                    # create the schema
make test                       # 135 tests, needs the test database
make check                      # configuration report
```

`make dev-db` needs `pg_trgm` and `unaccent`. `pgvector` is only required from
phase 3; on Debian/Ubuntu install `postgresql-16-pgvector`.

See the system working before any Google setup exists:

```bash
make seed          # loads sample mail through the real parser and ingest
make run           # http://localhost:8000/docs
python -m app.cli search "kasačná sťažnosť"
```

`make seed` replaces only the Gmail transport with a fixture. The parser,
ingest, attachment store and search index are the production ones.

## 2. Google Cloud project — needs you

> **Follow [GOOGLE_SETUP.md](GOOGLE_SETUP.md) instead of this section.** It is
> the complete, do-it-once version: it settles the scopes before you open the
> consent screen, because adding one afterwards means re-authorising every
> mailbox. What follows is the short form.

These steps require your Google account; I cannot do them for you.

### 2.1 Create the project

1. <https://console.cloud.google.com/projectcreate>
2. Name it something like `email-assistant-prod`.
3. Note the **project ID**.

### 2.2 Enable the APIs

In *APIs & Services → Library*, enable:

- **Gmail API** (required)
- **Cloud Pub/Sub API** (phase 2, for push notifications)

### 2.3 Configure the OAuth consent screen

*APIs & Services → OAuth consent screen*

- User type: **Internal** if the Workspace account is in your own organisation
  — this avoids Google verification entirely. Use External only if it is not.
- App name, support e-mail, developer contact.
- **Scopes** — add exactly these, no more:

  ```
  https://www.googleapis.com/auth/gmail.readonly
  https://www.googleapis.com/auth/gmail.settings.basic
  https://www.googleapis.com/auth/userinfo.email
  openid
  ```

  `gmail.settings.basic` is what lets the assistant read your send-as aliases,
  which is how it knows which addresses are yours. There is no write scope in
  phase 1, and none will be added without your explicit decision.

### 2.4 Create the OAuth client

*APIs & Services → Credentials → Create credentials → OAuth client ID*

- Type: **Web application**
- Authorised redirect URIs:
  - `http://localhost:8000/api/v1/auth/google/callback` (development)
  - `https://<your-cloud-run-url>/api/v1/auth/google/callback` (production)

Copy the client ID and secret into `.env`:

```
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/api/v1/auth/google/callback
```

Never commit these. `.env` is gitignored.

### 2.5 Connect a mailbox

```bash
make run
python -m app.cli auth-url        # prints the consent URL
```

Open the URL, sign in with the Workspace account, approve. The callback stores
the encrypted refresh token and imports your send-as aliases. Then:

```bash
python -m app.cli accounts
python -m app.cli sync you@example.com --mode initial
python -m app.cli stats
```

The first run stops at `SYNC_MAX_MESSAGES_PER_RUN` and reports `partial`; run
it again to continue from the checkpoint.

## 3. Choosing the start date

`SYNC_START_DATE` in `.env` (or per mailbox via
`python -m app.cli sync <mailbox> --start-date YYYY-MM-DD`) is a hard floor.
Nothing earlier is fetched or stored, even if Gmail's history API offers it.

Moving the date **later** does not delete anything already stored. Moving it
**earlier** does not backfill automatically — that is a deliberate operation,
so a mistyped date can never quietly pull in years of mail.

## 4. Running it continuously, on your own machine

Two ways. Either works; Docker is fewer moving parts.

### With Docker

```bash
cp .env.example .env          # fill in, including BACKUP_ENCRYPTION_KEY
make up                       # database + migrations + API + scheduler
make logs
make down                     # stops; volumes are kept
```

Ports bind to `127.0.0.1` only — the API holds mailbox contents and must not be
reachable from the local network.

### Without Docker

```bash
make run       # API on :8000
make daemon    # sync every SCHEDULER_SYNC_INTERVAL_MINUTES, backup daily
```

To keep the scheduler running across logins on macOS, a `launchd` agent calling
`python -m app.cli daemon` is the native way; `caffeinate -s` prevents sleep
while it runs.

**The limitation to be clear about:** none of this runs while the machine is
off. Overnight synchronisation and an early-morning briefing need a server.
Everything else works exactly the same locally.

## 5. Backups

```bash
python -m app.core.crypto keygen        # BACKUP_ENCRYPTION_KEY — store it in a
                                        # password manager too, not only here
python -m app.cli backup run
python -m app.cli backup list
python -m app.cli backup verify <archive>
```

For Google Drive, set `BACKUP_BACKEND=gdrive` and re-run the consent flow — the
Drive scope is only requested when Drive backups are enabled. Full details,
including how to restore, are in [BACKUP.md](BACKUP.md).

## 6. Production deployment (when it is time)

The hosting decision is still open; see *Architecture → Hosting*. The intended
Google Cloud shape:

```bash
# Database — Cloud SQL PostgreSQL 16 in an EU region
gcloud sql instances create email-assistant-db \
  --database-version=POSTGRES_16 --tier=db-g1-small --region=europe-west1
gcloud sql databases create email_assistant --instance=email-assistant-db

# Attachment bucket, uniform access, EU
gcloud storage buckets create gs://<project>-attachments \
  --location=europe-west1 --uniform-bucket-level-access

# Secrets
python -m app.core.crypto keygen | \
  gcloud secrets create token-encryption-key --data-file=-
gcloud secrets create google-client-secret --data-file=-

# Deploy
gcloud run deploy email-assistant \
  --source . --region=europe-west1 --no-allow-unauthenticated \
  --set-env-vars APP_ENV=production,ATTACHMENT_BACKEND=gcs,... \
  --set-secrets TOKEN_ENCRYPTION_KEY=token-encryption-key:latest,...

# Scheduled sync every 15 minutes
gcloud scheduler jobs create http email-assistant-sync \
  --schedule="*/15 * * * *" --time-zone="Europe/Bratislava" \
  --uri="https://<service-url>/api/v1/jobs/sync-all" --http-method=POST \
  --oidc-service-account-email=<scheduler-sa>
```

Migrations run as a separate step before traffic shifts, never automatically on
container start — an autoscaled service must not race itself into the schema.

### Production configuration checklist

`python -m app.cli check` verifies these and refuses to stay quiet about them:

- `TOKEN_ENCRYPTION_KEY` is a generated Fernet key, not a passphrase
- `JOB_AUTH_TOKEN` is set, so scheduler endpoints are not open
- `ATTACHMENT_BACKEND=gcs` — Cloud Run's filesystem is ephemeral
- `GOOGLE_OAUTH_REDIRECT_URI` is HTTPS
- `DATABASE_URL` does not point at localhost

## 7. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `TOKEN_ENCRYPTION_KEY is not set` | Run `python -m app.core.crypto keygen` and put it in `.env` |
| `Stored token could not be decrypted` | The encryption key changed. Re-run the consent flow |
| Sync reports `partial` | Per-run cap reached; run again to continue from the checkpoint |
| `historyId ... no longer available` | Normal after a long gap; the engine falls back to a date-bounded pass automatically |
| Tests skipped | The test database is unreachable; check `TEST_DATABASE_URL` |
| `BACKUP_ENCRYPTION_KEY is not set` | Deliberate: a backup holds every stored message and is never written unencrypted |
| `authorised without the Drive scope` | Set `BACKUP_BACKEND=gdrive`, then re-run `auth-url` and approve again |
| `pg_dump is not on PATH` | macOS: `brew install libpq && brew link --force libpq` |
| `cannot insert a non-DEFAULT value into column "search_vector"` | The model and migration disagree; run `alembic check` |
