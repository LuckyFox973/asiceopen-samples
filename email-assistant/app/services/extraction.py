"""Extracting readable text from attachments.

Deterministic parsing only — no model is asked to read a document that a
library can read exactly.  That keeps the cost at zero and the output
reproducible, and it gives the AI layer (phase 3) real text to work from
rather than a summary of a guess.

Text is stored against the **blob**, not the attachment: a contract circulated
twenty times is parsed once.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from enum import StrEnum

from app.core.logging import get_logger

log = get_logger(__name__)

# Beyond this a document is stored truncated: the tail of a 500-page scan adds
# nothing to search but a great deal to the row size.
MAX_TEXT_CHARS = 2_000_000
# Below this, a "successful" extraction is really a scanned page with no text
# layer.  Flagged for OCR rather than silently recorded as an empty document.
MIN_MEANINGFUL_CHARS = 24


class ExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    EMPTY = "empty"  # parsed fine, genuinely contains no text
    NEEDS_OCR = "needs_ocr"  # almost certainly a scan
    ENCRYPTED = "encrypted"  # readable, but locked with a password we do not have
    NOT_A_DOCUMENT = "not_a_document"  # transport plumbing, not something a person sent
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


@dataclass(slots=True)
class ExtractionResult:
    status: ExtractionStatus
    text: str = ""
    method: str = ""
    page_count: int | None = None
    error: str | None = None
    truncated: bool = False

    # Tracked changes and comments — populated for .docx, empty elsewhere.
    deleted_text: str = ""
    comment_text: str = ""
    revision_count: int = 0
    revision_authors: list[str] = field(default_factory=list)
    revision_summary: str | None = None

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def has_revisions(self) -> bool:
        return self.revision_count > 0


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

PDF_TYPES = {"application/pdf", "application/x-pdf"}
DOCX_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
XLSX_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel.sheet.macroenabled.12",
}
TEXT_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/tab-separated-values",
    "application/json",
    "application/xml",
    "text/xml",
}
HTML_TYPES = {"text/html", "application/xhtml+xml"}
IMAGE_PREFIX = "image/"
# Legacy Office and other binaries we deliberately do not guess at.  Note that
# the zip types are absent: archives are walked now, and listing them here
# would send a container whose header is not at byte zero to UNSUPPORTED.
KNOWN_UNSUPPORTED = {
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/x-rar-compressed",
}

CONTAINER_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "application/vnd.etsi.asic-e+zip",
    "application/vnd.etsi.asic-s+zip",
}
# mimetypes maps .sce to Scilab and .scs to an SCVP response, so the Slovak
# container suffixes are named here rather than deferred to any table.
CONTAINER_SUFFIXES = {"zip", "asice", "asics", "sce", "scs", "isdocx"}

XML_DOCUMENT_TYPES = {"application/xop+xml", "application/isdoc+xml"}
XML_DOCUMENT_SUFFIXES = {"isdoc"}

CALENDAR_TYPES = {"text/calendar", "application/ics", "text/x-vcalendar"}

# Signature blocks and the like: produced by the mail system, carrying nothing
# a person wrote.  Reporting them as unreadable asks the owner to solve a
# problem that is not one.
NOT_A_DOCUMENT_TYPES = {
    "application/pkcs7-signature",
    "application/x-pkcs7-signature",
    "application/pkcs7-mime",
    "application/pgp-signature",
}
NOT_A_DOCUMENT_SUFFIXES = {"p7s", "p7m", "asc"}
# Gmail's transparent spacer, and the like.
TRACKING_PIXEL_NAMES = {"cleardot.gif", "spacer.gif", "pixel.gif", "1x1.gif", "blank.gif"}
# Below this an image is a logo or an icon; there is no document in it to read.
MIN_OCR_WORTHY_PIXELS = 160
MIN_OCR_WORTHY_BYTES = 20_000


def detect_kind(mime_type: str | None, filename: str | None, data: bytes) -> str:
    """Decide how to parse, trusting content over a often-wrong MIME type."""
    # Magic bytes first: mail clients mislabel attachments constantly.
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        office = _office_kind_from_zip(data)
        if office:
            return office
        from app.services.containers import container_kind

        if container_kind(data) is not None:
            return "container"

    mime = (mime_type or "").split(";")[0].strip().lower()
    suffix = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""

    if mime in NOT_A_DOCUMENT_TYPES or suffix in NOT_A_DOCUMENT_SUFFIXES:
        return "not_a_document"
    if mime in CALENDAR_TYPES or suffix == "ics":
        return "calendar"
    if mime in XML_DOCUMENT_TYPES or suffix in XML_DOCUMENT_SUFFIXES:
        return "xmldoc"
    if mime in CONTAINER_TYPES or suffix in CONTAINER_SUFFIXES:
        # A container may carry prepended bytes, so the magic-byte gate above
        # can miss one that zipfile opens perfectly well.
        return "container" if _looks_like_zip(data) else "unknown"
    if mime in PDF_TYPES or suffix == "pdf":
        return "pdf"
    if mime in DOCX_TYPES or suffix == "docx":
        return "docx"
    if mime in XLSX_TYPES or suffix in {"xlsx", "xlsm"}:
        return "xlsx"
    if mime in HTML_TYPES or suffix in {"html", "htm"}:
        return "html"
    if mime in TEXT_TYPES or suffix in {"txt", "md", "csv", "tsv", "json", "xml", "log"}:
        return "text"
    if mime.startswith(IMAGE_PREFIX) or suffix in {"png", "jpg", "jpeg", "tif", "tiff"}:
        return "image"
    if mime in KNOWN_UNSUPPORTED:
        return "unsupported"
    return "unknown"


def image_size(data: bytes) -> tuple[int, int] | None:
    """Pixel dimensions from the file header, with no imaging dependency.

    Only the three formats that actually turn up in mail. Returning None means
    "could not tell", which is treated as "might be a document" — the safe
    direction, since the cost of guessing wrong is hiding a real scan.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return (
            int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"),
        )
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return (
            int.from_bytes(data[6:8], "little"),
            int.from_bytes(data[8:10], "little"),
        )
    if data[:2] == b"\xff\xd8":
        return _jpeg_size(data)
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Walk the segment chain to the start-of-frame marker."""
    index = 2
    limit = len(data)
    while index + 9 < limit:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        # Standalone markers carry no length; restart markers and padding too.
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            index += 2
            continue
        length = int.from_bytes(data[index + 2 : index + 4], "big")
        if length < 2:
            return None
        # SOF0-SOF15, excluding the four that are not frame headers.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        index += 2 + length
    return None


def _classify_image(data: bytes, filename: str | None) -> ExtractionResult:
    """Is this a document someone photographed, or furniture from a signature?

    Both are images with no text layer, and calling both "needs OCR" buries
    the two scans that matter under twenty logos and a tracking pixel.
    """
    name = (filename or "").strip().lower()
    if name in TRACKING_PIXEL_NAMES:
        return ExtractionResult(
            ExtractionStatus.NOT_A_DOCUMENT,
            method="none",
            error="Tracking pixel embedded by the mail client.",
        )

    size = image_size(data)
    if size is not None:
        width, height = size
        if width and height and (width < MIN_OCR_WORTHY_PIXELS or height < MIN_OCR_WORTHY_PIXELS):
            return ExtractionResult(
                ExtractionStatus.NOT_A_DOCUMENT,
                method="none",
                error=f"Decorative image, {width}x{height} — too small to hold a document.",
            )
    if len(data) < MIN_OCR_WORTHY_BYTES:
        return ExtractionResult(
            ExtractionStatus.NOT_A_DOCUMENT,
            method="none",
            error=f"Decorative image, {len(data):,} bytes — too small to hold a document.",
        )

    return ExtractionResult(
        ExtractionStatus.NEEDS_OCR,
        method="none",
        error="Photograph or scan: OCR is not enabled, so its text is not searchable.",
    )


def _looks_like_zip(data: bytes) -> bool:
    return zipfile.is_zipfile(io.BytesIO(data))


def _office_kind_from_zip(data: bytes) -> str | None:
    """A .docx and .xlsx are both ZIPs; the part names tell them apart."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return None
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return None


# ---------------------------------------------------------------------------
# Per-format extractors
# ---------------------------------------------------------------------------


def extract_pdf(data: bytes) -> ExtractionResult:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            # An empty user password is common on "protected" documents, and
            # pypdf reports a wrong one by RETURNING a falsy PasswordType, not
            # by raising — so an unchecked call surfaces much later as the
            # unhelpful "File has not been decrypted".
            try:
                opened = reader.decrypt("")
            except Exception:  # noqa: BLE001 - a damaged encryption dictionary
                opened = 0
            if not opened:
                return ExtractionResult(
                    ExtractionStatus.ENCRYPTED,
                    method="pypdf",
                    error="Password protected — the password is needed to read it.",
                )
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except (PdfReadError, OSError, ValueError, KeyError) as exc:
        return ExtractionResult(ExtractionStatus.FAILED, method="pypdf", error=str(exc)[:500])
    except Exception as exc:  # noqa: BLE001 - pypdf raises broadly on damaged files
        return ExtractionResult(ExtractionStatus.FAILED, method="pypdf", error=str(exc)[:500])

    text = normalise("\n\n".join(p for p in pages if p))
    if len(text) < MIN_MEANINGFUL_CHARS and pages:
        # A PDF with pages but no text layer is a scan.
        return ExtractionResult(
            ExtractionStatus.NEEDS_OCR, text=text, method="pypdf", page_count=len(pages)
        )
    return _finish(text, "pypdf", page_count=len(pages))


def extract_docx(data: bytes) -> ExtractionResult:
    """Read a Word file, keeping insertions and recording what was removed.

    Deliberately not ``python-docx``'s ``paragraph.text``: that drops both
    sides of a tracked change, including the *inserted* text, which is what
    the document currently says.  A contract whose figure was changed under
    review would otherwise be stored with no figure at all.
    """
    from app.services.docx_revisions import read_docx

    try:
        content = read_docx(data)
    except ValueError as exc:
        return ExtractionResult(ExtractionStatus.FAILED, method="docx-xml", error=str(exc)[:500])
    except Exception as exc:  # noqa: BLE001 - malformed XML in the wild
        return ExtractionResult(ExtractionStatus.FAILED, method="docx-xml", error=str(exc)[:500])

    comment_text = "\n".join(f"[{c.author or 'unknown'}] {c.text}" for c in content.comments)
    result = _finish(normalise(content.current_text), "docx-xml")
    result.deleted_text = normalise(content.deleted_text)
    result.comment_text = normalise(comment_text)
    result.revision_count = len(content.revisions)
    result.revision_authors = content.revision_authors
    result.revision_summary = content.summary()

    # A document that is nothing but comments still carries information.
    if result.status is ExtractionStatus.EMPTY and (result.comment_text or result.deleted_text):
        result.status = ExtractionStatus.EXTRACTED
    return result


def extract_xlsx(data: bytes) -> ExtractionResult:
    import openpyxl

    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl raises broadly
        return ExtractionResult(ExtractionStatus.FAILED, method="openpyxl", error=str(exc)[:500])

    lines: list[str] = []
    try:
        for sheet in workbook.worksheets:
            lines.append(f"# {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells = [str(v).strip() for v in row if v is not None and str(v).strip()]
                if cells:
                    lines.append(" | ".join(cells))
                if len(lines) > 200_000:  # a runaway sheet must not exhaust memory
                    break
    finally:
        workbook.close()
    return _finish(normalise("\n".join(lines)), "openpyxl")


def extract_container(data: bytes) -> ExtractionResult:
    """Every document inside an archive, under its own heading.

    ASiC-E and ASiC-S — Slovak e-filings and guaranteed conversions — are ZIPs
    whose payload sits at the root beside a ``META-INF`` directory of
    signatures.  The signatures are structure; the documents are what matter.
    """
    from app.services.containers import container_kind, render, walk

    def extract_member(payload: bytes, name: str) -> tuple[str, str]:
        # Deliberately not passing the container's declared media type: the
        # manifest inside is written by whoever built the archive.
        result = extract(payload, mime_type=None, filename=name)
        return result.status.value, result.text

    kind = container_kind(data) or "zip"
    members = walk(data, extract_member)
    text = normalise(render(members))
    if not text:
        return ExtractionResult(
            ExtractionStatus.EMPTY,
            method=f"{kind}-container",
            error="Archive holds no readable document.",
        )
    return _finish(text, f"{kind}-container")


def extract_calendar(data: bytes) -> ExtractionResult:
    """A meeting invitation as text: what, when, where, and who called it."""
    from app.services.icalendar_text import read_calendar

    return _finish(normalise(read_calendar(data, strip_html)), "ics")


def extract_xml_document(data: bytes) -> ExtractionResult:
    """A structured XML document — an ISDOC invoice, a filled-in form."""
    from app.services.xml_text import read_xml

    text = read_xml(data)
    if not text:
        # Not valid XML after all; the raw text is still better than nothing.
        return extract_text_file(data)
    return _finish(normalise(text), "xml-structured")


def extract_text_file(data: bytes) -> ExtractionResult:
    for charset in ("utf-8", "windows-1250", "iso-8859-2", "latin-1"):
        try:
            return _finish(normalise(data.decode(charset)), f"text/{charset}")
        except UnicodeDecodeError:
            continue
    return _finish(normalise(data.decode("utf-8", errors="replace")), "text/replace")


def extract_html(data: bytes) -> ExtractionResult:
    result = extract_text_file(data)
    return _finish(normalise(strip_html(result.text)), "html")


def extract_csv(data: bytes) -> ExtractionResult:
    text = extract_text_file(data).text
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error:
        return _finish(text, "text/csv-raw")
    lines = [" | ".join(c.strip() for c in row if c.strip()) for row in rows]
    return _finish(normalise("\n".join(line for line in lines if line)), "csv")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t\x0b\f\r]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def strip_html(html: str) -> str:
    """Plain text from HTML, without pulling in a parser dependency."""
    import html as html_module

    text = _SCRIPT_STYLE.sub(" ", html)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = _TAG.sub(" ", text)
    return html_module.unescape(text)


def normalise(text: str) -> str:
    """Collapse whitespace without losing paragraph structure."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def _finish(text: str, method: str, page_count: int | None = None) -> ExtractionResult:
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[:MAX_TEXT_CHARS]
    if len(text) < MIN_MEANINGFUL_CHARS and not text.strip():
        return ExtractionResult(
            ExtractionStatus.EMPTY, text="", method=method, page_count=page_count
        )
    return ExtractionResult(
        ExtractionStatus.EXTRACTED,
        text=text,
        method=method,
        page_count=page_count,
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_EXTRACTORS = {
    "pdf": extract_pdf,
    "docx": extract_docx,
    "xlsx": extract_xlsx,
    "html": extract_html,
    "text": extract_text_file,
    "container": extract_container,
    "calendar": extract_calendar,
    "xmldoc": extract_xml_document,
}


def extract(
    data: bytes, mime_type: str | None = None, filename: str | None = None
) -> ExtractionResult:
    """Extract text from *data*. Never raises — the status carries the outcome."""
    if not data:
        return ExtractionResult(ExtractionStatus.EMPTY, method="none")

    kind = detect_kind(mime_type, filename, data)

    if kind == "not_a_document":
        return ExtractionResult(
            ExtractionStatus.NOT_A_DOCUMENT,
            method="none",
            error="Signature block produced by the mail system, not a document.",
        )
    if kind == "image":
        return _classify_image(data, filename)
    if kind in {"unsupported", "unknown"}:
        return ExtractionResult(
            ExtractionStatus.UNSUPPORTED,
            method="none",
            error=f"No extractor for {mime_type or 'unknown type'} ({filename or 'unnamed'})",
        )
    if kind == "text" and (filename or "").lower().endswith((".csv", ".tsv")):
        return extract_csv(data)

    extractor = _EXTRACTORS.get(kind)
    if extractor is None:  # pragma: no cover - every kind above is mapped
        return ExtractionResult(ExtractionStatus.UNSUPPORTED, method="none")
    try:
        return extractor(data)
    except Exception as exc:  # noqa: BLE001 - a bad file must not stop a batch
        log.warning("extraction.failed", kind=kind, filename=filename, error=str(exc))
        return ExtractionResult(ExtractionStatus.FAILED, method=kind, error=str(exc)[:500])
