"""Readable text from a structured XML attachment.

Written for ISDOC — the Czech and Slovak electronic-invoice standard — but
deliberately schema-agnostic, and the reason matters.  An invoice parser that
hard-codes element paths is wrong the moment it meets a version it was not
written for, and quietly: it returns fewer fields rather than an error.  The
same applies to the XML forms that come out of ÚPVS.

So every leaf element becomes ``Path/To/Element: value``.  For an invoice that
puts the number, the dates, the amounts, the IBAN and the variable symbol into
the index under names taken from the document itself, with nothing invented.

On the XML parser: ``xml.etree`` is used rather than ``defusedxml``, and this
was measured rather than assumed on Python 3.11.15.  External entities are not
resolved at all (``file:///etc/hostname`` raises "undefined entity"), and
libexpat's own amplification guard stops entity-expansion bombs — a six-level
billion-laughs is refused with "limit on input amplification factor breached".
``tests/unit/test_xml_text.py`` pins both, so a runtime that lost the guard
would fail the suite rather than the mailbox.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass

# One attachment must not become an unbounded number of index rows.
MAX_ELEMENTS = 20_000
MAX_DEPTH = 40
MAX_VALUE_CHARS = 2_000

_NAMESPACE = re.compile(r"^\{[^}]*\}")

# Namespaces that identify the document, purely so the text can name it.
KNOWN_ROOTS = {
    "http://isdoc.cz/namespace/2013": "ISDOC 6.x electronic invoice",
    "http://isdoc.cz/namespace/invoice": "ISDOC 5.x electronic invoice",
}


@dataclass(slots=True)
class XmlLeaf:
    path: str
    value: str


def local_name(tag: str) -> str:
    """``{http://…}Invoice`` -> ``Invoice``."""
    return _NAMESPACE.sub("", tag) if isinstance(tag, str) else ""


def namespace_of(tag: str) -> str:
    match = _NAMESPACE.match(tag) if isinstance(tag, str) else None
    return match.group(0)[1:-1] if match else ""


def describe_root(root: ElementTree.Element) -> str:
    """A one-line title, from the namespace when it is one we recognise."""
    name = local_name(root.tag)
    known = KNOWN_ROOTS.get(namespace_of(root.tag))
    return f"{name} — {known}" if known else name


def leaves(root: ElementTree.Element) -> list[XmlLeaf]:
    """Every element carrying text, as a path and a value.

    Iterative rather than recursive: a document nested forty thousand deep is
    a legitimate thing to receive and not a legitimate reason to blow the
    Python stack.
    """
    found: list[XmlLeaf] = []
    stack: list[tuple[ElementTree.Element, str, int]] = [(root, "", 0)]

    while stack and len(found) < MAX_ELEMENTS:
        element, prefix, depth = stack.pop()
        name = local_name(element.tag)
        path = f"{prefix}/{name}" if prefix else name

        text = (element.text or "").strip()
        children = list(element)

        if text and not children:
            found.append(XmlLeaf(path, text[:MAX_VALUE_CHARS]))
        for key, value in element.attrib.items():
            attribute = local_name(key) or key
            cleaned = (value or "").strip()
            if cleaned:
                found.append(XmlLeaf(f"{path}@{attribute}", cleaned[:MAX_VALUE_CHARS]))

        if depth < MAX_DEPTH:
            # Reversed, because a stack pops last-in first and document order
            # is what a reader expects.
            for child in reversed(children):
                stack.append((child, path, depth + 1))

    return found


def read_xml(data: bytes) -> str:
    """The document as ``Path: value`` lines, or "" if it will not parse."""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return ""

    lines = [describe_root(root), ""]
    lines.extend(f"{leaf.path}: {leaf.value}" for leaf in leaves(root))
    if len(lines) == 2:
        return ""
    return "\n".join(lines)
