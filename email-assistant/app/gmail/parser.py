"""Pure parser for Gmail API ``users.messages.get(format="full")`` payloads.

Deliberately free of I/O and of database access: everything here is a
function of its input, which makes the trickiest part of the whole ingest
pipeline exhaustively testable from fixtures.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from app.gmail.addresses import (
    OwnedAddressSet,
    decode_mime_words,
    normalize_address,
    parse_address_list,
)

# Header lines worth keeping verbatim for later forensics / threading.
INTERESTING_HEADERS = (
    "from",
    "to",
    "cc",
    "bcc",
    "reply-to",
    "delivered-to",
    "subject",
    "date",
    "message-id",
    "in-reply-to",
    "references",
    "return-path",
    "list-id",
    "list-unsubscribe",
    "precedence",
    "auto-submitted",
    "x-original-to",
    "envelope-to",
    "content-type",
)

TEXT_PLAIN = "text/plain"
TEXT_HTML = "text/html"


@dataclass(slots=True)
class ParsedAttachment:
    part_id: str
    filename: str | None
    mime_type: str | None
    size_bytes: int | None
    gmail_attachment_id: str | None
    content_id: str | None = None
    is_inline: bool = False
    # Present only for small parts Gmail inlines into the payload itself.
    inline_data: bytes | None = None


@dataclass(slots=True)
class ParsedMessage:
    gmail_message_id: str
    gmail_thread_id: str
    history_id: int | None

    subject: str | None
    from_address: str | None
    from_name: str | None
    recipients: dict[str, list[tuple[str, str]]] = field(default_factory=dict)

    rfc822_message_id: str | None = None
    in_reply_to: str | None = None
    references: list[str] = field(default_factory=list)

    sent_at: datetime | None = None
    internal_date: datetime | None = None

    body_text: str | None = None
    body_html: str | None = None
    snippet: str | None = None

    labels: list[str] = field(default_factory=list)
    size_estimate: int | None = None
    raw_headers: dict[str, list[str]] = field(default_factory=dict)
    attachments: list[ParsedAttachment] = field(default_factory=list)

    # Filled in by :func:`resolve_direction`.
    direction: str = "unknown"
    account_address: str | None = None

    @property
    def has_attachments(self) -> bool:
        return any(not a.is_inline for a in self.attachments) or bool(self.attachments)

    def content_hash(self) -> str:
        """Stable fingerprint of the fields we persist.

        Lets incremental sync detect that a re-fetched message is unchanged
        apart from labels, and skip rewriting it.
        """
        parts = [
            self.gmail_message_id,
            self.subject or "",
            self.from_address or "",
            self.body_text or "",
            self.body_html or "",
            ",".join(sorted(self.labels)),
            "|".join(
                f"{a.part_id}:{a.filename}:{a.size_bytes}"
                for a in sorted(self.attachments, key=lambda x: x.part_id)
            ),
        ]
        return hashlib.sha256("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def decode_body_data(data: str | None, charset: str | None = None) -> str:
    """Decode a base64url body part, honouring the declared charset.

    Slovak mail still arrives as windows-1250 / iso-8859-2 often enough that
    assuming UTF-8 would silently mangle real content.
    """
    if not data:
        return ""
    raw = _b64url(data)
    if raw is None:
        return ""
    for candidate in (charset, "utf-8", "windows-1250", "iso-8859-2", "latin-1"):
        if not candidate:
            continue
        try:
            return raw.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", errors="replace")


def _b64url(data: str) -> bytes | None:
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        return None


def _headers_to_dict(headers: list[dict] | None) -> dict[str, list[str]]:
    """Case-insensitive multi-map; a header may legitimately repeat."""
    result: dict[str, list[str]] = {}
    for header in headers or []:
        name = (header.get("name") or "").lower()
        if not name:
            continue
        result.setdefault(name, []).append(header.get("value") or "")
    return result


def _first(headers: dict[str, list[str]], name: str) -> str | None:
    values = headers.get(name.lower())
    return values[0] if values else None


def _charset_of(part: dict) -> str | None:
    for header in part.get("headers") or []:
        if (header.get("name") or "").lower() == "content-type":
            value = header.get("value") or ""
            for token in value.split(";"):
                token = token.strip()
                if token.lower().startswith("charset="):
                    return token.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _part_disposition(part: dict) -> tuple[str | None, str | None]:
    """Return ``(disposition, content_id)`` for a MIME part."""
    disposition = None
    content_id = None
    for header in part.get("headers") or []:
        name = (header.get("name") or "").lower()
        value = header.get("value") or ""
        if name == "content-disposition":
            disposition = value.split(";", 1)[0].strip().lower()
        elif name == "content-id":
            content_id = value.strip().strip("<>")
    return disposition, content_id


def parse_epoch_ms(value: str | int | None) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def parse_date_header(value: str | None) -> datetime | None:
    """Parse a ``Date:`` header, tolerating the malformed ones in the wild."""
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_references(value: str | None) -> list[str]:
    if not value:
        return []
    tokens = value.replace(",", " ").split()
    return [t.strip("<>") for t in tokens if t.strip("<>")]


# ---------------------------------------------------------------------------
# MIME tree walk
# ---------------------------------------------------------------------------


def _walk_parts(part: dict, path: str = "") -> list[tuple[str, dict]]:
    """Depth-first walk yielding ``(part_id, part)``.

    Gmail's own ``partId`` is used when present; the positional path is the
    fallback so every part always has a stable identifier.
    """
    part_id = part.get("partId")
    if part_id in (None, ""):
        part_id = path
    collected = [(str(part_id), part)]
    for index, child in enumerate(part.get("parts") or []):
        child_path = f"{path}.{index}" if path else str(index)
        collected.extend(_walk_parts(child, child_path))
    return collected


def _is_attachment(part: dict, disposition: str | None) -> bool:
    if part.get("filename"):
        return True
    if (part.get("body") or {}).get("attachmentId"):
        return True
    return disposition == "attachment"


def extract_bodies_and_attachments(
    payload: dict,
) -> tuple[str | None, str | None, list[ParsedAttachment]]:
    """Split a payload into ``(text, html, attachments)``.

    Body parts are concatenated in document order, which keeps messages whose
    text is split across several parts (some mailing lists, some scanners)
    readable instead of truncated.
    """
    text_chunks: list[str] = []
    html_chunks: list[str] = []
    attachments: list[ParsedAttachment] = []

    for part_id, part in _walk_parts(payload):
        mime_type = (part.get("mimeType") or "").lower()
        body = part.get("body") or {}
        disposition, content_id = _part_disposition(part)

        if _is_attachment(part, disposition):
            inline_data = None
            if body.get("data") and not body.get("attachmentId"):
                inline_data = _b64url(body["data"])
            attachments.append(
                ParsedAttachment(
                    part_id=part_id,
                    filename=decode_mime_words(part.get("filename")) or None,
                    mime_type=mime_type or None,
                    size_bytes=body.get("size"),
                    gmail_attachment_id=body.get("attachmentId"),
                    content_id=content_id,
                    is_inline=disposition == "inline" or bool(content_id),
                    inline_data=inline_data,
                )
            )
            continue

        if mime_type.startswith("multipart/"):
            continue

        data = body.get("data")
        if not data:
            continue
        decoded = decode_body_data(data, _charset_of(part))
        if mime_type == TEXT_PLAIN:
            text_chunks.append(decoded)
        elif mime_type == TEXT_HTML:
            html_chunks.append(decoded)

    text = "\n".join(c for c in text_chunks if c).strip() or None
    html = "\n".join(c for c in html_chunks if c).strip() or None
    return text, html, attachments


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def parse_message(raw: dict) -> ParsedMessage:
    """Turn one Gmail API message resource into a :class:`ParsedMessage`."""
    payload = raw.get("payload") or {}
    headers = _headers_to_dict(payload.get("headers"))

    from_list = parse_address_list(_first(headers, "from"))
    from_name, from_address = from_list[0] if from_list else ("", None)

    recipients = {
        "to": parse_address_list(_first(headers, "to")),
        "cc": parse_address_list(_first(headers, "cc")),
        "bcc": parse_address_list(_first(headers, "bcc")),
        "reply_to": parse_address_list(_first(headers, "reply-to")),
        # Delivered-To may repeat; every value matters for alias detection.
        "delivered_to": parse_address_list(
            ", ".join(headers.get("delivered-to", []) + headers.get("x-original-to", []))
        ),
    }

    body_text, body_html, attachments = extract_bodies_and_attachments(payload)

    history_id = raw.get("historyId")
    try:
        history_id = int(history_id) if history_id is not None else None
    except (TypeError, ValueError):
        history_id = None

    return ParsedMessage(
        gmail_message_id=str(raw.get("id") or ""),
        gmail_thread_id=str(raw.get("threadId") or ""),
        history_id=history_id,
        subject=decode_mime_words(_first(headers, "subject")) or None,
        from_address=from_address,
        from_name=from_name or None,
        recipients=recipients,
        rfc822_message_id=(_first(headers, "message-id") or "").strip().strip("<>") or None,
        in_reply_to=(_first(headers, "in-reply-to") or "").strip().strip("<>") or None,
        references=parse_references(_first(headers, "references")),
        sent_at=parse_date_header(_first(headers, "date")),
        internal_date=parse_epoch_ms(raw.get("internalDate")),
        body_text=body_text,
        body_html=body_html,
        snippet=raw.get("snippet") or None,
        labels=list(raw.get("labelIds") or []),
        size_estimate=raw.get("sizeEstimate"),
        raw_headers={k: v for k, v in headers.items() if k in INTERESTING_HEADERS},
        attachments=attachments,
    )


def resolve_direction(message: ParsedMessage, owned: OwnedAddressSet) -> ParsedMessage:
    """Decide inbound/outbound/internal and which of my addresses was used.

    Mutates and returns *message* so callers can chain it onto
    :func:`parse_message`.
    """
    if not owned:
        message.direction = "unknown"
        message.account_address = None
        return message

    sender_is_mine = bool(message.from_address) and message.from_address in owned

    recipient_addresses = [
        addr
        for kind in ("to", "cc", "bcc", "delivered_to")
        for _, addr in message.recipients.get(kind, [])
    ]
    mine_among_recipients = [a for a in recipient_addresses if a in owned]

    if sender_is_mine:
        others = [a for a in recipient_addresses if a not in owned]
        message.direction = "outbound" if others or not recipient_addresses else "internal"
        message.account_address = owned.canonical(message.from_address)
    else:
        message.direction = "inbound"
        # Delivered-To is the most reliable signal for which alias received it;
        # fall back to the visible recipient headers, then to any known address.
        for kind in ("delivered_to", "to", "cc", "bcc"):
            for _, addr in message.recipients.get(kind, []):
                if addr in owned:
                    message.account_address = owned.canonical(addr)
                    return message
        if mine_among_recipients:
            message.account_address = owned.canonical(mine_among_recipients[0])
        else:
            # Neither sender nor any visible recipient is mine — typically a
            # BCC or a mailing list.  Direction stays inbound; the alias is
            # genuinely unknown rather than guessed.
            message.account_address = None
    return message


def parse_and_resolve(raw: dict, owned: OwnedAddressSet) -> ParsedMessage:
    return resolve_direction(parse_message(raw), owned)


def all_participants(message: ParsedMessage) -> list[tuple[str, str, str, int]]:
    """Flatten every address on the message into ``(kind, name, address, position)``."""
    rows: list[tuple[str, str, str, int]] = []
    if message.from_address:
        rows.append(("from", message.from_name or "", message.from_address, 0))
    for kind in ("to", "cc", "bcc", "reply_to", "delivered_to"):
        for position, (name, address) in enumerate(message.recipients.get(kind, [])):
            rows.append((kind, name, normalize_address(address), position))
    return rows
