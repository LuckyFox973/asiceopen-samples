# Install it on your Mac

Nothing about Google here — that is [GOOGLE_CLOUD.md](GOOGLE_CLOUD.md), and it
can be done before or after this. You will need the Client ID and secret from
it at step 3.

---

## 1. Install everything

Open **Terminal** (⌘-Space, type *Terminal*) and paste:

```bash
curl -fsSL https://raw.githubusercontent.com/LuckyFox973/asiceopen-samples/claude/gmail-ai-assistant-system-u72j2z/email-assistant/scripts/bootstrap_macos.sh | bash
```

It installs Homebrew if you do not have it, then Python, PostgreSQL and the
project into `~/email-assistant`. It creates the database, generates your
encryption keys, and loads demo data. It may ask for your Mac password once —
that is Homebrew.

Run it twice and nothing breaks; every step checks before it acts.

## 2. Check it works

```bash
cd ~/email-assistant/email-assistant
./.venv/bin/python -m app.cli find "CMR duplicitne"
```

That searches *inside* a PDF in the demo data. A result means everything works.

> **Where things are now.** The project is at
> `~/email-assistant/email-assistant`. Its settings live in `.env` in that
> folder. Open it with `open -e .env`.

## 3. Add your Google credentials

Google gives you the OAuth client as a downloaded `client_secret*.json` rather
than something you can paste. This reads it for you:

```bash
cd ~/email-assistant/email-assistant
./.venv/bin/python -m app.cli import-credentials
```

It finds the newest download in `~/Downloads` (pass a path if it is elsewhere),
writes the client id and secret into `.env`, makes that file readable only by
you, and **checks the redirect URIs against what the application uses** — which
is what turns a baffling `redirect_uri_mismatch` during authorisation into a
sentence beforehand. The secret is never printed.

Delete the download afterwards; it still holds your client secret.

Then open `.env` and set the rest to match what you configured in Google:

```bash
open -e .env
```

```
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/api/v1/auth/google/callback

GMAIL_WRITE_ENABLED=true
GMAIL_ALLOW_PERMANENT_DELETE=false
GMAIL_AUTO_ARCHIVE=false

SYNC_START_DATE=2026-01-01
```

`SYNC_START_DATE` is a hard floor — nothing older is ever fetched. Start recent;
you can lower it later.

Save, then confirm the application will ask Google for exactly what you
configured:

```bash
./.venv/bin/python -m app.cli check
```

It prints the scope list one per line. **If it disagrees with what you saved in
Data Access, fix it now** — after authorising, fixing it means authorising again.

## 4. Connect the mailbox

Two Terminal windows.

**First**, leave this running:

```bash
cd ~/email-assistant/email-assistant
./.venv/bin/uvicorn app.main:app --port 8000
```

**Second**:

```bash
cd ~/email-assistant/email-assistant
./.venv/bin/python -m app.cli auth-url
```

Open the printed URL, sign in with the mailbox you want, approve. Google shows
exactly the scopes you configured — read that screen.

Approving is not optional and not instant: the browser has to come back to a
page saying the mailbox is connected. Until it does, nothing is connected.
`auth-url` refuses to print anything if the first window is not running, since
Google would redirect the browser into a closed port and the sign-in would be
lost at the last step.

Confirm before going on:

```bash
./.venv/bin/python -m app.cli accounts
```

Your address must appear. If the only line is `demo@example.invalid`, the
authorisation did not finish — that is seeded demo data, not your mailbox.
Remove it whenever you like with `./.venv/bin/python scripts/demo_seed.py --reset`.

## 5. Pull the mail in

Use your own address — the one `accounts` printed:

```bash
./.venv/bin/python -m app.cli sync you@yourdomain.sk --mode initial
```

It runs until the mailbox is caught up and prints a line per pass. Leave it.
A year of mail takes tens of minutes; you can stop it with Ctrl-C and start it
again with the same command, and it carries on where it stopped — nothing
already fetched is fetched twice.

Then pull the text out of the attachments:

```bash
./.venv/bin/python -m app.cli extract
```

This also runs until the queue is empty. Scanned PDFs are reported as
`need OCR` and stay unsearchable — OCR is not built yet.

To see exactly which files could not be read, and how often each arrived:

```bash
./.venv/bin/python -m app.cli extract --problems
```

### Reading scans

Photographs and scanned filings have no text in them to extract. To make them
searchable, install the two OCR tools once:

```bash
brew install tesseract tesseract-lang poppler
```

`tesseract-lang` is the part that speaks Slovak. Check it took:

```bash
./.venv/bin/python -m app.cli ocr --check
```

Then read the queue. This is slow — seconds per page — so leave it running:

```bash
./.venv/bin/python -m app.cli ocr
```

To have it happen by itself, put `OCR_ENABLED=true` in `.env`; the background
daemon then reads a few scans per cycle.

Then look at what it has:

```bash
./.venv/bin/python -m app.cli stats
./.venv/bin/python -m app.cli search "danova kontrola"
```

## 6. Connect Claude

```bash
claude mcp add email-assistant \
  -- ~/email-assistant/email-assistant/.venv/bin/python -m app.mcp.server
```

No API key, no per-token cost. See [MCP.md](MCP.md), including how to reach it
from a phone.

## 7. Keep it synchronising

```bash
cd ~/email-assistant/email-assistant
./.venv/bin/python -m app.cli daemon
```

Syncs every 15 minutes, extracts new documents, backs up once a day. Close the
window and it stops — the trade-off of running on your own Mac.

---

## If something goes wrong

| What you see | What it means |
|---|---|
| `command not found: brew` | Close Terminal, open a new one, run the installer again. |
| `connection refused` on 5432 | PostgreSQL stopped. `brew services start postgresql@16` |
| `redirect_uri_mismatch` | The URI in `.env` is not character-for-character one of the two you added in Google. |
| `insufficient authentication scopes` | You enabled writes after authorising. Run `auth-url` again. |
| `Missing code verifier` | An older checkout. Update (below), then start over from `auth-url`. |
| Migrations fail | `./.venv/bin/alembic upgrade head` to see the real error. |

## Updating

```bash
cd ~/email-assistant
git pull
cd email-assistant
./.venv/bin/pip install --quiet -e ".[dev]"
./.venv/bin/alembic upgrade head
```

Restart `uvicorn` afterwards — it holds the old code in memory. Nothing you
have already synchronised is lost; migrations only add to the database.

## Removing it

Deleting `~/email-assistant` removes the code and the virtualenv. The databases
are separate: `dropdb email_assistant email_assistant_test`. Revoke Google
access at <https://myaccount.google.com/connections>.
