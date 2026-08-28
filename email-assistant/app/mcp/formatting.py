"""Turning query results into text a model reads well.

Tool results are capped by the client — roughly 150k characters in the Claude
apps, 25k tokens in Claude Code — and every character spends context. So these
render compact prose rather than JSON dumps: no key names repeated on every
row, no null fields, and hard limits with an honest "N more" line rather than
a silent truncation.
"""

from __future__ import annotations

from datetime import datetime

# Long enough to be useful, short enough that ten results still fit.
SNIPPET_CHARS = 220
BODY_CHARS = 4000


def stamp(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment else "?"


def clip(text: str | None, limit: int) -> str:
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rsplit(" ", 1)[0] + "…"


def direction_arrow(direction: str | None) -> str:
    return {"inbound": "←", "outbound": "→", "internal": "↔"}.get(direction or "", "?")


def more(shown: int, total: int, noun: str = "result") -> str:
    if total <= shown:
        return ""
    return f"\n({total - shown} more {noun}(s) not shown — narrow the query or raise limit)"


def empty(what: str) -> str:
    return f"No {what} found."
