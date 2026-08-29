"""Extraction against genuinely generated documents."""

from __future__ import annotations

import io

import pytest

from app.services.extraction import (
    MAX_TEXT_CHARS,
    ExtractionStatus,
    detect_kind,
    extract,
    image_size,
    normalise,
    strip_html,
)
from tests.fixtures.documents import (
    make_broken_zip,
    make_docx,
    make_gif,
    make_jpeg,
    make_pdf,
    make_png,
    make_scanned_pdf,
    make_xlsx,
)

SLOVAK = "Rozhodnutie spravcu dane o danovej kontrole DPH za rok 2025"


class TestPdf:
    def test_text_is_extracted(self):
        result = extract(make_pdf([SLOVAK]), "application/pdf", "Rozhodnutie.pdf")
        assert result.status is ExtractionStatus.EXTRACTED
        assert "danovej kontrole" in result.text
        assert result.page_count == 1
        assert result.method == "pypdf"

    def test_multiple_pages_are_joined(self):
        result = extract(make_pdf(["Strana jedna textu", "Strana dva textu"]), "application/pdf")
        assert result.page_count == 2
        assert "Strana jedna" in result.text
        assert "Strana dva" in result.text

    def test_scan_without_a_text_layer_is_flagged_for_ocr(self):
        result = extract(make_scanned_pdf(), "application/pdf", "sken.pdf")
        assert result.status is ExtractionStatus.NEEDS_OCR
        assert result.page_count == 1

    def test_corrupt_pdf_fails_cleanly(self):
        result = extract(b"%PDF-1.4\nthis is not really a pdf", "application/pdf", "x.pdf")
        assert result.status is ExtractionStatus.FAILED
        assert result.error

    def test_detected_by_content_despite_a_wrong_mime_type(self):
        result = extract(make_pdf([SLOVAK]), "application/octet-stream", "zmluva.bin")
        assert result.status is ExtractionStatus.EXTRACTED
        assert "danovej" in result.text


class TestDocx:
    def test_paragraphs_are_extracted(self):
        data = make_docx(["Vazeny pan kolega,", SLOVAK, "S pozdravom"])
        result = extract(
            data,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "podanie.docx",
        )
        assert result.status is ExtractionStatus.EXTRACTED
        assert "Vazeny pan kolega" in result.text
        assert "S pozdravom" in result.text

    def test_table_contents_are_not_lost(self):
        data = make_docx(
            ["Prehlad faktur:"],
            table=[["Cislo", "Suma"], ["FA-2025-001", "1 250,00 EUR"]],
        )
        result = extract(data, filename="prehlad.docx")
        assert "FA-2025-001" in result.text
        assert "1 250,00 EUR" in result.text

    def test_detected_from_zip_content_without_a_mime_type(self):
        assert detect_kind(None, None, make_docx(["ahoj"])) == "docx"

    def test_empty_document_reports_empty(self):
        assert extract(make_docx([]), filename="prazdny.docx").status is ExtractionStatus.EMPTY


class TestXlsx:
    def test_cells_and_sheet_names_are_extracted(self):
        data = make_xlsx(
            {
                "Faktury": [["Cislo", "Suma"], ["FA-001", 1250.5]],
                "Poznamky": [["CMR listy boli duplicitne"]],
            }
        )
        result = extract(data, filename="prehlad.xlsx")
        assert result.status is ExtractionStatus.EXTRACTED
        assert "# Faktury" in result.text
        assert "FA-001" in result.text
        assert "1250.5" in result.text
        assert "CMR listy boli duplicitne" in result.text

    def test_empty_cells_are_skipped(self):
        result = extract(make_xlsx({"List": [["a", None, "b"], [None, None, None]]}))
        assert "a | b" in result.text

    def test_detected_from_zip_content(self):
        assert detect_kind(None, None, make_xlsx({"S": [["x"]]})) == "xlsx"


class TestTextFormats:
    def test_utf8_text(self):
        result = extract("Kasačná sťažnosť".encode(), "text/plain", "poznamka.txt")
        assert result.text == "Kasačná sťažnosť"

    def test_windows_1250_text_is_decoded(self):
        text = "Žiadosť o predĺženie lehoty"
        result = extract(text.encode("windows-1250"), "text/plain", "ziadost.txt")
        assert result.text == text

    def test_html_tags_are_stripped(self):
        html = b"<html><body><h1>Vyzva</h1><p>Spravca dane</p><script>x()</script></body></html>"
        result = extract(html, "text/html", "vyzva.html")
        assert "Vyzva" in result.text
        assert "Spravca dane" in result.text
        assert "<" not in result.text
        assert "x()" not in result.text

    def test_csv_rows_are_flattened(self):
        result = extract(b"cislo,suma\nFA-001,1250\n", "text/csv", "faktury.csv")
        assert "cislo | suma" in result.text
        assert "FA-001 | 1250" in result.text

    def test_json_is_kept_as_text(self):
        result = extract(b'{"vec": "kontrola"}', "application/json", "data.json")
        assert result.status is ExtractionStatus.EXTRACTED
        assert "kontrola" in result.text


class TestUnsupported:
    def test_a_page_sized_image_is_flagged_for_ocr(self):
        page = make_png(1700, 2200) + b"\x00" * 60_000
        result = extract(page, "image/png", "sken.png")
        assert result.status is ExtractionStatus.NEEDS_OCR

    def test_legacy_doc_is_unsupported_not_failed(self):
        result = extract(b"\xd0\xcf\x11\xe0" + b"\x00" * 50, "application/msword", "stare.doc")
        assert result.status is ExtractionStatus.UNSUPPORTED
        assert "stare.doc" in result.error

    def test_a_zip_of_documents_is_walked_rather_than_refused(self):
        """Archives used to be dropped whole; their contents are the point."""
        result = extract(make_broken_zip(), "application/zip", "archiv.zip")
        assert result.status is ExtractionStatus.EXTRACTED
        assert "not an office document" in result.text

    def test_empty_input(self):
        assert extract(b"").status is ExtractionStatus.EMPTY

    def test_unknown_binary(self):
        result = extract(b"\x00\x01\x02\x03", "application/x-mystery", "x.bin")
        assert result.status is ExtractionStatus.UNSUPPORTED


class TestNormalisation:
    def test_paragraphs_survive_but_runs_of_blanks_collapse(self):
        assert normalise("a\n\n\n\n\nb") == "a\n\nb"

    def test_horizontal_whitespace_collapses(self):
        assert normalise("a     \t  b") == "a b"

    def test_crlf_and_null_bytes_are_removed(self):
        assert normalise("a\r\nb\x00c") == "a\nbc"

    def test_empty_input(self):
        assert normalise("") == ""

    def test_strip_html_turns_block_ends_into_newlines(self):
        assert "\n" in strip_html("<p>one</p><p>two</p>")

    def test_entities_are_decoded(self):
        assert "&" in strip_html("<p>a &amp; b</p>")


class TestLimits:
    def test_very_long_text_is_truncated_and_flagged(self):
        huge = ("slovo " * (MAX_TEXT_CHARS // 3)).encode()
        result = extract(huge, "text/plain", "velky.txt")
        assert result.truncated is True
        assert result.char_count == MAX_TEXT_CHARS

    def test_normal_text_is_not_flagged(self):
        assert extract(b"kratky text pre kontrolu", "text/plain").truncated is False


@pytest.mark.parametrize(
    ("mime", "filename", "expected"),
    [
        ("application/pdf", None, "pdf"),
        (None, "x.pdf", "pdf"),
        (None, "x.DOCX", "docx"),
        (None, "x.xlsm", "xlsx"),
        ("text/html", None, "html"),
        ("image/jpeg", None, "image"),
        (None, "x.txt", "text"),
        (None, "x.unknownext", "unknown"),
    ],
)
def test_detect_kind(mime, filename, expected):
    assert detect_kind(mime, filename, b"") == expected


class TestEncryptedPdf:
    """pypdf reports a wrong password by return value, not by raising."""

    @staticmethod
    def _locked_pdf(password: str = "s3cret") -> bytes:
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.encrypt(password)
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()

    def test_a_locked_pdf_is_reported_as_encrypted(self):
        result = extract(self._locked_pdf(), mime_type="application/pdf", filename="Faktura.pdf")
        assert result.status is ExtractionStatus.ENCRYPTED

    def test_the_message_says_a_password_is_needed(self):
        """`File has not been decrypted` told the owner nothing actionable."""
        result = extract(self._locked_pdf(), mime_type="application/pdf", filename="Faktura.pdf")
        assert "password" in (result.error or "").lower()
        assert "not been decrypted" not in (result.error or "")

    def test_an_empty_user_password_still_opens(self):
        """ "Protected" documents often carry no user password at all."""
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.encrypt(user_password="", owner_password="owner-only")
        buffer = io.BytesIO()
        writer.write(buffer)

        result = extract(buffer.getvalue(), mime_type="application/pdf", filename="Open.pdf")
        assert result.status is not ExtractionStatus.ENCRYPTED


class TestImageClassification:
    """A photographed contract and a signature logo are both images with no
    text layer. Reporting both as "needs OCR" buries the one that matters."""

    def test_a_tracking_pixel_is_not_a_document(self):
        result = extract(make_gif(1, 1), mime_type="image/gif", filename="cleardot.gif")
        assert result.status is ExtractionStatus.NOT_A_DOCUMENT

    def test_a_signature_logo_is_not_a_document(self):
        result = extract(make_png(120, 40), mime_type="image/png", filename="logo.png")
        assert result.status is ExtractionStatus.NOT_A_DOCUMENT
        assert "120x40" in (result.error or "")

    def test_a_photographed_page_is_worth_ocr(self):
        page = make_jpeg(2400, 3200) + b"\x00" * 50_000
        result = extract(page, mime_type="image/jpeg", filename="IMG_0085.JPG")
        assert result.status is ExtractionStatus.NEEDS_OCR

    def test_an_smime_signature_block_is_not_a_document(self):
        result = extract(
            b"\x30\x82\x04\x00 fake pkcs7",
            mime_type="application/x-pkcs7-signature",
            filename="smime.p7s",
        )
        assert result.status is ExtractionStatus.NOT_A_DOCUMENT

    def test_an_unreadable_header_is_given_the_benefit_of_the_doubt(self):
        """Guessing wrong must hide no scan; size is the only other signal."""
        result = extract(b"\xff\xd8" + b"\x00" * 60_000, mime_type="image/jpeg", filename="x.jpg")
        assert result.status is ExtractionStatus.NEEDS_OCR


class TestImageSize:
    @pytest.mark.parametrize("width,height", [(1, 1), (120, 40), (2400, 3200)])
    def test_png_dimensions_are_read_from_the_header(self, width, height):
        assert image_size(make_png(width, height)) == (width, height)

    @pytest.mark.parametrize("width,height", [(1, 1), (640, 480)])
    def test_gif_dimensions_are_read_from_the_header(self, width, height):
        assert image_size(make_gif(width, height)) == (width, height)

    @pytest.mark.parametrize("width,height", [(16, 16), (2400, 3200)])
    def test_jpeg_dimensions_come_from_the_frame_marker(self, width, height):
        assert image_size(make_jpeg(width, height)) == (width, height)

    def test_an_unknown_format_reports_nothing_rather_than_guessing(self):
        assert image_size(b"not an image at all") is None
