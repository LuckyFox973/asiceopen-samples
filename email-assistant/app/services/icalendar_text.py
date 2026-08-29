"""Readable text from an iCalendar (.ics) attachment.

Hand-rolled against RFC 5545 rather than pulled from a library, for three
reasons that all matter here.  The whole job is unfold, split, unescape and
format; every library that models the calendar properly also materialises
recurrences, and ``RRULE:FREQ=SECONDLY;COUNT=2000000000`` is then a two-line
denial of service that an opposing party can send for free.  And these files
arrive from strangers, so every bound wants to be one we set.

What a lawyer needs out of an invitation is the date, who called it, who is
coming, and whether it was cancelled.  That is what this produces.
"""

from __future__ import annotations

import io
import quopri
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

# A single logical line may be folded across many physical ones.  Both bounds
# are needed: without the first, one 5 MB DESCRIPTION becomes 5 MB of indexed
# text; without the second, a file that is one endlessly folded line does the
# same more slowly.
MAX_LINE_BYTES = 64 * 1024
MAX_COMPONENTS = 2_000
MAX_DEPTH = 20

# Components worth rendering, and components that only look like content.
# VALARM's own DESCRIPTION is the word "Reminder" and VTIMEZONE's child
# components carry DTSTART values in 1601 — flattened, either would report a
# hearing that does not exist.
RENDERABLE = frozenset({"VEVENT", "VTODO", "VJOURNAL"})
SKIPPED = frozenset({"VALARM", "VTIMEZONE", "STANDARD", "DAYLIGHT", "VFREEBUSY"})

# Machine bookkeeping: real properties, no legal content, and together they
# would triple the size of the index.
IGNORED_PROPERTIES = frozenset(
    {
        "DTSTAMP",
        "CREATED",
        "LAST-MODIFIED",
        "UID",
        "SEQUENCE",
        "CLASS",
        "PRIORITY",
        "TRANSP",
        "GEO",
        "PRODID",
        "VERSION",
        "CALSCALE",
        "ATTACH",  # can inline megabytes of base64
    }
)

# Only these carry comma-separated multiple values.  Splitting LOCATION on
# commas would chop "Záhradnícka 10, Bratislava" in half.
MULTI_VALUE = frozenset({"EXDATE", "RDATE", "CATEGORIES", "RESOURCES"})

_CHARSETS = ("utf-8", "windows-1250", "iso-8859-2", "latin-1")


@dataclass
class Component:
    """One VEVENT/VTODO/VJOURNAL, as the properties that were found on it."""

    name: str
    properties: dict[str, list[tuple[dict[str, str], str]]] = field(default_factory=dict)

    def add(self, prop: str, params: dict[str, str], value: str) -> None:
        self.properties.setdefault(prop, []).append((params, value))

    def first(self, prop: str) -> tuple[dict[str, str], str] | None:
        found = self.properties.get(prop)
        return found[0] if found else None


# ---------------------------------------------------------------------------
# Unfolding — on bytes, before any decoding
# ---------------------------------------------------------------------------


def unfold(data: bytes) -> list[bytes]:
    """Physical lines joined into logical ones, per RFC 5545 §3.1.

    Folding is permitted at 75 *octets*, which splits UTF-8 multi-byte
    sequences down the middle.  Decoding first and unfolding after turns every
    folded Slovak word into replacement characters, so this works on bytes and
    the caller decodes what comes out.

    A continuation line begins with exactly one space or tab, and rejoining
    removes the newline and that one character — a second space is content.
    """
    lines: list[bytes] = []
    current = bytearray()
    truncated = False

    for physical in _physical_lines(data):
        if physical[:1] in (b" ", b"\t"):
            if not truncated and len(current) < MAX_LINE_BYTES:
                current.extend(physical[1:])
            continue
        if current or truncated:
            lines.append(bytes(current))
        current = bytearray(physical[:MAX_LINE_BYTES])
        truncated = len(physical) > MAX_LINE_BYTES

    if current or truncated:
        lines.append(bytes(current))
    return lines


def _physical_lines(data: bytes):
    """Yield lines however the file ends them.

    Iterating a BytesIO splits on LF alone, so a file written with bare CR —
    classic Mac exporters still produce them — arrives as one enormous line
    and nothing in it is ever read.
    """
    for chunk in io.BytesIO(data):
        stripped = chunk.rstrip(b"\r\n")
        if b"\r" in stripped:
            yield from stripped.split(b"\r")
        else:
            yield stripped


def decode_line(raw: bytes) -> str:
    for charset in _CHARSETS:
        try:
            return raw.decode(charset)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")  # pragma: no cover - latin-1 never raises


# ---------------------------------------------------------------------------
# Splitting — quote-aware, because a parameter value may contain a colon
# ---------------------------------------------------------------------------


def split_property(line: str) -> tuple[str, dict[str, str], str] | None:
    """``NAME;PARAM=value:content`` into its three parts.

    Not ``line.split(":", 1)``: a DQUOTE-quoted parameter may contain a colon,
    and court mail really does carry ``CN="Súd: Okresný súd BA I"``.
    """
    cut = _unquoted_index(line, ":")
    if cut is None:
        return None

    head, value = line[:cut], line[cut + 1 :]
    parts = _split_unquoted(head, ";")
    if not parts or not parts[0]:
        return None

    # A group prefix ("item1.SUMMARY") is legal; the name is the last segment.
    name = parts[0].rsplit(".", 1)[-1].strip().upper()
    params: dict[str, str] = {}
    for parameter in parts[1:]:
        key, _, raw = parameter.partition("=")
        params[key.strip().upper()] = raw.strip().strip('"')
    return name, params, value


def _unquoted_index(text: str, char: str) -> int | None:
    in_quotes = False
    for index, character in enumerate(text):
        if character == '"':
            in_quotes = not in_quotes
        elif character == char and not in_quotes:
            return index
    return None


def _split_unquoted(text: str, separator: str) -> list[str]:
    parts: list[str] = []
    buffer: list[str] = []
    in_quotes = False
    for character in text:
        if character == '"':
            in_quotes = not in_quotes
            buffer.append(character)
        elif character == separator and not in_quotes:
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(character)
    parts.append("".join(buffer))
    return parts


def unescape(value: str) -> str:
    r"""Decode ``\\ \; \, \n \N`` in one left-to-right pass.

    Chained ``.replace()`` calls get this wrong: ``C:\\nazov`` is an escaped
    backslash followed by the letter n, but replacing ``\n`` first turns it
    into a newline.
    """
    out: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character != "\\" or index + 1 >= len(value):
            out.append(character)
            index += 1
            continue
        following = value[index + 1]
        out.append("\n" if following in "nN" else following)
        index += 2
    return "".join(out)


def split_values(value: str) -> list[str]:
    """Split on unescaped commas only."""
    parts: list[str] = []
    buffer: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == "\\" and index + 1 < len(value):
            buffer.append(value[index : index + 2])
            index += 2
            continue
        if character == ",":
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(character)
        index += 1
    parts.append("".join(buffer))
    return [p for p in parts if p]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_DATE_TIME = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$")
_DATE_ONLY = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def render_datetime(params: dict[str, str], value: str, *, end_of_all_day: bool = False) -> str:
    """A date as written in the file, with its zone named but never converted.

    TZID is frequently not an IANA zone — Exchange writes "Central Europe
    Standard Time" — so feeding it to :mod:`zoneinfo` raises on real mail.
    Echoing it is also the more honest rendering: the file says half past nine
    local, so the text should say half past nine local.
    """
    value = value.strip()
    zone = params.get("TZID", "").lstrip("/")

    match = _DATE_TIME.match(value)
    if match:
        year, month, day, hour, minute, _second, utc = match.groups()
        stamp = f"{year}-{month}-{day} {hour}:{minute}"
        if utc:
            return f"{stamp} UTC"
        if zone:
            return f"{stamp} ({zone})"
        return f"{stamp} (floating local time)"

    match = _DATE_ONLY.match(value)
    if match:
        year, month, day = (int(g) for g in match.groups())
        try:
            day_value = date(year, month, day)
        except ValueError:
            return value
        if end_of_all_day:
            # DTEND of an all-day event is exclusive (RFC 5545 §3.8.2.2).  A
            # one-day deadline written 15th–16th is the 15th; printing the
            # range tells a lawyer the wrong date.
            day_value -= timedelta(days=1)
        return f"{day_value.isoformat()} (all day)"

    return value


def render_person(params: dict[str, str], value: str) -> str:
    address = value.strip()
    if address.lower().startswith("mailto:"):
        address = address[7:]
    name = params.get("CN", "").strip()
    rendered = f"{name} <{address}>" if name and address else name or address

    marks = [params[key] for key in ("ROLE", "PARTSTAT") if params.get(key)]
    return f"{rendered} [{', '.join(marks)}]" if marks else rendered


# Printed in this order regardless of the order in the file, so eight copies of
# one invitation from three clients normalise to identical text.
_LAYOUT: tuple[tuple[str, str], ...] = (
    ("SUMMARY", "SUMMARY"),
    ("DTSTART", "START"),
    ("DTEND", "END"),
    ("DUE", "DUE"),
    ("DURATION", "DURATION"),
    ("LOCATION", "LOCATION"),
    ("ORGANIZER", "ORGANIZER"),
    ("ATTENDEE", "ATTENDEE"),
    ("STATUS", "STATUS"),
    ("RECURRENCE-ID", "INSTANCE OF A SERIES"),
    ("RRULE", "RECURS"),
    ("EXDATE", "EXDATE"),
    ("RDATE", "RDATE"),
    ("CATEGORIES", "CATEGORIES"),
    ("RESOURCES", "RESOURCES"),
    ("URL", "URL"),
    ("COMMENT", "COMMENT"),
    ("CONTACT", "CONTACT"),
)

_DATE_PROPERTIES = frozenset({"DTSTART", "DTEND", "DUE", "RECURRENCE-ID", "EXDATE", "RDATE"})
_PERSON_PROPERTIES = frozenset({"ORGANIZER", "ATTENDEE"})


def render_component(component: Component, index: int, strip_html) -> str:
    lines = [f"# {component.name} {index}"]

    for prop, label in _LAYOUT:
        for params, value in component.properties.get(prop, []):
            if not value.strip():
                continue
            if prop in _PERSON_PROPERTIES:
                lines.append(f"{label}: {render_person(params, value)}")
            elif prop in _DATE_PROPERTIES:
                pieces = split_values(value) if prop in MULTI_VALUE else [value]
                rendered = ", ".join(
                    render_datetime(params, piece, end_of_all_day=(prop == "DTEND"))
                    for piece in pieces
                )
                lines.append(f"{label}: {rendered}")
            elif prop in MULTI_VALUE:
                lines.append(f"{label}: {', '.join(unescape(p) for p in split_values(value))}")
            else:
                lines.append(f"{label}: {unescape(value)}")

    description = component.first("DESCRIPTION")
    body = unescape(description[1]) if description else ""
    if not body.strip():
        # Exchange often sends an empty plain description and the real one as
        # HTML, which is attacker-controlled and goes through the stripper.
        alternative = component.first("X-ALT-DESC")
        if alternative:
            body = strip_html(unescape(alternative[1]))
    if body.strip():
        lines.append("DESCRIPTION:")
        lines.append(body.strip())

    return "\n".join(lines)


def read_calendar(data: bytes, strip_html) -> str:
    """Every renderable component in *data*, as text.

    *strip_html* is injected rather than imported to keep this module free of
    a cycle back into :mod:`app.services.extraction`.
    """
    stack: list[str] = []
    components: list[Component] = []
    open_component: Component | None = None
    method = ""

    for raw in unfold(data):
        line = decode_line(raw).lstrip("\ufeff")
        if not line.strip():
            continue
        split = split_property(line)
        if split is None:
            continue
        name, params, value = split

        if name == "BEGIN":
            child = value.strip().upper()
            if len(stack) < MAX_DEPTH:
                stack.append(child)
            if child in RENDERABLE:
                # These cannot legally nest; a file with 5000 consecutive
                # BEGIN:VEVENT lines is hostile, not deeply structured.
                if open_component is not None:
                    components.append(open_component)
                    open_component = None
                # Past the cap nothing more is opened, so the properties that
                # follow are dropped rather than piled onto the last one.
                if len(components) < MAX_COMPONENTS:
                    open_component = Component(child)
            continue

        if name == "END":
            closing = value.strip().upper()
            if stack and stack[-1] == closing:
                stack.pop()
            if closing in RENDERABLE and open_component is not None:
                components.append(open_component)
                open_component = None
            continue

        if any(part in SKIPPED for part in stack):
            continue
        if name in IGNORED_PROPERTIES:
            continue

        if name == "METHOD" and open_component is None:
            method = value.strip().upper()
            continue

        if open_component is None:
            continue
        if params.get("ENCODING", "").upper() == "QUOTED-PRINTABLE":
            value = _decode_quoted_printable(value, params.get("CHARSET", ""))
        open_component.add(name, params, value)

    # A file truncated in transit never sends its END; keeping what arrived is
    # better than discarding an invitation because the last line is missing.
    if open_component is not None and len(components) < MAX_COMPONENTS:
        components.append(open_component)

    blocks = [
        render_component(component, index, strip_html)
        for index, component in enumerate(components, start=1)
    ]
    if not blocks:
        return ""
    header = f"CALENDAR METHOD: {method}\n\n" if method else ""
    return header + "\n\n".join(blocks)


def _decode_quoted_printable(value: str, charset: str) -> str:
    """Ancient Outlook writes vCalendar 1.0 with quoted-printable values."""
    try:
        decoded = quopri.decodestring(value.encode("latin-1"))
    except (ValueError, UnicodeEncodeError):
        return value
    for candidate in (charset.lower(), *_CHARSETS):
        if not candidate:
            continue
        try:
            return decoded.decode(candidate)
        except (UnicodeDecodeError, LookupError):
            continue
    return value  # pragma: no cover - latin-1 in the chain never raises
