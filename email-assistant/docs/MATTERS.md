# Clients and matters

The shape the brief asked for:

```
KOVACO                          client
└── Kasačná sťažnosť            matter
    ├── threads                 filed by the rules below
    ├── documents               via the messages that carried them
    ├── contacts
    ├── tasks                   phase 4
    └── deadlines               phase 4
```

## Two rules govern the whole thing

**Nothing here ever creates a matter.** Not on a hunch, not on a strong hunch.
It links conversations to matters that already exist; where it cannot, it
produces a *proposal* you accept or ignore. A wrongly auto-created file is far
more expensive to unpick than an unfiled thread, and a mailbox that quietly
grows two hundred spurious matters is worse than one that grows none.

**Every link says how sure it is, and why.** Each carries a confidence, the
rule that produced it, and a sentence you can read. Below the threshold the
link is still made but flagged `needs_review`, so nothing disappears silently
and nothing is asserted more strongly than the evidence supports.

Threshold: **0.75** to stand on its own. Below **0.35** the evidence is too
thin to record at all.

## The rules, strongest first

| Rule | Confidence | What it uses |
|---|---|---|
| `subject_reference` | 0.95 | The subject quotes a matter reference (`KOV-2026-01`). People quote file numbers precisely because they want the mail filed there. References shorter than 4 characters are ignored — they match inside ordinary words. |
| `sibling_thread` | 0.9 / 0.7 | Another thread with the same normalised subject is already filed. 0.9 when it shares participants, 0.7 when it does not. |
| `single_open_matter` | 0.8 | Everyone external belongs to one client's domains, and that client has exactly one open matter. |
| `subject_similarity` | ≤0.7 | Trigram similarity between the subject and a matter title. Capped below the auto-link threshold unless the client also matches — resemblance is suggestive, never conclusive. |
| `client_only` | 0.5 | The client is clear, the matter is not. Reported as a proposal; **no link is made**. |

Subject normalisation strips reply and forward prefixes in the languages this
mailbox actually sees — `Re:`, `Fwd:`, `Odp:`, `Aw:`, `Vec:`, `Re[2]:` — so a
chain compares as one subject. "Rekonštrukcia" is not treated as a reply.

## Working with it

```bash
python -m app.cli client add "KOVACO" --reference KOV --domains kovaco.sk
python -m app.cli matter add <client-id> "Kasačná sťažnosť" --reference KOV-2026-01

python -m app.cli file run --dry-run     # what would happen, changes nothing
python -m app.cli file run
python -m app.cli matter list            # the spis, with counts

python -m app.cli file review            # links the system was unsure about
python -m app.cli file confirm <link-id>
python -m app.cli file reject <link-id>
```

Over HTTP: `POST /matters/assign` (with `dry_run`),
`GET /matters/suggestions/{thread_id}` for a single explained suggestion,
`GET /matters/review/queue`, `POST /matters/links/{id}/confirm`,
`DELETE /matters/links/{id}`.

## Behaviour worth knowing

- **A conversation may belong to several matters.** Links are many-to-many, not
  a column on the thread — one piece of correspondence often touches two files.
- **Re-running files nothing twice.** Threads already linked are skipped.
- **Better evidence upgrades a link; nothing downgrades a confirmed one.** Once
  you confirm a filing, no later guess overrides it.
- **Rejecting removes the link but keeps the record.** The audit entry survives,
  so a filing that was undone is still traceable.
- **`file run --dry-run` is honest.** It counts exactly what the real run would
  do, and writes nothing.

## What comes next

Phase 4 adds tasks, follow-ups and deadlines to the same file, and phase 3's AI
layer will propose *new* matters from the correspondence — as proposals in the
same review queue, never as silent creations. The rules above stay: they are
deterministic, free, and they already handle the cases where the answer is
genuinely obvious.
