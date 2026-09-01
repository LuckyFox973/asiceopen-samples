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
