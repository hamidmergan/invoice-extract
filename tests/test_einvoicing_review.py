"""Cases from the final review: inputs our rules let through but the official KoSIT validator rejects."""

import dataclasses
import datetime
from decimal import Decimal

import pytest
from einvoice_fixtures import valid_invoice

from invoice_extract.einvoicing.cii import build_cii
from invoice_extract.einvoicing.fields import LINE_FIELDS, from_row
from invoice_extract.einvoicing.model import Line, from_dict, to_dict
from invoice_extract.einvoicing.profile import profile_loads
from invoice_extract.einvoicing.rules import check
from invoice_extract.errors import ConfigError
from invoice_extract.results import Cell, Row


def fields(inv, fmt="zugferd"):
    return {i.field: i.level for i in check(inv, fmt) if i.level == "error"}


def seller(inv, **changes):
    inv.seller = dataclasses.replace(inv.seller, **changes)
    return inv


def test_tax_number_only_needs_legal_id():  # BR-CO-26
    inv = seller(valid_invoice(), vat_id="", tax_number="12/345/67890")
    assert fields(inv) == {"seller.legal_id": "error"}
    inv = seller(inv, legal_id="HRB 12345")
    assert fields(inv, "xrechnung") == {} and b"HRB 12345" in build_cii(inv, "xrechnung")


def test_small_business_needs_tax_number():  # BR-E-02, BR-DE-16
    inv = seller(valid_invoice(lines=[Line(description="A", unit_price=Decimal("100.00"), vat_rate=Decimal("0"))],
                               exemption_reason="Kleinunternehmer gemäß § 19 UStG", printed_net=None, printed_vat=None,
                               printed_gross=None), vat_id="", legal_id="HRB 1")
    assert fields(inv) == {"seller.tax_number": "error"}
    assert fields(seller(inv, tax_number="12/345/67890"), "xrechnung") == {}


def test_negative_price_moves_to_quantity():  # BR-27
    row = Row(path="a.pdf", cells={"lines": Cell(kind="table", columns=list(LINE_FIELDS), value=[
        {"Description": "Discount", "Quantity": "1", "Unit": "", "Unit Price": "-10,00", "VAT Rate": "19",
         "Amount": "-10,00"}])})
    line = from_row(row).lines[0]
    assert (line.quantity, line.unit_price, line.amount) == (Decimal("-1"), Decimal("10.00"), Decimal("-10.00"))
    data = to_dict(valid_invoice())
    data["lines"][0].update(quantity="2", unit_price="-50.00", amount="-100.00")
    line = from_dict(data).lines[0]
    assert (line.quantity, line.unit_price) == (Decimal("-2"), Decimal("50.00"))
    bad = valid_invoice(lines=[Line(description="A", unit_price=Decimal("-1"), vat_rate=Decimal("19"))],
                        printed_net=None, printed_vat=None, printed_gross=None)
    assert fields(bad) == {"lines.0.unit_price": "error"}


@pytest.mark.parametrize("change, field", [
    (dict(currency="ABC"), "currency"),
    (dict(period_start=datetime.date(2026, 10, 31), period_end=datetime.date(2026, 10, 1)), "period_end"),
    (dict(payment_terms="#SKONTO#TAGE=10#PROZENT=2#"), "payment_terms"),
    (dict(payment_terms="#2% Skonto bei Zahlung innerhalb 10 Tagen"), "payment_terms"),
])
def test_codes_and_formats(change, field):
    assert field in fields(valid_invoice(**change), "xrechnung")


def test_country_vat_id_and_unit_codes():
    assert "seller.country" in fields(seller(valid_invoice(), country="UK"))
    assert "seller.vat_id" in fields(seller(valid_invoice(), vat_id="123456789"))
    inv = valid_invoice()
    inv.lines[0].unit = "KWHX"
    assert "lines.0.unit" in fields(inv)
    data = to_dict(valid_invoice())
    data["lines"][0]["unit"] = "Std"
    assert from_dict(data).lines[0].unit == "HUR"


def test_skonto_terms_get_their_line_break():  # BR-DE-18
    inv = valid_invoice(payment_terms="#SKONTO#TAGE=10#PROZENT=2.00#")
    assert fields(inv, "xrechnung") == {}
    assert b"#SKONTO#TAGE=10#PROZENT=2.00#\n" in build_cii(inv, "xrechnung")


@pytest.mark.parametrize("cat, extra, field", [
    ("Z", {}, "lines.0.vat_rate"),             # BR-Z-05: zero rated means rate 0
    ("X", {}, "lines.0.vat_category"),         # BR-CL-18: unknown category
    ("K", {}, "lines.0.vat_category"),         # not supported yet
    ("AE", {"exemption_reason": ""}, "exemption_reason"),  # BR-AE-10
])
def test_vat_categories(cat, extra, field):
    rate = Decimal("19") if cat == "Z" else Decimal("0")
    options = {"exemption_reason": "Reverse charge", **extra}
    inv = valid_invoice(lines=[Line(description="A", unit_price=Decimal("10"), vat_rate=rate, vat_category=cat)],
                        printed_net=None, printed_vat=None, printed_gross=None, **options)
    inv.buyer = dataclasses.replace(inv.buyer, vat_id="DE987654321")
    assert field in fields(inv)


def test_reverse_charge_is_written_with_reason():
    inv = valid_invoice(lines=[Line(description="A", unit_price=Decimal("10"), vat_rate=Decimal("0"), vat_category="AE")],
                        printed_net=None, printed_vat=None, printed_gross=None, exemption_reason="Reverse charge")
    inv.buyer = dataclasses.replace(inv.buyer, vat_id="FR12345678901")
    assert fields(inv, "xrechnung") == {}
    assert b"Reverse charge" in build_cii(inv, "xrechnung")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1" * 40])
def test_hostile_numbers_are_rejected(value):  # Focus 5
    data = to_dict(valid_invoice())
    data["lines"][0]["unit_price"] = value
    with pytest.raises(ConfigError, match="lines.0.unit_price"):
        from_dict(data)
    row = Row(path="a.pdf", cells={"gross": Cell(value=Decimal(value) if value != "1" * 40 else value),
                                   "lines": Cell(kind="table", columns=list(LINE_FIELDS),
                                                 value=[{"Description": "A", "Unit Price": value, "VAT Rate": "NaN"}])})
    inv = from_row(row)
    assert inv.printed_gross is None and inv.lines[0].unit_price is None and inv.lines[0].vat_rate is None


def test_exponent_is_written_plainly():
    data = to_dict(valid_invoice(printed_net=None, printed_vat=None, printed_gross=None))
    data["lines"][0].update(quantity="1E+1", amount="")
    inv = from_dict(data)
    assert str(inv.lines[0].quantity) == "10"
    assert b"BilledQuantity" in build_cii(inv, "zugferd")


def test_fallback_line_does_not_invent_a_rate():
    def row(net, vat):
        return Row(path="a.pdf", cells={"invoice_no": Cell(value="R1"), "net": Cell(value=Decimal(net)),
                                        "vat": Cell(value=Decimal(vat))})
    assert from_row(row("200.00", "26.00")).lines[0].vat_rate is None
    assert from_row(row("100.00", "19.00")).lines[0].vat_rate == Decimal("19")


def test_iban_spaces_are_removed():
    assert profile_loads("iban: DE89 3704 0044 0532 0130 00\n").iban == "DE89370400440532013000"
    data = to_dict(valid_invoice())
    data["iban"] = "de89 3704 0044 0532 0130 00"
    assert from_dict(data).iban == "DE89370400440532013000"
