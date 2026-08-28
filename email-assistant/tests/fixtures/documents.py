"""Builders for real documents used in extraction tests.

Genuine PDF/DOCX/XLSX bytes, produced by the same libraries the wild uses —
a mocked parser would prove nothing about whether extraction works.
"""

from __future__ import annotations

import io
import zipfile


def make_pdf(pages: list[str]) -> bytes:
    """A minimal but valid PDF with a real text layer.

    Hand-built rather than pulled from a generator dependency: the format is
    simple enough, and this keeps the test suite free of another library.
    """
    objects: list[bytes] = []
    page_count = len(pages)

    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(page_count))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode()
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for text in pages:
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1", "replace")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 3 0 R >> >> /Contents "
            + str(len(objects) + 2).encode()
            + b" 0 R >>"
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")

    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF".encode()
    )
    return out.getvalue()


def make_scanned_pdf() -> bytes:
    """A PDF with a page but no text layer — what a scanner produces."""
    return make_pdf([""])


def make_docx(paragraphs: list[str], table: list[list[str]] | None = None) -> bytes:
    import docx

    document = docx.Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    if table:
        added = document.add_table(rows=len(table), cols=len(table[0]))
        for row_index, row in enumerate(table):
            for col_index, value in enumerate(row):
                added.cell(row_index, col_index).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets.items():
        sheet = workbook.create_sheet(title=title)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_broken_zip() -> bytes:
    """Looks like an Office file by magic bytes, but is not one."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("random.txt", "not an office document")
    return buffer.getvalue()


def make_docx_with_revisions(
    *,
    before: str = "Zmluvna pokuta je ",
    deleted: str = "5000 EUR",
    inserted: str = "2000 EUR",
    after: str = ".",
    delete_author: str = "Advokat",
    insert_author: str = "Protistrana",
    comment: tuple[str, str] | None = None,
) -> bytes:
    """A .docx carrying real tracked changes, and optionally a comment.

    Built by injecting genuine ``w:ins`` / ``w:del`` markup into a document
    python-docx produced, because python-docx cannot author revisions itself.
    """
    import io
    import re
    import zipfile

    import docx

    document = docx.Document()
    document.add_paragraph("PLACEHOLDER")
    buffer = io.BytesIO()
    document.save(buffer)
    base = buffer.getvalue()

    def esc(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = "<w:p>"
    if before:
        body += f"<w:r><w:t xml:space='preserve'>{esc(before)}</w:t></w:r>"
    if deleted:
        body += (
            f'<w:del w:id="101" w:author="{esc(delete_author)}" '
            f'w:date="2026-08-01T10:00:00Z">'
            f"<w:r><w:delText xml:space='preserve'>{esc(deleted)}</w:delText></w:r></w:del>"
        )
    if inserted:
        body += (
            f'<w:ins w:id="102" w:author="{esc(insert_author)}" '
            f'w:date="2026-08-02T11:00:00Z">'
            f"<w:r><w:t xml:space='preserve'>{esc(inserted)}</w:t></w:r></w:ins>"
        )
    if after:
        body += f"<w:r><w:t xml:space='preserve'>{esc(after)}</w:t></w:r>"
    body += "</w:p>"

    with zipfile.ZipFile(io.BytesIO(base)) as archive:
        document_xml = archive.read("word/document.xml").decode()
        others = {
            name: archive.read(name)
            for name in archive.namelist()
            if name != "word/document.xml"
        }

    document_xml = re.sub(
        r"<w:p\b.*?</w:p>", body, document_xml, count=1, flags=re.DOTALL
    )

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document_xml)
        for name, payload in others.items():
            archive.writestr(name, payload)
        if comment:
            author, text = comment
            archive.writestr(
                "word/comments.xml",
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:comments xmlns:w="http://schemas.openxmlformats.org/'
                'wordprocessingml/2006/main">'
                f'<w:comment w:id="1" w:author="{esc(author)}" '
                f'w:date="2026-08-03T09:00:00Z">'
                f"<w:p><w:r><w:t>{esc(text)}</w:t></w:r></w:p>"
                "</w:comment></w:comments>",
            )
    return out.getvalue()
