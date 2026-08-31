"""Deciding which of my own companies a document was billed to."""

from __future__ import annotations

import pytest

from app.services.filing import IDENTIFIER, fold, matches_in, term_weight


class Folder:
    """A stand-in carrying only what matching reads."""

    def __init__(self, name: str, terms: list[str]):
        self.name = name
        self.match_terms = terms
        self.is_active = True


class TestFolding:
    def test_accents_do_not_decide_a_match(self):
        """An OCR'd invoice and a typed registry entry differ in exactly this."""
        assert fold("Ľudovíta Fullu") == fold("LUDOVITA FULLU")

    def test_case_does_not_decide_a_match(self):
        assert fold("Infinity Finance s.r.o.") == fold("INFINITY FINANCE S.R.O.")

    def test_runs_of_whitespace_collapse(self):
        """PDF text extraction pads columns with spaces."""
        assert fold("INFINITY   FINANCE\n s.r.o.") == "infinity finance s.r.o."


class TestTermWeight:
    @pytest.mark.parametrize("number", ["12345678", "36 512 345", "2020123456"])
    def test_a_registration_number_is_conclusive(self, number):
        assert term_weight(number) == 1.0
        assert IDENTIFIER.match(fold(number))

    def test_a_long_company_name_is_nearly_conclusive(self):
        assert term_weight("Infinity Finance s.r.o.") >= 0.9

    def test_a_short_word_decides_nothing_alone(self):
        """ "Fox" is in four of these companies."""
        assert term_weight("Fox") <= 0.3


class TestMatching:
    INVOICE = (
        "Faktura c. 2898-2388-5736\\n"
        "Odberatel: INFINITY FINANCE s.r.o., ICO: 51234567\\n"
        "Dodavatel: Anthropic PBC\\n"
        "Suma: 120,00 EUR"
    )

    def test_the_billed_company_is_found_not_the_sender(self):
        infi = Folder("03_INFI", ["Infinity Finance", "51234567"])
        matched, confidence = matches_in(self.INVOICE, infi)
        assert set(matched) == {"Infinity Finance", "51234567"}
        assert confidence >= 0.9

    def test_a_company_not_named_does_not_match(self):
        other = Folder("02_Cleaning Fox", ["Cleaning Fox", "99999999"])
        assert matches_in(self.INVOICE, other) == ((), 0.0)

    def test_one_registration_number_outweighs_several_weak_names(self):
        """Three vague aliases must not beat one identifier."""
        strong = Folder("A", ["51234567"])
        weak = Folder("B", ["Fox", "sro", "EUR"])
        _, strong_score = matches_in(self.INVOICE, strong)
        _, weak_score = matches_in("Fox sro EUR", weak)
        assert strong_score > weak_score

    def test_an_accented_name_in_the_document_still_matches(self):
        folder = Folder("X", ["Ludovita Fullu 7"])
        matched, _ = matches_in("Sidlo: Ľudovíta Fullu 7, Bratislava", folder)
        assert matched == ("Ludovita Fullu 7",)

    def test_an_empty_term_is_ignored(self):
        assert matches_in(self.INVOICE, Folder("X", ["", "  "])) == ((), 0.0)
