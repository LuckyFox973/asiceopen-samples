# Using it from Claude

The point of this file: **you do not need an Anthropic API key, and there is no
per-token bill.** Claude queries the database as a tool, over MCP. The
reasoning happens in the Claude you already pay for.

That is why the MCP server was built before the AI layer. For testing whether
this system is useful, it is the whole answer.

## What Claude can do through it

Twenty-four tools, in three groups by what they cost you if they are wrong.

**Reading — sixteen tools, no consequences:**

| | |
|---|---|
| `search_emails` | words in subject or body, with filters |
| `get_thread` | a whole conversation in order |
| `search_threads` | find conversations rather than messages |
| `search_documents` | **inside** attachment text — PDF, Word, Excel, archives, scans |
| `get_attachment_text` | one document, with its tracked changes and comments |
| `document_versions` | every version of a document, and what changed |
| `diff_documents` | compare any two documents |
| `list_clients` | clients and their open matters |
| `get_matter` | one case file and its conversations |
| `review_queue` | filings the system was unsure about |
| `recent_activity` | who wrote last, by conversation |
| `sync_status` | what is stored, and when it last synced |
| `run_sync` | fetch new mail now |
| `recent_actions` | the audit log |
| `pending_actions` | what is waiting for your decision |
| `approve_action` / `reject_action` | your decision on one of them |

**Changing the mailbox, without asking — five tools, all reversible:**

| | |
|---|---|
| `apply_label` | put a thread under a label |
| `archive_message` / `unarchive_message` | out of the inbox, and back |
| `draft_reply` | write a draft — it is never sent |
| `restore_message` | back out of the bin |

Labels cannot touch `INBOX`, `TRASH`, `SPAM` or the other system labels:
`modify` with `addLabelIds: ["TRASH"]` would otherwise be an unaudited bin
button wearing a label's name.

**Requiring your approval — two tools, and they only ever queue:**

| | |
|---|---|
| `request_trash` | asks to move to the bin |
| `request_permanent_delete` | asks to delete beyond recovery |

Neither does anything by itself. Both write a `pending_action` and stop; the
mailbox changes when you approve it, and the audit log records who asked, when,
and what happened.

**There is no tool that sends mail.** The action type exists in the approval
tiers, and nothing creates one — deliberately, until the system has been lived
with. Every call, read or write, is written to the audit log.

## Claude Code on your Mac — the simplest path

Works on any plan, today:

```bash
claude mcp add email-assistant \
  -- /full/path/to/email-assistant/.venv/bin/python -m app.mcp.server
```

Then in Claude Code, `/mcp` shows it connected, and you can ask things like:

> Where did the tax authority claim the CMR notes were duplicates?
>
> What changed in the KOVACO contract between versions?
>
> Which conversations have been waiting on me for more than a week?

Testing happens **in the terminal**, not in the Claude desktop app: this
registers the server with Claude Code, so start it with

```bash
cd ~/email-assistant/email-assistant
claude
```

and type `/mcp` to see it connected.

Run `claude` **from the project directory**. The server reads `.env` from the
working directory for the database connection, and from anywhere else it will
not connect. (Alternatively, set `DATABASE_URL` in the MCP entry's own
environment.)

## Claude Desktop

Local MCP servers in Claude Desktop ship as **desktop extensions**, which
Anthropic documents as available on **Team and Enterprise plans**. On Pro or
Max, use Claude Code (above) or the remote route (below).

## iPhone, and Claude Desktop on any plan — remote connector

Custom **remote** connectors are available on Free, Pro, Max, Team and
Enterprise, under *Customize → Connectors*. They are added to your account,
so every Claude surface signed into it can use them.

A remote connector needs an HTTPS URL. That does **not** mean paying for
hosting: a tunnel from your own Mac is enough, and it is honest about the
trade-off you already accepted — it works while the Mac is on.

```bash
# 1. Serve MCP over HTTP, on loopback only
python -m app.mcp.server --transport streamable-http --port 8765

# 2. Expose that port over HTTPS (either tool; both have a free tier)
cloudflared tunnel --url http://127.0.0.1:8765
#   or
tailscale funnel 8765
```

Then in Claude: *Customize → Connectors → Add custom connector*, and paste the
`https://…./mcp` URL the tunnel prints.

**Before you do this, read the next section.** A tunnel puts a door to your
client correspondence on the public internet.

## Securing the remote route

The stdio route needs none of this — nothing listens on a port. The tunnel
route does.

1. **A random URL is not authentication.** Cloudflare's quick tunnels hand out
   an unguessable hostname, which stops crawlers and nothing else. Anyone who
   sees the URL — in a screenshot, a log, a clipboard — has your mailbox.
2. **Use the connector's request-header authentication.** Issue a key
   (`python -m app.cli api-key create ios-connector`) and set it as a request
   header on the connector, then put a proxy in front that checks it. The MCP
   server itself does not authenticate yet — treat that as the gap it is.
3. **Prefer Tailscale Funnel over a public quick tunnel** if you have the
   choice: traffic stays inside your tailnet unless you explicitly publish it.
4. **Turn the tunnel off when you are not testing.** It is one Ctrl-C.

For privileged legal correspondence, the honest ranking is: Claude Code on the
Mac (nothing listens) → Tailscale → a public tunnel with header auth → nothing
else.

## What this costs

Nothing beyond the Claude subscription. There is no API key, no metered call,
no `ai_usage` row — because no request goes to the Anthropic API. Claude reads
tool results the same way it reads a file you paste in.

The one cost is context: a tool result consumes tokens from the conversation
window, which is why every tool caps its output and says "N more not shown"
rather than dumping a mailbox into the chat.
