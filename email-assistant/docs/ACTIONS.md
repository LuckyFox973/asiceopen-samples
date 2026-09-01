# Acting on the mailbox

Three tiers, from the brief. What separates them is not how useful an action
is, but **how bad it is if the assistant is wrong.**

| Tier | Actions | When it runs |
|---|---|---|
| **automatic** | apply a managed label, write a draft, create a task, unarchive, restore from bin | immediately |
| **configurable** | archive, remove a label | immediately once `GMAIL_AUTO_ARCHIVE=true`, otherwise it asks |
| **configurable** | file a document to Drive | immediately once `DRIVE_AUTO_FILE=true`, otherwise it asks |
| **approval** | move to bin, permanent delete, send | never without an explicit yes |

**No setting promotes an action out of the approval tier.** There is
deliberately no configuration that lets the assistant bin or send on its own,
and a test asserts it stays that way even with every permissive flag on.

Every action — including the automatic ones — is written to `pending_action`
before it runs and to `audit_log` after. The rule the brief set is the one this
enforces: *the assistant never makes a consequential change quietly.*

## Why "delete" means the bin

`gmail.modify` covers labels, archiving, the bin, drafts and sending.
Permanent deletion needs Google's restricted `https://mail.google.com/` scope
and is the one thing nothing undoes.

So "delete this" moves the message to the bin: Gmail keeps it about 30 days,
and `restore_message` reverses it. Permanent deletion exists, behind
`GMAIL_ALLOW_PERMANENT_DELETE=true`, requires approval like everything
destructive, and is labelled as irreversible everywhere it appears.

For a mailbox of privileged correspondence, holding a token that can
irreversibly delete is a real risk with almost no upside. Off by default.

## The label guard

`users.messages.modify` with `addLabelIds: ["TRASH"]` bins a message. So an
"apply a label" permission that accepted system labels would be an unaudited
bin button.

Label operations refuse every Gmail system label — `INBOX`, `TRASH`, `SPAM`,
`STARRED`, `IMPORTANT`, the categories. Archiving and binning have their own
operations, each in its own tier. Tests cover the obvious attempts, including
lower-case ones.

## From Claude

```
apply_label(gmail_message_id, label)      → runs now
draft_reply(to, subject, body, …)         → runs now, sends nothing
archive_message(gmail_message_id)         → runs now if enabled, else asks
unarchive_message / restore_message       → runs now, always safe
request_trash(gmail_message_id)           → waits
request_permanent_delete(gmail_message_id)→ waits, and says it cannot be undone
pending_actions()                         → what is waiting
approve_action(action_id)                 → carries it out
reject_action(action_id, note)            → drops it
```

A waiting action returns its id and the sentence *"Nothing has changed in the
mailbox yet."* So the natural exchange is one turn: Claude proposes, you say
yes, Claude approves. The record exists either way — which is the part that
matters when the proposal came from a schedule rather than from you.

## From the command line

```bash
python -m app.cli action list                 # waiting for you
python -m app.cli action approve <id>         # decide and execute
python -m app.cli action reject <id> --note "not this one"
python -m app.cli action history              # everything, decided or not

python -m app.cli action draft --to klient@abc.sk \
  --subject "Re: Danova kontrola" --body "Podklady som prevzal."
```

## Details worth knowing

- **Proposals expire after 7 days.** Something nobody answered should lapse,
  not fire later against a mailbox that has moved on.
- **Every executed action records how to undo it**, where undoing is possible.
  `undo_hint` is empty exactly when nothing can be undone — permanent deletion
  and sending.
- **A Gmail failure is recorded, not raised.** The action becomes `failed` with
  the reason, and a 403 for missing scopes is translated into what to do about
  it rather than passed through raw.
- **Nothing can be proposed at all while `GMAIL_WRITE_ENABLED=false`.** The
  grant carries no write permission, so the assistant is not merely restrained
  — it is not authorised.
- **Sending is not exposed as a tool.** `send` exists as an action type in the
  approval tier, but no MCP tool creates one. Sending on your behalf is a
  bigger step than binning, and it will ship when you ask for it, not as a side
  effect of enabling writes.


## Filing a document to Drive

An invoice is filed under the company that was **billed**, not under whoever
sent it: Anthropic invoicing INFI belongs in INFI's folder. The document's own
text is searched for the billed company's name and registration number, and
the number is what carries the weight — a name can be a substring of another
company's name, while a registration number is a fact about exactly one.

Nothing is guessed. When two of the owner's own companies are both named, or
only a weak match exists, it files nothing and says why: a misfiled invoice is
worse than one that waits for an answer.

**The order is deliberate.** The document is uploaded first and the mail
archived second, so it is safely somewhere else before it leaves the inbox. A
failed upload archives nothing, and the mail is still where its owner expects
to find it.

Each configurable action is released by **its own** setting. One shared flag
was enough while archiving was the only one; letting a Drive upload run
because archiving had been allowed would be a different permission than the
owner granted.

### The permission this needs

Writing into a folder the owner already made requires the full `drive` scope.
`drive.file` — which is what backups use, and all this asked for until now —
reaches only files the application itself created, so a pre-existing folder is
invisible to it and naming it as a parent fails.

Google treats the full scope as restricted. On a Workspace domain an
**Internal** OAuth app may use it without review; on a personal account the
app stays in testing, where refresh tokens expire after seven days.

It is off by default, and turning it on is two steps rather than one:
`DRIVE_WRITE_ENABLED=true` and a fresh consent. The MCP tool refuses with an
explanation rather than failing obscurely when it is off.

### From the command line

```bash
# Register a company's folder, once
python -m app.cli folder add 03_INFI <drive-folder-id> \
    --match "Infinity Finance" --match 51234567

python -m app.cli folder list

# See what would happen
python -m app.cli file-to-drive <attachment-id> --dry-run

# Do it
python -m app.cli file-to-drive <attachment-id>
python -m app.cli file-to-drive <attachment-id> --no-archive
python -m app.cli file-to-drive <attachment-id> --folder 03_INFI
```

A folder registered with no match terms is refused: it could never resolve,
and a registry entry that silently never fires is worse than none.

### From Claude

`filing_folders` lists them, `which_folder` says where a document belongs
without touching anything, and `file_to_drive` does it. The last one's
description tells the model plainly that the user must ask for it — filing
happens once an invoice is paid or booked, and only its recipient knows when
that was.


## Tasks

A task called "pay Orange" is a reminder to go and look something up. A task
called "Pay Orange — invoice 2897510916, 47.90 EUR" with its due date set is
the answer, and the difference is only that the amount and the date were read
out of the document — which has already happened, since the text is extracted
and indexed.

Reading them is patterns, not a model. Slovak, Czech and English invoices
label the due date half a dozen ways, and both `47,90 EUR` and `$120.00`
arrive, sometimes in the same mailbox. Two details decide whether a date is
right:

- **The day comes first.** A Slovak supplier writing `09.05.2026` means the
  ninth of May; reading it the American way moves a payment four months.
- **The issue date is not the due date.** Both are labelled *Dátum*, and only
  one is when the money is owed, so the labels are matched most-specific first.

Two more, both found on real invoices after the first version shipped:

- **The amount is the total, not the first figure on the page.** A Slovak
  invoice prints several subtotals above it; reading `Splátky spolu 142,94 €`
  when `Spolu s DPH 304,68 €` sits two lines below has a bill half paid. Only
  a labelled total counts — an unlabelled figure produces no amount at all.
- **The payee comes from the document, not the envelope.** A forwarded
  invoice arrives from one of the user's own companies, and "Pay Royalty Fox"
  for an Orange bill looks right and is nonsense. The issuer is read from
  `Dodávateľ:` and its equivalents; failing that the sender is used, unless
  the sender matches a registered filing folder — never name your own company
  as the party to pay. Who it arrived from is kept in the task's notes, since
  that is how the mail is found again.

Nothing is invented: every field is optional, and a document that is not an
invoice produces a task with no date rather than a guessed one.

Creating a task sits in the **automatic** tier, alongside writing a draft —
both prepare something for the user and change nothing they would miss. It is
still recorded in `pending_action` and the audit log like everything else.

```bash
python -m app.cli config set TASKS_ENABLED true
python -m app.cli auth-url            # Google must grant Tasks

python -m app.cli task --lists
python -m app.cli task <attachment-id>                  # from an invoice
python -m app.cli task --title "Zavolat sudu" --due 2026-09-15
```

A due date in Google Tasks is a date, not a moment. The API takes RFC 3339 and
ignores the time, so midday UTC is sent — a local midnight would land the task
on the previous day for anyone east of UTC.
