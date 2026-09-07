"""Reading the issuer off invoices that never say who the issuer is.

Written against the text of a real Orange statement, not an invented one.  On
that document the only place the issuer appears is the footer every Slovak
company must print under §3a of the Commercial Code — and the customer, one of
the mailbox owner's own companies, is the party that *is* labelled.
"""

from __future__ import annotations

from app.services.invoices import find_supplier, read_invoice

# Verbatim structure of Orange_doklad_CN0397580739_20251223, trimmed.
ORANGE = """Strana 1 / 9
Stav účtu
Názov a sídlo účastníka:
Viribial Trade s. r. o
Ľudovíta Fullu 7
841 05 Bratislava 4
IČO: 55001882
Korešpondenčná adresa:
Viribial Trade s. r. o
Ľudovíta Fullu 7
841 05 Bratislava 4
Variabilný symbol: 0397580739
Dátum splatnosti: 07. 01. 2026
Suma na úhradu: 304,68 €
Nedoplatok z predchádzajúcich
období k 23. 12. 2025: 285,10 €
Celková suma na úhradu: 589,78 €
Prehľad faktúr a splátok
Číslo faktúry Telefónne číslo Meno používateľa Suma s DPH
2864622723 0239 067 216   Viribial Trade s. r. o 22,55 €
Mesačné splátky Suma
Splátky spolu 142,94 €
Spolu s DPH 304,68 €
Typ služby:     Hlasový paušál     Internet       Televízia
Orange Slovensko, a. s., Metodova 8, 821 08 Bratislava, Slovenská republika, \
telefónne číslo: 0905 905 905, IČO: 356 97 270, IČ DPH: SK 20 20 31 05 78, DIČ: 20 20 31 05 78,
zapísaná v Obchodnom registri Mestského súdu Bratislava III, oddiel: Sa, vložka číslo 1142/B.
"""


class TestOrangeStatement:
    def test_the_issuer_is_read_from_the_statutory_footer(self):
        assert find_supplier(ORANGE) == "Orange Slovensko, a. s."

    def test_the_customer_is_not_mistaken_for_the_issuer(self):
        """Viribial is one of my own companies; naming it would invert the bill."""
        assert "Viribial" not in (find_supplier(ORANGE) or "")

    def test_the_other_facts_still_read(self):
        facts = read_invoice(ORANGE)
        # Money stays a string all the way through; a float would round it.
        assert facts.amount == "304.68", "the invoice's own total, not the arrears"
        assert facts.currency == "EUR"
        assert facts.variable_symbol == "0397580739"
        assert facts.due_date is not None and facts.due_date.isoformat() == "2026-01-07"


class TestFooterRule:
    def test_a_labelled_supplier_still_wins(self):
        """An explicit label is better evidence than a footer at the bottom."""
        text = (
            "Dodávateľ: Slovanet, a. s.\n"
            "Odberateľ: Lucky Fox s. r. o.\n"
            "Orange Slovensko, a. s., Metodova 8, IČO: 356 97 270,\n"
            "zapísaná v Obchodnom registri Mestského súdu Bratislava III.\n"
        )
        assert find_supplier(text) == "Slovanet, a. s."

    def test_a_customer_footer_is_not_the_supplier(self):
        """Some invoices print the customer's register entry too."""
        text = (
            "Odberateľ: Viribial Trade s. r. o.\n"
            "Viribial Trade s. r. o., Ľudovíta Fullu 7, IČO: 55001882,\n"
            "zapísaná v Obchodnom registri Mestského súdu Bratislava III.\n"
        )
        assert find_supplier(text) is None

    def test_a_name_without_a_register_clause_is_not_taken(self):
        """A company named in passing is not thereby the issuer."""
        text = "Prehľad faktúr\nViribial Trade s. r. o., Ľudovíta Fullu 7\nIČO: 55001882\n"
        assert find_supplier(text) is None

    def test_the_masculine_spelling_is_read_too(self):
        text = (
            "Alza.cz a. s., Jankovcova 1522/53, IČO: 27082440,\n"
            "zapísaný v obchodnom registri Mestského súdu v Prahe.\n"
        )
        assert find_supplier(text) == "Alza.cz a. s."

    def test_nothing_is_invented_from_an_empty_document(self):
        assert find_supplier("") is None
        assert find_supplier("Ďakujeme za Vašu platbu.") is None
