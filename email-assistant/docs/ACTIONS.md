# Acting on the mailbox

Three tiers, from the brief. What separates them is not how useful an action
is, but **how bad it is if the assistant is wrong.**

| Tier | Actions | When it runs |
|---|---|---|
| **automatic** | apply a managed label, write a draft, unarchive, restore from bin | immediately |
| **configurable** | archive, remove a label | immediately once `GMAIL_AUTO_ARCHIVE=true`, otherwise it asks |
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
