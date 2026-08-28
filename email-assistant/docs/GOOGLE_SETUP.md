# Google Cloud setup — do it once

The whole point of this page: **decide the scopes before you touch the consent
screen.** Adding a scope later means editing the consent screen *and*
re-authorising every mailbox, because a refresh token carries only the
permissions granted when it was issued. Everything below assumes you want the
finished system, so you configure it once and never come back.

Roughly 20 minutes. Nothing here needs a credit card.

---

## Step 0 — decide two things first

Answer these now; they determine the scope list in step 3.

**1. Should the assistant be able to change the mailbox?**
Labels, archiving, drafts, and moving to the bin. Almost certainly yes — it is
what you asked for. → `GMAIL_WRITE_ENABLED=true`

**2. Should it be able to delete *permanently*, bypassing the bin?**
**Recommended: no.** With the bin, "delete" is reversible for about 30 days and
`restore_message` undoes it. Bypassing the bin needs Google's *restricted*
scope `https://mail.google.com/`, which:

- grants full mailbox control, including irreversible deletion, on a token
  that sits in your database;
- triggers a stricter Google review if your app is **External** (an Internal
  Workspace app is exempt).

You can enable it later — but that is the one change that *does* cost a
re-authorisation. If you want to avoid ever coming back, and you want that
capability eventually, turn it on now.

**3. Do you want encrypted backups to Google Drive?**
→ adds `drive.file`, which sees only files this application creates.

Whatever you choose, the application requests **exactly one** mail scope —
`gmail.modify` already includes reading, and `https://mail.google.com/`
includes both — so the consent screen never asks for more than it can use.

---

## Step 1 — create the project

<https://console.cloud.google.com/projectcreate>

Name it `email-assistant`. Note the **project ID** (it differs from the name).

## Step 2 — enable the APIs

*APIs & Services → Library*, enable:

| API | Needed for |
|---|---|
| **Gmail API** | everything |
| **Google Drive API** | only if you chose Drive backups |
| **Cloud Pub/Sub API** | only if you later want push notifications instead of polling |

Enabling an API you do not use costs nothing, so turning on Pub/Sub now saves a
return trip.

## Step 3 — the OAuth consent screen

*APIs & Services → OAuth consent screen*

**User type: Internal**, if the Workspace account is in your own organisation.
This matters: Internal apps skip Google verification entirely, including for
restricted scopes. Choose External only if the mailbox is on a personal Gmail
account — and then expect a review if you asked for permanent deletion.

Fill in app name, support e-mail, developer contact.

### Scopes — add exactly these

Under *Add or remove scopes*, paste the ones matching your Step 0 answers.
**Add every line you might want**, because coming back means re-authorising.

Always:

```
https://www.googleapis.com/auth/gmail.settings.basic
https://www.googleapis.com/auth/userinfo.email
openid
```

Then **one** mail scope:

| Your choice | Scope |
|---|---|
| Read only | `https://www.googleapis.com/auth/gmail.readonly` |
| **Read + write (recommended)** | `https://www.googleapis.com/auth/gmail.modify` |
| Read + write + permanent delete | `https://mail.google.com/` |

And if you chose Drive backups:

```
https://www.googleapis.com/auth/drive.file
```

`gmail.settings.basic` is what lets the assistant read your send-as aliases,
which is how it knows which addresses are yours and tells your messages from
the other side's. It grants nothing else.

If the consent screen asks for a justification, the honest one is: *"Internal
tool that maintains a searchable archive of the firm's own mailbox and
prepares draft replies. It applies labels and archives; it does not send
without explicit approval."*

## Step 4 — the OAuth client

*APIs & Services → Credentials → Create credentials → OAuth client ID*

- **Type: Web application**
- **Authorised redirect URIs** — add both now:
  ```
  http://localhost:8000/api/v1/auth/google/callback
  http://127.0.0.1:8000/api/v1/auth/google/callback
  ```
  Both, because browsers and tools disagree about which one they send. Adding
  a redirect URI later is the one change that is *not* painful — but you may as
  well do it now.

Copy the **client ID** and **client secret**.

## Step 5 — put it in `.env`

```bash
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
GOOGLE_OAUTH_REDIRECT_URI=http://localhost:8000/api/v1/auth/google/callback

# Step 0 answers
GMAIL_WRITE_ENABLED=true
GMAIL_ALLOW_PERMANENT_DELETE=false
GMAIL_AUTO_ARCHIVE=false        # ask before archiving until you trust it

SYNC_START_DATE=2026-01-01      # nothing older is ever fetched
```

Never commit `.env`. It is gitignored.

Verify the scope list matches what you configured:

```bash
python -m app.cli check
```

It prints the exact scopes it will request. If they do not match the consent
screen, fix it **now**, not after authorising.

## Step 6 — connect the mailbox

```bash
make run                      # in one terminal
python -m app.cli auth-url    # in another
```

Open the URL, sign in, approve. Google shows precisely the scopes from step 3 —
read that screen; it is the last honest summary of what you granted.

```bash
python -m app.cli accounts
python -m app.cli sync you@example.com --mode initial
python -m app.cli extract
```

The first sync stops at `SYNC_MAX_MESSAGES_PER_RUN` and reports `partial`. Run
it again to continue from the checkpoint.

## Step 7 — connect it to Claude

```bash
claude mcp add email-assistant \
  -- /full/path/to/email-assistant/.venv/bin/python -m app.mcp.server
```

See [MCP.md](MCP.md), including how to reach it from a phone.

---

## What each scope actually permits

So you can judge the consent screen rather than trust it:

| Scope | Permits | Does **not** permit |
|---|---|---|
| `gmail.readonly` | read messages, threads, attachments | any change at all |
| `gmail.settings.basic` | read send-as aliases and basic settings | reading mail |
| `gmail.modify` | everything in readonly, plus labels, archive, bin, restore, drafts, send | permanent deletion |
| `https://mail.google.com/` | everything, including permanent deletion | — |
| `drive.file` | files this app creates in Drive | anything else in your Drive |

Note that `gmail.modify` includes **send**. The application does not expose a
send tool, and sending sits in the approval tier — but the *token* could send.
That is unavoidable: Google has no "drafts but never send" scope. It is why
every action is proposed, recorded, and audited rather than simply performed.

## Common stumbles

| Symptom | Cause |
|---|---|
| `Error 400: redirect_uri_mismatch` | The URI in `.env` is not character-for-character one of the authorised URIs. `localhost` and `127.0.0.1` are different entries. |
| `Error 403: access_denied` | Consent screen is External and in Testing — add your address under *Test users*, or switch to Internal. |
| `insufficient authentication scopes` on an action | The mailbox was authorised before you enabled writes. Re-run `auth-url`. |
| Consent screen shows fewer scopes than expected | `.env` and the consent screen disagree. `python -m app.cli check` prints what the app requests. |
| Refresh token missing after re-authorising | Google only returns one on first consent; the app forces `prompt=consent` so it always gets one. If it still fails, remove the app under [Google Account → Third-party access](https://myaccount.google.com/connections) and authorise again. |

## Undoing it

Revoke everything at <https://myaccount.google.com/connections> — the stored
refresh token becomes useless immediately. Your synced data stays in the local
database; `python -m app.cli` still reads it. Deleting the Cloud project
removes the OAuth client for good.
