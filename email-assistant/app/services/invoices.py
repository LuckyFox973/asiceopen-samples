"""Reading the facts off an invoice: what is owed, to whom, and by when.

A task called "pay Orange" is a reminder to go and look something up.  A task
called "pay Orange 2897510916, 47.90 EUR, due 15 September" is the answer.
The difference is entirely in whether the due date and the amount were read
out of the document, and they already have been — the text is extracted and
sitting in the database.

Patterns, not a model.  A date either appears next to the words "splatnosť" /
"due date" or it does not, and a wrong due date on a payable invoice is worse
than none at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# Slovak and Czech invoices label it several ways; English ones two.  Ordered
# by how specific the label is, because "dátum" alone also introduces the
# issue date, which must never be mistaken for the due date.
DUE_LABELS = (
    r"d[aá]tum\s+splatnosti",
    r"splatnos[ťt]\s*(?:do)?",
    r"splatn[eé]\s+do",
    r"due\s+date",
    r"payment\s+due",
    r"zaplat(?:i[ťt]|te)\s+do",
)

# 15.09.2026 · 15. 9. 2026 · 2026-09-15 · 15/09/2026
DATE = r"(\d{1,2}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{2,4}|\d{4}-\d{2}-\d{2})"

# Ordered: the total including VAT first, because a Slovak invoice prints
# several subtotals above it and the first figure on the page is routinely an
# instalment line.  Reading "Splátky spolu 142,94" as the amount owed, when
# "Spolu s DPH" two lines below says 304,68, is how a bill gets half paid.
AMOUNT_LABELS = (
    r"spolu\s+s\s+DPH",
    r"celkom\s+s\s+DPH",
    r"celkov[aá]\s+suma\s+s\s+DPH",
    r"celkom\s+k\s+[uú]hrade",
    r"suma\s+k\s+[uú]hrade",
    r"suma\s+na\s+[uú]hradu",
    r"celkom\s+na\s+[uú]hradu",
    r"k\s+[uú]hrade\s+spolu",
    r"celkov[aá]\s+suma",
    r"k\s+[uú]hrade",
    r"total\s+incl[a-z.]*\s*VAT",
    r"amount\s+payable",
    r"amount\s+due",
    r"total\s+due",
    r"total\s+amount",
)

# Who issued the invoice.  Taken from the document, because the sender of the
# e-mail is often not the supplier: a forwarded invoice arrives from one of
# my own companies, and naming that as the party to pay is nonsense.
SUPPLIER_LABELS = (
    r"dod[aá]vate[ľl]",
    r"predávaj[uú]ci",
    r"vystavil",
    r"supplier",
    r"issued\s+by",
    r"from",
)
# 1 234,56 · 1.234,56 · 1,234.56 · 47.90
MONEY = r"(-?\d[\d\s., ]*\d|\d)"
CURRENCY = r"(EUR|€|CZK|K[čc]|USD|\$)"
# The symbol goes before the number in English and after it in Slovak,
# and both arrive: "$120.00" from Anthropic, "47,90 EUR" from Orange.
PRICE = rf"(?:{CURRENCY}\s*)?{MONEY}(?:\s*{CURRENCY})?"

VARIABLE_SYMBOL = re.compile(r"variabiln[ýy]\s+symbol\s*:?\s*(\d[\d\s]{3,})", re.IGNORECASE)
INVOICE_NUMBER = re.compile(
    r"(?:fakt[uú]ra|invoice|da[ňn]ov[ýy]\s+doklad)[^\n]{0,20}?"
    r"(?:[cč]\.|no\.?|number|#)\s*:?\s*([A-Z0-9][A-Z0-9\-/]{3,})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class InvoiceFacts:
    """What could be read. Every field is optional because every field can be
    missing, and inventing one is the failure this exists to avoid."""

    supplier: str | None = None
    number: str | None = None
    variable_symbol: str | None = None
    due_date: date | None = None
    amount: str | None = None
    currency: str | None = None

    @property
    def anything(self) -> bool:
        return any((self.number, self.variable_symbol, self.due_date, self.amount))

    def summary(self) -> str:
        """A one-line description, naming only what was actually found."""
        parts: list[str] = []
        if self.supplier:
            parts.append(self.supplier)
        if self.number:
            parts.append(f"no. {self.number}")
        if self.amount:
            parts.append(f"{self.amount} {self.currency or ''}".strip())
        if self.due_date:
            parts.append(f"due {self.due_date.isoformat()}")
        if self.variable_symbol:
            parts.append(f"VS {self.variable_symbol}")
        return ", ".join(parts)


def parse_date(raw: str) -> date | None:
    """A Slovak, Czech or ISO date. Day first — never American order.

    An invoice from a Slovak supplier writes 09.05.2026 for the ninth of May.
    Reading it as September would move a payment four months.
    """
    cleaned = re.sub(r"\s+", "", raw)
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", cleaned)
    if iso:
        year, month, day = (int(g) for g in iso.groups())
    else:
        parts = re.split(r"[.\-/]", cleaned)
        if len(parts) != 3:
            return None
        try:
            day, month, year = (int(p) for p in parts)
        except ValueError:
            return None
        if year < 100:
            year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_amount(raw: str) -> str | None:
    """Normalise a written amount to digits and a full stop.

    Both conventions appear, sometimes in one document: 1 234,56 from a Slovak
    supplier and 1,234.56 from an American one.  The last separator decides.
    """
    cleaned = re.sub(r"[\s ]", "", raw).rstrip(".,")
    if not cleaned or not re.search(r"\d", cleaned):
        return None

    last_comma = cleaned.rfind(",")
    last_dot = cleaned.rfind(".")
    if last_comma > last_dot:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    else:
        cleaned = cleaned.replace(",", "")

    try:
        value = float(cleaned)
    except ValueError:
        return None
    return f"{value:.2f}".rstrip("0").rstrip(".") if "." in f"{value:.2f}" else str(value)


def find_due_date(text: str) -> date | None:
    """The date beside a due-date label, and no other date in the document."""
    for label in DUE_LABELS:
        # Up to 60 characters between label and date: on a rendered invoice
        # they sit in the same row but with a column of whitespace between.
        pattern = re.compile(rf"{label}\D{{0,60}}?{DATE}", re.IGNORECASE)
        match = pattern.search(text)
        if match:
            found = parse_date(match.group(1))
            if found:
                return found
    return None


def find_amount(text: str) -> tuple[str | None, str | None]:
    for label in AMOUNT_LABELS:
        match = re.search(rf"{label}\s*:?\s*{PRICE}", text, re.IGNORECASE)
        if match:
            before, figure, after = match.groups()
            amount = parse_amount(figure)
            if amount:
                return amount, _currency(before or after)

    # Nothing labelled as a total: report no amount.  The first figure that
    # names a currency is whatever the layout happened to print first — on a
    # real invoice that was an instalment subtotal — and a wrong amount on a
    # payment reminder is worse than no amount at all.
    return None, None


def _currency(raw: str | None) -> str | None:
    if not raw:
        return None
    symbol = raw.strip().upper()
    return {"€": "EUR", "$": "USD", "KČ": "CZK", "KC": "CZK"}.get(symbol, symbol)


def find_supplier(text: str) -> str | None:
    """The company that issued the invoice, as the document names it."""
    for label in SUPPLIER_LABELS:
        match = re.search(rf"{label}\s*:?\s*([^\n]{{3,80}})", text, re.IGNORECASE)
        if match:
            name = _tidy_company(match.group(1))
            if name:
                return name
    return None


# A legal-form suffix ends a company name; whatever follows on the line is the
# address, which does not belong in a task title.
COMPANY_END = re.compile(
    r"^(.*?\b(?:a\.?\s?s\.?|s\.?\s?r\.?\s?o\.?|spol\.?\s+s\s+r\.?\s?o\.?"
    r"|k\.?\s?s\.?|v\.?\s?o\.?\s?s\.?|PBC|Inc\.?|Ltd\.?|LLC|GmbH))",
    re.IGNORECASE,
)


def _tidy_company(raw: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", raw).strip(" :,-")
    if not cleaned:
        return None
    match = COMPANY_END.match(cleaned)
    if match:
        cleaned = match.group(1).strip()
    # A line of digits or a single letter is a column artefact, not a name.
    return cleaned[:80] if re.search(r"[A-Za-zÀ-ž]{3}", cleaned) else None


def read_invoice(text: str) -> InvoiceFacts:
    """Everything that can be read off this document, and nothing more."""
    if not text or not text.strip():
        return InvoiceFacts()

    amount, currency = find_amount(text)
    number = INVOICE_NUMBER.search(text)
    symbol = VARIABLE_SYMBOL.search(text)

    return InvoiceFacts(
        supplier=find_supplier(text),
        number=number.group(1).strip() if number else None,
        variable_symbol=re.sub(r"\s", "", symbol.group(1)) if symbol else None,
        due_date=find_due_date(text),
        amount=amount,
        currency=currency,
    )
