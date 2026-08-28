"""Tracked changes, comments, and filename version families."""

from __future__ import annotations

import pytest

from app.services.docx_revisions import read_docx
from app.services.extraction import ExtractionStatus, extract
from app.services.versions import normalise_filename
from tests.fixtures.documents import make_docx, make_docx_with_revisions


class TestTrackedChanges:
    def test_inserted_text_is_kept(self):
        """The regression this module exists for.

        python-docx drops inserted text as well as deleted, so a contract
        whose penalty was changed under review would be stored with no figure
        at all and no search would ever find it.
        """
        result = extract(make_docx_with_revisions(), filename="zmluva.docx")
        assert result.text == "Zmluvna pokuta je 2000 EUR."
        assert "2000 EUR" in result.text

    def test_deleted_text_is_not_in_the_indexed_body(self):
        result = extract(make_docx_with_revisions(), filename="zmluva.docx")
        assert "5000 EUR" not in result.text

    def test_deleted_text_is_kept_separately(self):
        result = extract(make_docx_with_revisions(), filename="zmluva.docx")
        assert result.deleted_text == "5000 EUR"

    def test_revisions_are_counted_with_their_authors(self):
        result = extract(make_docx_with_revisions(), filename="zmluva.docx")
        assert result.revision_count == 2
        assert result.revision_authors == ["Advokat", "Protistrana"]
        assert result.has_revisions

    def test_summary_reads_like_a_sentence(self):
        result = extract(make_docx_with_revisions(), filename="zmluva.docx")
        assert "1 insertion(s)" in result.revision_summary
        assert "1 deletion(s)" in result.revision_summary
        assert "Protistrana" in result.revision_summary

    def test_a_clean_document_reports_no_revisions(self):
        result = extract(make_docx(["Ciste znenie", "Bez zmien"]), filename="ciste.docx")
        assert result.revision_count == 0
        assert result.has_revisions is False
        assert result.revision_summary is None
        assert result.deleted_text == ""

    def test_insertion_only(self):
        result = extract(
            make_docx_with_revisions(deleted="", inserted="doplnena veta"),
            filename="z.docx",
        )
        assert "doplnena veta" in result.text
        assert result.revision_count == 1
        assert result.deleted_text == ""

    def test_deletion_only(self):
        result = extract(
            make_docx_with_revisions(deleted="vypustena veta", inserted=""),
            filename="z.docx",
        )
        assert "vypustena veta" not in result.text
        assert result.deleted_text == "vypustena veta"


class TestComments:
    def test_comment_text_is_captured_with_its_author(self):
        result = extract(
            make_docx_with_revisions(comment=("Advokat", "Trvame na 5000.")),
            filename="zmluva.docx",
        )
        assert "[Advokat] Trvame na 5000." in result.comment_text

    def test_a_document_with_only_comments_is_not_empty(self):
        result = extract(
            make_docx_with_revisions(
                before="",
                deleted="",
                inserted="",
                after="",
                comment=("Advokat", "Pripomienka k celemu zneniu."),
            ),
            filename="pripomienky.docx",
        )
        assert result.status is ExtractionStatus.EXTRACTED
        assert "Pripomienka" in result.comment_text

    def test_no_comments_leaves_the_field_empty(self):
        assert extract(make_docx(["Text"]), filename="x.docx").comment_text == ""


class TestStructure:
    def test_table_rows_stay_on_one_line(self):
        result = extract(
            make_docx(["Prehlad:"], table=[["Cislo", "Suma"], ["FA-001", "1250 EUR"]]),
            filename="t.docx",
        )
        assert "Cislo | Suma" in result.text
        assert "FA-001 | 1250 EUR" in result.text

    def test_paragraphs_stay_on_separate_lines(self):
        result = extract(make_docx(["Prvy", "Druhy"]), filename="p.docx")
        assert result.text == "Prvy\nDruhy"

    def test_method_records_how_it_was_read(self):
        assert extract(make_docx(["x"]), filename="x.docx").method == "docx-xml"

    def test_a_corrupt_docx_fails_cleanly(self):
        result = extract(b"PK\x03\x04 not really a docx", filename="broken.docx")
        assert result.status in {ExtractionStatus.FAILED, ExtractionStatus.UNSUPPORTED}


class TestReadDocxDirectly:
    def test_returns_structured_content(self):
        content = read_docx(make_docx_with_revisions(comment=("A", "poznamka")))
        assert content.insertion_count == 1
        assert content.deletion_count == 1
        assert len(content.comments) == 1
        assert content.comments[0].author == "A"

    def test_revision_dates_are_preserved(self):
        content = read_docx(make_docx_with_revisions())
        assert any(r.date and r.date.startswith("2026-08") for r in content.revisions)

    def test_non_docx_bytes_raise(self):
        with pytest.raises(ValueError, match="Not a readable"):
            read_docx(b"definitely not a zip")


class TestFilenameFamilies:
    @pytest.mark.parametrize(
        "filename",
        [
            "Zmluva.docx",
            "Zmluva_v2.docx",
            "Zmluva (final).docx",
            "Zmluva-2026-08-01.docx",
            "Zmluva_v2_final.docx",
            "zmluva copy.docx",
            "Zmluva - draft.docx",
        ],
    )
    def test_version_decoration_is_stripped(self, filename):
        assert normalise_filename(filename) == "zmluva"

    def test_different_documents_stay_distinct(self):
        assert normalise_filename("Zmluva o dielo.docx") != normalise_filename("Zmluva.docx")
        assert normalise_filename("Rozhodnutie.pdf") == "rozhodnutie"

    def test_empty_and_extensionless(self):
        assert normalise_filename(None) == ""
        assert normalise_filename("") == ""
        assert normalise_filename("README") == "readme"
