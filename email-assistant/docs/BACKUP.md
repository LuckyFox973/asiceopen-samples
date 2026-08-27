# Backups

## What is backed up, and what is not

**The database, every run.** It holds the assistant's whole structured memory:
threads, participants, contacts, sync cursors, audit history. Nothing else can
reconstruct it.

**Attachment bytes: off by default.** They are content-addressed copies of
files that still exist in Gmail, so they can be re-fetched from the source as
long as the messages are known — and the messages are in the database backup.
Uploading gigabytes nightly to protect data that is already safe elsewhere is a
poor trade. `--include-attachments` is there for when you want it anyway.

Measured on real ingest: about **7 KB of database per message**, so 10 000
messages is roughly 67 MB before compression. A database-only archive of a
sizeable mailbox stays comfortably small.

## Encryption is not optional

A dump of this database contains every client e-mail plus the encrypted OAuth
tokens. `BACKUP_ENCRYPTION_KEY` has no default and no bypass: without it,
`backup run` refuses and writes nothing.

Archives use chunked AES-256-GCM with a per-archive key derived through
HKDF. Each chunk carries its index and a final-chunk flag as associated data,
so a **truncated or reordered archive fails to decrypt** rather than silently
yielding partial data. Tests cover a flipped byte, a truncated file, trailing
data, and the wrong key.

Two rules about the key:

1. **Keep it off the Drive you back up to.** A key stored beside the archive
   protects nothing.
2. It is deliberately *not* `TOKEN_ENCRYPTION_KEY`. A restore can then be
   handed to someone without also handing over the key that unlocks live
   mailbox credentials.

Losing the key means losing the backups. There is no recovery path — that is
what makes the encryption worth anything.

## Google Drive

```bash
BACKUP_ENABLED=true
BACKUP_BACKEND=gdrive
BACKUP_GDRIVE_FOLDER=EmailAssistantBackups
BACKUP_ENCRYPTION_KEY=<from: python -m app.core.crypto keygen>
```

The Drive scope requested is **`drive.file`** — access to files this
application itself creates, and to nothing else in your Drive. Your client
documents stay invisible to it.

That scope is only requested when `BACKUP_BACKEND=gdrive`. If a mailbox was
already authorised without it, re-run the consent flow:

```bash
python -m app.cli auth-url     # approve again; Drive permission now included
```

Running a Drive backup against a mailbox that lacks the scope fails with that
instruction rather than a cryptic API error.

## Commands

```bash
python -m app.cli backup run                        # take one now, then prune
python -m app.cli backup run --include-attachments   # database + stored files
python -m app.cli backup list
python -m app.cli backup verify <archive>            # prove it decrypts
python -m app.cli backup restore <archive> --into ./restored.dump
```

`BACKUP_RETENTION` (default 14) archives are kept; older ones are deleted after
each successful run. Pruning and creation are both written to the audit log.

## Restoring

`backup restore` stops at the decrypted `pg_dump` payload. Loading it into a
live database is destructive, so that step is a command you run knowingly, with
the target named:

```bash
python -m app.cli backup restore <archive> --into ./restored.dump

createdb email_assistant_restored
pg_restore --no-owner --dbname email_assistant_restored ./restored.dump
```

Point `DATABASE_URL` at the restored database, run `python -m app.cli stats`,
and compare before replacing anything.

**Test the restore, don't assume it.** A backup nobody has ever decrypted is a
hope, not a backup. `backup verify` proves an archive decrypts; the integration
suite goes further and restores a real dump into a fresh database, then asserts
the message is there, its attachment metadata survived, and full-text search
still finds it.

## Automation

`python -m app.cli daemon` takes one backup a day at `SCHEDULER_BACKUP_HOUR`
in `TIMEZONE`, then prunes. A failed backup does **not** mark the day as done,
so the next cycle retries rather than silently skipping a day.

On a server later, the same work becomes a Cloud Scheduler job hitting an HTTP
endpoint. The code does not change.

## What this does not protect against

Stated plainly:

- **A lost encryption key.** The archives become unreadable. Keep the key in a
  password manager, not only on the machine that makes the backups.
- **Deleting the Google account.** Backups live in the same account whose mail
  they protect. A second copy elsewhere — an external disk is enough — removes
  that single point of failure.
- **Silent corruption over time.** `backup verify` reads an archive end to end;
  run it on an old archive occasionally, not just a fresh one.
