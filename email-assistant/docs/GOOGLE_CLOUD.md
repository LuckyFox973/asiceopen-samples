# Google Cloud — the Gmail API part, on its own

Ten minutes in a browser. **Nothing needs to be installed** to do this — the
credentials just sit there until something uses them.

For installing the application that uses them, see [INSTALL_MAC.md](INSTALL_MAC.md).

---

## Before you start: one decision

**May the assistant change the mailbox?** Labels, archiving, drafts, moving to
the bin. If yes — which is the point — you want `gmail.modify` below.

The one thing you cannot add later without redoing all of this: **permanent
deletion**, bypassing the bin. It needs Google's *restricted* scope
`https://mail.google.com/` instead of `gmail.modify`. With the bin, "delete" is
reversible for about 30 days, so this is almost never worth the risk of holding
a token that can irreversibly wipe a mailbox. Skip it unless you are sure.

## 1. Create the project

<https://console.cloud.google.com/projectcreate> → name it `email-assistant` →
**Create**. Wait for the notification, then check it is selected in the top bar.

## 2. Enable the Gmail API

**APIs & Services** → **Library** → search *Gmail API* → **Enable**.

Only if you want encrypted backups to your Drive, also enable **Google Drive
API**.

## 3. Audience

> Google moved the consent screen. It is **not** under "APIs & Services" any
> more — look for **Google Auth Platform** in the left menu. If it offers
> *Get started*, take it.

**Google Auth Platform** → **Branding**: app name `Email Assistant`, your
e-mail as support and developer contact. Save.

**Google Auth Platform** → **Audience**:

- **Internal** — if the mailbox is on a Google Workspace account in your own
  organisation. Take this. Internal apps skip Google's review entirely.
- **External** — only for a personal `@gmail.com`. Then also add your own
  address under **Test users**, or authorising fails with `access_denied`.

## 4. Scopes

**Google Auth Platform** → **Data Access** → **Add or remove scopes**.

A panel opens with a search box and a long table. **Ignore both.** Scroll to
the bottom of that panel, where there is a box labelled **"Manually add
scopes"**. Paste all four lines there at once:

```
https://www.googleapis.com/auth/gmail.modify
https://www.googleapis.com/auth/gmail.settings.basic
https://www.googleapis.com/auth/userinfo.email
openid
```

→ **Add to table** → **Update** → **Save**.

Add `https://www.googleapis.com/auth/drive.file` too if you enabled the Drive
API in step 2.

If you decided on permanent deletion, replace the first line with
`https://mail.google.com/` — do not add both; it already includes everything
`gmail.modify` does.

## 5. OAuth client

**Google Auth Platform** → **Clients** → **Create client**.

- **Application type**: Web application
- **Name**: anything, e.g. `Email Assistant local`
- **Authorised redirect URIs** → **Add URI**, twice, exactly:

```
http://localhost:8000/api/v1/auth/google/callback
http://127.0.0.1:8000/api/v1/auth/google/callback
```

Both entries, because browsers and command-line tools disagree about which one
they send — a mismatch here is the most common failure in the whole process.

**Create**. A panel offers a JSON download — take it. That file holds both the
client id and the secret, and the application can read it directly:

```bash
./.venv/bin/python -m app.cli import-credentials
```

Copying the two values by hand works too. Either way, that is the Google side
finished.

---

## What each scope permits

| Scope | Permits | Does **not** permit |
|---|---|---|
| `gmail.readonly` | read messages, threads, attachments | any change |
| `gmail.settings.basic` | read your send-as aliases | reading mail |
| `gmail.modify` | the above, plus labels, archive, bin, restore, drafts, send | permanent deletion |
| `https://mail.google.com/` | everything, including permanent deletion | — |
| `drive.file` | files this app creates in Drive | anything else in your Drive |

`gmail.settings.basic` is how the assistant learns which addresses are yours, so
it can tell your messages from the other side's. It grants nothing else.

`gmail.modify` includes **send**. The application exposes no send command and
sending requires your approval — but the *token* could send. Google has no
"drafts but never send" scope. That is why every action is recorded before it
happens and audited after.

## If something goes wrong

| What you see | What it means |
|---|---|
| `redirect_uri_mismatch` | The redirect URI does not match one of the two from step 5, character for character. `localhost` and `127.0.0.1` are separate entries. |
| `access_denied` | Audience is External and you are not under Test users. |
| `insufficient authentication scopes` | The mailbox was authorised before a scope was added. Authorise again. |
| Consent screen shows fewer scopes than expected | Data Access was not saved, or the application is configured for a narrower set. |

## Undoing it

Revoke at <https://myaccount.google.com/connections> — any stored token becomes
useless immediately. Deleting the Cloud project removes the OAuth client for
good.
