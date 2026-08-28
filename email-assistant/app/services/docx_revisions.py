"""Revision-aware reading of Word documents.

``python-docx`` exposes ``paragraph.text``, which silently drops **both** sides
of a tracked change: deleted text (fair enough) *and inserted text* — which is
what the document currently says. A contract whose penalty was changed from
5000 to 2000 EUR under review reads back as "Zmluvna pokuta je ." with no
number at all, and no search would ever find it.

So the XML is read directly. Three texts come out of one file:

* **current** — what the document says now: unchanged text plus insertions,
  without deletions. This is what gets indexed.
* **deleted** — what a revision removed. Kept separately, because "what did
  they take out?" is a real question about a draft.
* **comments** — margin comments, where the substance of a negotiation often is.

Nothing here needs a dependency: it is stdlib XML over the .docx ZIP.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

DOCUMENT_PART = "word/document.xml"
COMMENTS_PART = "word/comments.xml"

# Elements that end a visual line, so text does not run together.
BLOCK_TAGS = {f"{W}tr"}
CELL_TAG = f"{W}tc"


@dataclass
class Revision:
    kind: str  # "insertion" | "deletion"
    author: str | None
    date: str | None
    text: str


@dataclass
class Comment:
    author: str | None
    date: str | None
    text: str


@dataclass
class DocxContent:
    current_text: str = ""
    deleted_text: str = ""
    comments: list[Comment] = field(default_factory=list)
    revisions: list[Revision] = field(default_factory=list)

    @property
    def has_revisions(self) -> bool:
        return bool(self.revisions)

    @property
    def insertion_count(self) -> int:
        return sum(1 for r in self.revisions if r.kind == "insertion")

    @property
    def deletion_count(self) -> int:
        return sum(1 for r in self.revisions if r.kind == "deletion")

    @property
    def revision_authors(self) -> list[str]:
        seen = {r.author for r in self.revisions if r.author}
        seen |= {c.author for c in self.comments if c.author}
        return sorted(seen)

    def summary(self) -> str | None:
        """One line a person can read, or None when the document is clean."""
        if not self.revisions and not self.comments:
            return None
        parts: list[str] = []
        if self.insertion_count:
            parts.append(f"{self.insertion_count} insertion(s)")
        if self.deletion_count:
            parts.append(f"{self.deletion_count} deletion(s)")
        if self.comments:
            parts.append(f"{len(self.comments)} comment(s)")
        who = ", ".join(self.revision_authors)
        return f"{'; '.join(parts)}" + (f" by {who}" if who else "")


def read_docx(data: bytes) -> DocxContent:
    """Read a .docx, keeping track of what was changed and by whom."""
    content = DocxContent()
    try:
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
            names = set(archive.namelist())
            if DOCUMENT_PART not in names:
                return content
            document_xml = archive.read(DOCUMENT_PART)
            comments_xml = archive.read(COMMENTS_PART) if COMMENTS_PART in names else None
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        raise ValueError(f"Not a readable .docx: {exc}") from exc

    root = ElementTree.fromstring(document_xml)
    body = root.find(f"{W}body")
    if body is not None:
        _walk(body, content, in_ins=None, in_del=None)

    content.current_text = _tidy(content.current_text)
    content.deleted_text = _tidy(content.deleted_text)

    if comments_xml:
        content.comments = _read_comments(comments_xml)
    return content


def _walk(
    element: ElementTree.Element,
    content: DocxContent,
    in_ins: ElementTree.Element | None,
    in_del: ElementTree.Element | None,
    in_cell: bool = False,
) -> None:
    """Depth-first walk that remembers whether it is inside a revision.

    *in_cell* keeps a table row on one line: a paragraph inside a cell must
    not break the row, or a two-column table reads as a list of orphaned
    values with the pairing lost.
    """
    for child in element:
        tag = child.tag

        if tag == f"{W}ins":
            _record(content, child, "insertion", _collect_text(child, f"{W}t"))
            _walk(child, content, in_ins=child, in_del=in_del, in_cell=in_cell)
            continue

        if tag == f"{W}del":
            _record(content, child, "deletion", _collect_text(child, f"{W}delText"))
            _walk(child, content, in_ins=in_ins, in_del=child, in_cell=in_cell)
            continue

        if tag == f"{W}t":
            # Plain text, or text inside an insertion: both are current.
            if in_del is None:
                content.current_text += child.text or ""
            continue

        if tag == f"{W}delText":
            content.deleted_text += (child.text or "") + " "
            continue

        if tag in (f"{W}tab",):
            content.current_text += "\t"
            continue

        if tag in (f"{W}br", f"{W}cr"):
            content.current_text += "\n"
            continue

        _walk(
            child,
            content,
            in_ins=in_ins,
            in_del=in_del,
            in_cell=in_cell or tag == CELL_TAG,
        )

        if tag == CELL_TAG:
            # Cells are separated so a table row stays readable as one line.
            content.current_text += " | "
        elif tag == f"{W}p":
            # A paragraph ends a line, unless it is one line of a table cell.
            content.current_text += " " if in_cell else "\n"
        elif tag in BLOCK_TAGS:
            content.current_text += "\n"


def _collect_text(element: ElementTree.Element, text_tag: str) -> str:
    return "".join(node.text or "" for node in element.iter(text_tag))


def _record(content: DocxContent, element: ElementTree.Element, kind: str, text: str) -> None:
    content.revisions.append(
        Revision(
            kind=kind,
            author=element.get(f"{W}author"),
            date=element.get(f"{W}date"),
            text=text.strip(),
        )
    )


def _read_comments(comments_xml: bytes) -> list[Comment]:
    try:
        root = ElementTree.fromstring(comments_xml)
    except ElementTree.ParseError:
        return []

    comments: list[Comment] = []
    for node in root.iter(f"{W}comment"):
        text = " ".join(
            (t.text or "").strip() for t in node.iter(f"{W}t") if (t.text or "").strip()
        )
        if text:
            comments.append(
                Comment(
                    author=node.get(f"{W}author"),
                    date=node.get(f"{W}date"),
                    text=text,
                )
            )
    return comments


def _tidy(text: str) -> str:
    lines = [
        " ".join(line.replace(" | ", " | ").split()).strip(" |").strip()
        for line in text.split("\n")
    ]
    return "\n".join(line for line in lines if line)
