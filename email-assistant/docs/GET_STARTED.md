# From nothing to a working assistant

Written for a Mac with nothing installed. Three parts:

1. **Get it running on your Mac** — one command, about ten minutes
2. **Google Cloud** — about ten minutes, and the part worth doing once
3. **Connect your mailbox and Claude** — five minutes

Nothing here needs a credit card.

---

# Part 1 — Get it running on your Mac

Open **Terminal** (⌘-Space, type "Terminal") and paste this:

```bash
curl -fsSL https://raw.githubusercontent.com/LuckyFox973/asiceopen-samples/claude/gmail-ai-assistant-system-u72j2z/email-assistant/scripts/bootstrap_macos.sh | bash
```

It installs Homebrew if you do not have it, then Python, PostgreSQL and the
project itself into `~/email-assistant`. It creates the database, generates
your encryption keys, and loads demo data. Run it twice and nothing breaks —
every step checks before it acts.

When it finishes, try it on the demo data:

```bash
cd ~/email-assistant/email-assistant
./.venv/bin/python -m app.cli stats
./.venv/bin/python -m app.cli find "CMR duplicitne"
```

That second command searches *inside* a PDF. If it returns a result, everything
works.

> **Where things live now.** The project is at
> `~/email-assistant/email-assistant`. Its settings are in a file called `.env`
> in that folder — that is where every `GOOGLE_...` value below goes. Open it
> with `open -e .env` from that folder, or in any text editor.

---

# Part 2 — Google Cloud

## Before you click anything: two decisions

These determine which permissions you ask Google for. **Adding one later means
redoing the consent screen and re-authorising your mailbox**, so settle them now.

### 1. May the assistant change the mailbox?

Labels, archiving, drafts, moving to the bin. **Yes** — it is what you asked for.

### 2. May it delete permanently, bypassing the bin?

**Recommended: no.** With the bin, "delete" is reversible for about 30 days.
Bypassing it needs Google's *restricted* permission, which grants complete
control of the mailbox on a token stored on your machine.

This is the only choice you cannot add later without re-authorising. If you are
sure you will want it eventually, turn it on now.

**Set your answers before you continue.** In `~/email-assistant/email-assistant/.env`:

```
GMAIL_WRITE_ENABLED=true
GMAIL_ALLOW_PERMANENT_DELETE=false
```

Then, in that folder, run:

```bash
./.venv/bin/python -m app.cli check
```

It prints, one per line, **the exact list of permissions you will paste into
Google**. Keep that terminal window open — you will copy from it in step 2.3.

## 2.1 Create the project

<https://console.cloud.google.com/projectcreate>

Name it `email-assistant` and click **Create**. Wait for the notification that
it is ready, and make sure it is the selected project in the bar at the top.

## 2.2 Turn on the Gmail API

Left menu → **APIs & Services** → **Library**. Search for **Gmail API**, open
it, click **Enable**.

If you want backups to your Google Drive, also enable **Google Drive API**.

## 2.3 The consent screen

> Google renamed this. It is no longer under "APIs & Services". Look in the left
> menu for **Google Auth Platform**.

Left menu → **Google Auth Platform**. If it offers **Get started**, take it.
You will end up with four pages down the left: *Overview*, *Branding*,
*Audience*, *Data Access*, *Clients*.

**Branding** — app name (`Email Assistant`), your e-mail as support contact,
your e-mail again as developer contact. Save.

**Audience** — choose:

- **Internal** if your mailbox is on a Google Workspace account in your own
  organisation. Take this. Internal apps skip Google's review entirely, and the
  permission list is not even shown as a warning screen.
- **External** only if it is a personal `@gmail.com` account. Then also add your
  own address under **Test users**, or authorising will fail with
  `access_denied`.

**Data Access** — this is where the permissions go.

Click **Add or remove scopes**. A panel opens with a search box and a long
table. At the bottom of that panel there is a box labelled **"Manually add
scopes"**. Paste your list from `app.cli check` there — all lines at once —
then click **Add to table**, then **Update**, then **Save**.

For a standard install with write access, that list is:

```
https://www.googleapis.com/auth/gmail.modify
https://www.googleapis.com/auth/gmail.settings.basic
https://www.googleapis.com/auth/userinfo.email
openid
```

If `app.cli check` printed something different, **use what it printed** — it
reflects the choices in your `.env`, and it is the list that has to match.

## 2.4 The OAuth client

Left menu → **Google Auth Platform** → **Clients** → **Create client**.

- **Application type**: Web application
- **Name**: anything, e.g. `Email Assistant local`
- **Authorised redirect URIs** → **Add URI**, twice, exactly these two:

```
http://localhost:8000/api/v1/auth/google/callback
http://127.0.0.1:8000/api/v1/auth/google/callback
```

Both, because browsers and command-line tools disagree about which one they
send, and a mismatch is the most common failure here.

Click **Create**. A panel shows your **Client ID** and **Client secret** —
copy both now.

## 2.5 Put the credentials in `.env`

Back in Terminal:

```bash
cd ~/email-assistant/email-assistant
open -e .env
```

Fill in the two values you just copied:

```
GOOGLE_CLIENT_ID=1234567890-abcdefg.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/api/v1/auth/google/callback

GMAIL_WRITE_ENABLED=true
GMAIL_ALLOW_PERMANENT_DELETE=false
GMAIL_AUTO_ARCHIVE=false

SYNC_START_DATE=2026-01-01
```

`SYNC_START_DATE` is a hard floor: nothing older is ever fetched. Start recent —
you can lower it later.

Save the file, then check everything lines up:

```bash
./.venv/bin/python -m app.cli check
```

It should report no configuration problems, and the permission list should match
what you saved in Data Access. **If they disagree, fix it now** — after you
authorise, fixing it means authorising again.

---

# Part 3 — Connect your mailbox

Two Terminal windows.

**First window** — start the application and leave it running:

```bash
cd ~/email-assistant/email-assistant
./.venv/bin/uvicorn app.main:app --port 8000
```

**Second window** — get the authorisation link:

```bash
cd ~/email-assistant/email-assistant
./.venv/bin/python -m app.cli auth-url
```

Open the printed URL in a browser, sign in with the mailbox you want, and
approve. Google shows exactly the permissions from step 2.3 — read that screen;
it is the last honest summary of what you granted.

The page will confirm the mailbox is connected. Then, in the second window:

```bash
./.venv/bin/python -m app.cli accounts
./.venv/bin/python -m app.cli sync you@yourdomain.sk --mode initial
./.venv/bin/python -m app.cli extract
```

The first sync stops after a couple of thousand messages and reports `partial`.
Run the same `sync` command again to continue where it left off. Repeat until
it says `completed`.

Then look at what it has:

```bash
./.venv/bin/python -m app.cli stats
./.venv/bin/python -m app.cli search "danova kontrola"
```

## Connect it to Claude

```bash
claude mcp add email-assistant \
  -- ~/email-assistant/email-assistant/.venv/bin/python -m app.mcp.server
```

Now ask Claude things like *"Which conversations have been waiting on me for
more than a week?"* or *"What changed in the KOVACO contract between versions?"*.
No API key, no per-token cost. See [MCP.md](MCP.md), including how to reach it
from your phone.

## Keep it synchronising

```bash
cd ~/email-assistant/email-assistant
./.venv/bin/python -m app.cli daemon
```

Leave that window open and it syncs every 15 minutes, extracts new documents,
and takes a backup once a day. Close it and everything stops — which is the
trade-off of running on your own Mac.

---

# Reference

## What each permission actually allows

| Permission | Allows | Does **not** allow |
|---|---|---|
| `gmail.readonly` | Read messages, threads, attachments | Any change at all |
| `gmail.settings.basic` | Read your send-as aliases | Reading mail |
| `gmail.modify` | The above, plus labels, archive, bin, restore, drafts, send | Permanent deletion |
| `mail.google.com` | Everything, including permanent deletion | — |
| `drive.file` | Files this app creates in Drive | Anything else in your Drive |

`gmail.settings.basic` is how the assistant learns which addresses are yours,
so it can tell your messages from the other side's. It grants nothing else.

Note that `gmail.modify` includes **send**. The assistant exposes no send
command, and sending requires your approval — but the *token* could send.
Google has no "drafts but never send" permission. That is why every action is
recorded before it happens and audited after.

## What it will do without asking

| | Actions | When |
|---|---|---|
| **Automatic** | Apply its own label, write a draft, unarchive, restore from bin | Immediately |
| **Configurable** | Archive | Only if you set `GMAIL_AUTO_ARCHIVE=true` |
| **Needs your yes** | Move to bin, delete permanently, send | Never on its own |

No setting moves an action out of the last row.

## When something goes wrong

| What you see | What it means |
|---|---|
| `redirect_uri_mismatch` | The URI in `.env` is not character-for-character one of the two you added in step 2.4. `localhost` and `127.0.0.1` are separate entries. |
| `access_denied` | Audience is External and you are not listed under Test users. Add yourself, or switch to Internal. |
| `insufficient authentication scopes` | You enabled writes after authorising. Run `auth-url` again. |
| `command not found: brew` | Close Terminal, open a new one, run the bootstrap command again. |
| `connection refused` on port 5432 | PostgreSQL stopped. `brew services start postgresql@16` |
| The consent screen lists different permissions than expected | `.env` and Data Access disagree. `app.cli check` prints what the app will ask for. |
| No refresh token after re-authorising | Remove the app at [Third-party access](https://myaccount.google.com/connections) and authorise again. |

## Undoing it all

Revoke access at <https://myaccount.google.com/connections> — the stored token
becomes useless immediately. Your synced mail stays in the local database and
the commands still read it. Deleting the Cloud project removes the OAuth client
for good. Deleting `~/email-assistant` removes everything local.
