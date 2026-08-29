"""Optical character recognition, against real images and a real recogniser.

The engine tests are skipped where tesseract is absent — they prove the
pipeline works, and there is nothing to prove without it. Everything about
how the system behaves *without* OCR installed runs everywhere, because that
is the case a user is most likely to be in.
"""

from __future__ import annotations

import subprocess

import pytest

from app.services.ocr import (
    MIN_PAGE_CHARS,
    OcrCapability,
    capability,
    forget_capability,
    read_image,
    read_pdf,
)
from tests.fixtures.documents import make_pdf

SLOVAK = "Záložné právo, náhrada škody, výška úroku"

has_tesseract = pytest.mark.skipif(
    capability().tesseract is None, reason="tesseract is not installed"
)
has_poppler = pytest.mark.skipif(
    capability().rasteriser is None, reason="poppler (pdftoppm) is not installed"
)


def rasterise(pdf: bytes, dpi: int = 300) -> bytes:
    """A PDF page as a PNG — i.e. a page with its text layer thrown away."""
    return subprocess.run(
        ["pdftoppm", "-png", "-r", str(dpi), "-f", "1", "-l", "1", "-singlefile", "-"],
        input=pdf,
        capture_output=True,
        timeout=120,
        check=True,
    ).stdout


class TestCapabilityProbe:
    def test_a_machine_with_neither_tool_says_what_is_missing(self):
        bare = OcrCapability()
        assert bare.reads_images is False
        assert bare.reads_pdfs is False
        assert bare.missing() == ["tesseract", "poppler (pdftoppm)"]

    def test_images_work_without_the_rasteriser_but_pdfs_do_not(self):
        """A photograph needs no poppler; a scanned PDF does."""
        partial = OcrCapability(tesseract="/usr/bin/tesseract", languages=("slk", "eng"))
        assert partial.reads_images is True
        assert partial.reads_pdfs is False
        assert partial.missing() == ["poppler (pdftoppm)"]

    def test_a_missing_language_pack_is_named(self):
        installed = OcrCapability(tesseract="/x", rasteriser="/y", languages=("eng",))
        assert installed.unsupported("slk+eng") == ["slk"]
        assert installed.unsupported("eng") == []

    def test_an_unprobed_installation_claims_no_language_is_missing(self):
        """Without a language list, refusing to run would be a guess."""
        assert OcrCapability(tesseract="/x").unsupported("slk+eng") == []


class TestWithoutTheTools:
    """The commonest situation: OCR is not installed, and must say so."""

    @pytest.fixture(autouse=True)
    def no_tools(self, monkeypatch):
        forget_capability()
        monkeypatch.setattr("app.services.ocr.shutil.which", lambda _name: None)
        yield
        forget_capability()

    def test_reading_an_image_reports_the_missing_tool(self):
        result = read_image(b"\x89PNG\r\n\x1a\n")
        assert result.ok is False
        assert "tesseract" in (result.error or "")

    def test_reading_a_pdf_reports_the_missing_tool(self):
        result = read_pdf(make_pdf(["x"]))
        assert result.ok is False
        assert result.error


@has_tesseract
@has_poppler
class TestRealRecognition:
    def test_a_page_with_no_text_layer_is_read(self):
        """The whole point: this image has no text in it, only pixels."""
        page = rasterise(make_pdf(["Zmluvna pokuta je 2000 EUR"]))
        result = read_image(page)
        assert result.ok
        assert "2000 EUR" in result.text

    def test_slovak_diacritics_survive_the_pipeline(self):
        page = rasterise(make_pdf([SLOVAK]))
        result = read_image(page, languages="slk+eng")
        assert "Záložné" in result.text
        assert "škody" in result.text

    def test_a_scanned_pdf_is_read_page_by_page(self):
        pdf = make_pdf(["Zmluvna pokuta je 2000 EUR", "Lehota uplynie 31.08.2026", "Okresny sud"])
        result = read_pdf(pdf, dpi=200, max_pages=5)
        assert result.pages == 3
        assert "2000 EUR" in result.text
        assert "31.08.2026" in result.text

    def test_each_page_is_labelled(self):
        result = read_pdf(make_pdf(["Prva strana", "Druha strana"]), dpi=150)
        assert "[page 1]" in result.text
        assert "[page 2]" in result.text

    def test_a_long_document_is_capped_and_says_so(self):
        """Silently reading three of forty pages would be worse than refusing."""
        pdf = make_pdf([f"Strana cislo {i}" for i in range(1, 9)])
        result = read_pdf(pdf, dpi=150, max_pages=3)
        assert result.pages == 3
        assert any("of 8 pages" in note for note in result.notes)

    def test_a_blank_page_contributes_nothing(self):
        result = read_pdf(make_pdf([" "]), dpi=150)
        assert len(result.text.strip()) < MIN_PAGE_CHARS

    def test_a_damaged_pdf_is_a_result_not_a_crash(self):
        result = read_pdf(b"%PDF-1.4 truncated nonsense")
        assert result.ok is False
        assert result.error

    def test_an_image_that_is_not_an_image_is_a_result_not_a_crash(self):
        result = read_image(b"certainly not a picture")
        assert result.ok is False
        assert result.error

    def test_a_timeout_is_reported_rather_than_hanging(self, monkeypatch):
        def timeout(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd="tesseract", timeout=1)

        monkeypatch.setattr("app.services.ocr.subprocess.run", timeout)
        result = read_image(b"\x89PNG\r\n\x1a\n", timeout=1)
        assert "gave up" in (result.error or "")
