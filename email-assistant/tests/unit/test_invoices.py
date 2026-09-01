"""Reading the amount and the due date off an invoice."""

from __future__ import annotations

from datetime import date

import pytest

from app.services.invoices import parse_amount, parse_date, read_invoice

ORANGE = """
Faktúra č. 2897510916
Variabilný symbol: 0397580739     Telefónne číslo: 0239 067 216
Dátum vystavenia: 23.07.2026
Dátum splatnosti: 15.09.2026
Celkom k úhrade                          47,90 EUR
IBAN: SK29 1100 0000 0026 2800 5850
"""

ANTHROPIC = """
Receipt from Anthropic PBC
Invoice number 2898-2388-5736
Date paid August 31, 2026
Amount due $120.00
Due date 2026-09-15
"""


class TestDates:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("15.09.2026", date(2026, 9, 15)),
            ("15. 9. 2026", date(2026, 9, 15)),
            ("2026-09-15", date(2026, 9, 15)),
            ("15/09/2026", date(2026, 9, 15)),
            ("15.9.26", date(2026, 9, 15)),
        ],
    )
    def test_the_forms_that_actually_arrive(self, raw, expected):
        assert parse_date(raw) == expected

    def test_the_day_comes_first(self):
        """A Slovak supplier writing 09.05.2026 means the ninth of May;
        reading it the American way moves a payment four months."""
        assert parse_date("09.05.2026") == date(2026, 5, 9)

    @pytest.mark.parametrize("raw", ["32.01.2026", "15.13.2026", "not a date", ""])
    def test_an_impossible_date_is_refused(self, raw):
        assert parse_date(raw) is None


class TestAmounts:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("47,90", "47.9"),
            ("1 234,56", "1234.56"),
            ("1.234,56", "1234.56"),
            ("1,234.56", "1234.56"),
            ("120.00", "120"),
        ],
    )
    def test_both_conventions_are_read(self, raw, expected):
        """A Slovak supplier and an American one write these differently,
        sometimes in the same document."""
        assert parse_amount(raw) == expected

    def test_nonsense_is_refused(self):
        assert parse_amount("abc") is None


class TestRealInvoices:
    def test_a_slovak_invoice(self):
        facts = read_invoice(ORANGE)
        assert facts.due_date == date(2026, 9, 15)
        assert facts.amount == "47.9"
        assert facts.currency == "EUR"
        assert facts.variable_symbol == "0397580739"
        assert facts.number == "2897510916"

    def test_the_issue_date_is_not_taken_for_the_due_date(self):
        """Both are labelled "Dátum"; only one is when the money is owed."""
        assert read_invoice(ORANGE).due_date != date(2026, 7, 23)

    def test_an_english_invoice(self):
        facts = read_invoice(ANTHROPIC)
        assert facts.due_date == date(2026, 9, 15)
        assert facts.amount == "120"
        assert facts.currency == "USD"

    def test_the_summary_names_only_what_was_found(self):
        summary = read_invoice(ORANGE).summary()
        assert "47.9 EUR" in summary
        assert "due 2026-09-15" in summary
        assert "VS 0397580739" in summary

    def test_a_document_that_is_not_an_invoice_yields_nothing(self):
        facts = read_invoice("Dobry den, v prilohe posielam podklady. S pozdravom.")
        assert facts.due_date is None
        assert not facts.anything

    def test_empty_text_yields_nothing(self):
        assert not read_invoice("").anything


REAL_ORANGE = """
Mesačné splátky Suma
Splátky spolu 142,94 €
Spolu s DPH 304,68 €
Typ služby:     Hlasový paušál     Internet       Televízia
Dodávateľ: Orange Slovensko, a. s., Metodova 8, 821 08 Bratislava, IČO 35697270
Faktúra č. 2864622723
Variabilný symbol: 0397580739
Dátum splatnosti: 07.01.2026
"""


class TestTheAmountIsTheOneOwed:
    """From a real invoice, where the first figure on the page is not it."""

    def test_the_total_with_vat_wins_over_an_instalment_subtotal(self):
        """142,94 is "Splátky spolu"; 304,68 is what is owed. Taking the
        first currency figure had a bill half paid."""
        assert read_invoice(REAL_ORANGE).amount == "304.68"

    def test_an_unlabelled_figure_is_not_taken_for_a_total(self):
        """No label, no amount — a wrong number on a payment reminder is
        worse than none."""
        facts = read_invoice("Nejaka suma 99,90 € niekde v texte")
        assert facts.amount is None

    def test_a_labelled_total_is_still_found_when_it_stands_alone(self):
        assert read_invoice("Celkom k úhrade 47,90 EUR").amount == "47.9"


class TestTheSupplierComesFromTheDocument:
    def test_the_issuer_is_read_from_the_document(self):
        assert read_invoice(REAL_ORANGE).supplier == "Orange Slovensko, a. s."

    def test_the_address_is_not_part_of_the_name(self):
        """The label's line runs on into the street; a task title should not."""
        assert "Metodova" not in (read_invoice(REAL_ORANGE).supplier or "")

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("Dodávateľ: Alza.sk s.r.o., Karadžičova 8", "Alza.sk s.r.o."),
            ("Predávajúci: Anthropic PBC, San Francisco", "Anthropic PBC"),
            ("Supplier: Example Ltd., London", "Example Ltd."),
        ],
    )
    def test_the_forms_that_arrive(self, text, expected):
        assert read_invoice(text).supplier == expected

    def test_a_document_naming_no_supplier_reports_none(self):
        assert read_invoice("Dobry den, v prilohe podklady.").supplier is None

    def test_a_column_artefact_is_not_a_company(self):
        assert read_invoice("Dodávateľ: 12345678").supplier is None
