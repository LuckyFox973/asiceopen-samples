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
# Legacy Office and other binaries we deliberately do not guess at.
KNOWN_UNSUPPORTED = {
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/zip",
    "application/x-rar-compressed",
    "application/octet-stream",
}


def detect_kind(mime_type: str | None, filename: str | None, data: bytes) -> str:
    """Decide how to parse, trusting content over a often-wrong MIME type."""
    # Magic bytes first: mail clients mislabel attachments constantly.
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        office = _office_kind_from_zip(data)
        if office:
            return office

    mime = (mime_type or "").split(";")[0].strip().lower()
    suffix = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""

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
}


def extract(
    data: bytes, mime_type: str | None = None, filename: str | None = None
) -> ExtractionResult:
    """Extract text from *data*. Never raises — the status carries the outcome."""
    if not data:
        return ExtractionResult(ExtractionStatus.EMPTY, method="none")

    kind = detect_kind(mime_type, filename, data)

    if kind == "image":
        return ExtractionResult(
            ExtractionStatus.NEEDS_OCR,
            method="none",
            error="Image attachment: OCR is not enabled.",
        )
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
