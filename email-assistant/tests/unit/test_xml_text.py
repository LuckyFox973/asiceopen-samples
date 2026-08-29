"""Structured XML — ISDOC invoices, e-filing forms — and hostile XML.

The security assertions here are not decoration. They pin behaviour this code
relies on but does not implement: libexpat refuses entity-expansion bombs and
ElementTree never resolves external entities. A runtime that lost either
should fail this suite rather than the owner's mailbox.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ElementTree

import pytest

from app.services.extraction import ExtractionStatus, detect_kind, extract
from app.services.xml_text import MAX_ELEMENTS, leaves, read_xml
from tests.fixtures.documents import make_isdoc


class TestIsdocInvoice:
    def test_the_invoice_number_is_indexed(self):
        assert "2026045" in read_xml(make_isdoc())

    def test_the_amount_and_the_iban_are_indexed(self):
        text = read_xml(make_isdoc(total="1250.50"))
        assert "1250.50" in text
        assert "SK3112000000198742637541" in text

    def test_values_carry_the_element_path_that_names_them(self):
        """Nothing is invented: the labels come from the document itself."""
        text = read_xml(make_isdoc())
        assert "Invoice/LegalMonetaryTotal/PayableAmount: 1250.50" in text
        assert "Invoice/IssueDate: 2026-03-01" in text

    def test_the_namespace_identifies_the_document(self):
        assert "ISDOC" in read_xml(make_isdoc())

    def test_attributes_are_indexed_too(self):
        assert "@version: 6.0.2" in read_xml(make_isdoc())

    def test_document_order_is_preserved(self):
        text = read_xml(make_isdoc())
        assert text.index("Invoice/ID") < text.index("Invoice/IssueDate")

    def test_an_isdoc_attachment_is_routed_here(self):
        result = extract(make_isdoc(), mime_type="application/xop+xml", filename="Faktura.isdoc")
        assert result.status is ExtractionStatus.EXTRACTED
        assert result.method == "xml-structured"

    def test_the_suffix_alone_is_enough(self):
        assert detect_kind(None, "Faktura_2026045.isdoc", make_isdoc()) == "xmldoc"


class TestHostileXml:
    def test_an_external_entity_is_not_resolved(self):
        """XXE would let an attachment read a file off this machine."""
        xxe = (
            b'<?xml version="1.0"?>'
            b'<!DOCTYPE r [ <!ENTITY x SYSTEM "file:///etc/hostname"> ]>'
            b"<r>&x;</r>"
        )
        with pytest.raises(ElementTree.ParseError):
            ElementTree.fromstring(xxe)
        assert read_xml(xxe) == ""

    def test_an_entity_expansion_bomb_is_refused(self):
        """Six levels of tenfold expansion is a billion characters."""
        entities = ['<!ENTITY a "aaaaaaaaaa">']
        for level in range(1, 7):
            previous = "a" if level == 1 else f"a{level - 1}"
            entities.append(f'<!ENTITY a{level} "{("&" + previous + ";") * 10}">')
        bomb = ('<?xml version="1.0"?><!DOCTYPE r [' + "".join(entities) + "]><r>&a6;</r>").encode()

        started = time.monotonic()
        assert read_xml(bomb) == ""
        assert time.monotonic() - started < 5

    def test_deep_nesting_does_not_blow_the_stack(self):
        deep = b"<a>" * 20_000 + b"payload" + b"</a>" * 20_000
        assert read_xml(deep) is not None

    def test_a_huge_document_is_bounded(self):
        wide = b"<r>" + b"<e>x</e>" * (MAX_ELEMENTS + 5_000) + b"</r>"
        root = ElementTree.fromstring(wide)
        assert len(leaves(root)) <= MAX_ELEMENTS

    def test_malformed_xml_falls_back_to_plain_text(self):
        """Better a raw string in the index than nothing at all."""
        result = extract(b"<not xml at all", filename="broken.isdoc")
        assert result.status in {ExtractionStatus.EXTRACTED, ExtractionStatus.EMPTY}


class TestDocxParsingIsSafeToday:
    """The same guard protects .docx, which the system already reads."""

    def test_a_bomb_inside_a_word_document_is_refused(self):
        import io
        import zipfile

        entities = ['<!ENTITY a "aaaaaaaaaa">']
        for level in range(1, 7):
            previous = "a" if level == 1 else f"a{level - 1}"
            entities.append(f'<!ENTITY a{level} "{("&" + previous + ";") * 10}">')
        document = (
            '<?xml version="1.0"?><!DOCTYPE w:document [' + "".join(entities) + "]>"
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>&a6;</w:t></w:r></w:p></w:body></w:document>"
        ).encode()

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", document)

        started = time.monotonic()
        result = extract(buffer.getvalue(), filename="Zmluva.docx")
        assert time.monotonic() - started < 5
        assert result.status is ExtractionStatus.FAILED
        assert result.char_count == 0
