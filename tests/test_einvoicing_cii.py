import pytest
from einvoice_fixtures import valid_invoice

from invoice_extract.einvoice import parse_line_items, parse_xml
from invoice_extract.einvoicing.cii import GUIDELINES, build_cii
from invoice_extract.errors import ConfigError


@pytest.mark.parametrize("fmt", ["xrechnung", "zugferd"])
def test_build_cii_roundtrip(fmt):
    xml = build_cii(valid_invoice(), fmt)
    values = parse_xml(xml)
    assert values["invoice_no"] == "R-2026-001" and values["gross"] == "119.00"
    assert values["iban"] == "DE89370400440532013000" and values["supplier"] == "ACME GmbH"
    assert values["invoice_date"] == "2026-10-21" and values["due_date"] == "2026-11-20"
    assert GUIDELINES[fmt].encode() in xml
    assert parse_line_items(xml) == [{"Description": "Consulting", "Quantity": "2", "Unit Price": "50.00",
                                      "Amount": "100.00"}]


def test_build_cii_refuses_invalid():
    with pytest.raises(ConfigError, match="buyer.email"):
        build_cii(valid_invoice(buyer_reference="x", buyer=valid_invoice().buyer.__class__(name="B", country="DE")),
                  "xrechnung")


def test_build_cii_direct_debit():  # Review Focus 3
    inv = valid_invoice(payment_means="debit", iban="", mandate_reference="M-42", creditor_id="DE98ZZZ09999999999",
                        debited_iban="DE89370400440532013000")
    xml = build_cii(inv, "xrechnung").decode()
    assert "<ram:TypeCode>59</ram:TypeCode>" in xml
    assert "M-42" in xml and "DE98ZZZ09999999999" in xml


def test_build_cii_two_rates_and_exempt_reason():
    from decimal import Decimal
    from invoice_extract.einvoicing.model import Line
    inv = valid_invoice(lines=[Line(description="A", unit_price=Decimal("10.00"), vat_rate=Decimal("19")),
                               Line(description="B", unit_price=Decimal("5.00"), vat_rate=Decimal("0"))],
                        exemption_reason="Exempt under § 4 UStG", printed_net=None, printed_vat=None,
                        printed_gross=None)
    xml = build_cii(inv, "zugferd").decode()
    assert xml.count("<ram:ApplicableTradeTax>") >= 2 and "Exempt under" in xml
    assert "<ram:GrandTotalAmount>16.90</ram:GrandTotalAmount>" in xml


def test_xrechnung_has_business_process():  # KoSIT: PEPPOL-EN16931-R001
    xml = build_cii(valid_invoice(), "xrechnung").decode()
    assert "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0" in xml


def test_build_cii_notes_and_period():
    import datetime
    inv = valid_invoice(notes=["Thank you."], period_start=datetime.date(2026, 10, 1),
                        period_end=datetime.date(2026, 10, 31), delivery_date=datetime.date(2026, 10, 15))
    xml = build_cii(inv, "xrechnung").decode()
    assert "Thank you." in xml and "20261001" in xml and "20261015" in xml


def test_build_cii_rounding_amount():
    from decimal import Decimal
    from invoice_extract.einvoicing.model import Line
    inv = valid_invoice(lines=[Line(description="Plan", unit_price=Decimal("10.0756"), vat_rate=Decimal("19"),
                                    amount=Decimal("10.0756"))],
                        printed_net=Decimal("10.0756"), printed_vat=Decimal("1.9144"), printed_gross=Decimal("11.99"))
    xml = build_cii(inv, "xrechnung").decode()
    assert "<ram:RoundingAmount>-0.01</ram:RoundingAmount>" in xml
    assert "<ram:DuePayableAmount>11.99</ram:DuePayableAmount>" in xml
