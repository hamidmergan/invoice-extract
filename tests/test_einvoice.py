import time

import pytest

from invoice_extract.einvoice import parse_xml, xml_text
from invoice_extract.errors import UnsupportedFileError

from pdfgen import SAMPLE, cii_xml, ubl_xml

BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
 <!ENTITY lol5 "&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;&lol4;">
 <!ENTITY lol6 "&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;&lol5;">
 <!ENTITY lol7 "&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;&lol6;">
 <!ENTITY lol8 "&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;&lol7;">
 <!ENTITY lol9 "&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;&lol8;">
]>
<Invoice>&lol9;</Invoice>
"""


def test_parse_cii():
    result = parse_xml(cii_xml())
    assert result == SAMPLE
    assert result["invoice_date"] == "2026-10-21"


def test_parse_ubl():
    assert parse_xml(ubl_xml()) == SAMPLE
    assert parse_xml(ubl_xml(party_name_only=True))["supplier"] == "ACME GmbH"


def test_parse_ubl_credit_note():
    data = ubl_xml().replace(b"<Invoice ", b"<CreditNote ").replace(b"</Invoice>", b"</CreditNote>")
    data = data.replace(b"xsd:Invoice-2", b"xsd:CreditNote-2")
    assert parse_xml(data)["gross"] == "119.00"
    assert parse_xml(data)["doc_type"] == "credit note"
    assert parse_xml(cii_xml().replace(b"<ram:TypeCode>380<", b"<ram:TypeCode>381<"))["doc_type"] == "credit note"


def test_parse_description_joins_lines_and_cuts():
    line = cii_xml().split(b"<ram:IncludedSupplyChainTradeLineItem>")[1].split(b"</ram:IncludedSupplyChainTradeLineItem>")[0]
    def with_names(*names):
        items = b"".join(b"<ram:IncludedSupplyChainTradeLineItem>" + line.replace(b"Paper", n.encode())
                         + b"</ram:IncludedSupplyChainTradeLineItem>" for n in names)
        return cii_xml().replace(b"<ram:IncludedSupplyChainTradeLineItem>" + line
                                 + b"</ram:IncludedSupplyChainTradeLineItem>", items)
    assert parse_xml(with_names("A", "B", "C"))["description"] == "A; B; C"
    long = parse_xml(with_names("x" * 100))["description"]
    assert len(long) == 80 and long.endswith("…")


def test_parse_vat_rates_mixed():
    tax = cii_xml().split(b"<ram:ApplicableTradeTax>")[1].split(b"</ram:ApplicableTradeTax>")[0]
    second = b"<ram:ApplicableTradeTax>" + tax.replace(b"19.00", b"7.00") + b"</ram:ApplicableTradeTax>"
    data = cii_xml().replace(b"</ram:ApplicableTradeTax>", b"</ram:ApplicableTradeTax>" + second)
    assert parse_xml(data)["vat_rate"] == "19.00, 7.00"
    ubl = ubl_xml().replace(b"</cac:TaxSubtotal>", b"</cac:TaxSubtotal><cac:TaxSubtotal><cac:TaxCategory>"
                            b"<cbc:Percent>7.00</cbc:Percent></cac:TaxCategory></cac:TaxSubtotal>")
    assert parse_xml(ubl)["vat_rate"] == "19.00, 7.00"


def test_parse_minimal_xml_has_no_new_keys():
    import re
    data = cii_xml()
    data = re.sub(rb"<ram:TypeCode>380</ram:TypeCode>|<ram:SpecifiedTaxRegistration>.*?</ram:SpecifiedTaxRegistration>"
                  rb"|<ram:ApplicableTradeTax>.*?</ram:ApplicableTradeTax>"
                  rb"|<ram:IncludedSupplyChainTradeLineItem>.*?</ram:IncludedSupplyChainTradeLineItem>", b"", data,
                  flags=re.S)
    result = parse_xml(data)
    assert not {"doc_type", "supplier_vat_id", "vat_rate", "description"} & set(result)
    assert result["gross"] == "119.00"


def test_parse_skips_missing_fields():
    data = ubl_xml().replace(b"<cbc:DueDate>2026-11-20</cbc:DueDate>", b"")
    assert "due_date" not in parse_xml(data)


def test_parse_xml_bomb_rejected():
    start = time.monotonic()
    with pytest.raises(UnsupportedFileError):
        parse_xml(BOMB)
    assert time.monotonic() - start < 1


def test_parse_unknown_root():
    with pytest.raises(UnsupportedFileError):
        parse_xml(b"<Order><ID>1</ID></Order>")


def test_parse_broken_xml():
    with pytest.raises(UnsupportedFileError):
        parse_xml(b"<Invoice><unclosed>")


def test_xml_text_is_readable():
    text = xml_text(cii_xml())
    assert "GrandTotalAmount" in text
    assert "119.00" in text
    assert "urn:un:unece" not in text
    assert "\n  " in text


def test_parse_line_items_cii_and_ubl():
    from invoice_extract.einvoice import parse_line_items
    expected = [{"Description": "Paper", "Quantity": "10", "Unit Price": "10.00", "Amount": "100.00"}]
    assert parse_line_items(cii_xml()) == expected
    assert parse_line_items(ubl_xml()) == expected
