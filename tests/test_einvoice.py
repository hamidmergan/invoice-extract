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
