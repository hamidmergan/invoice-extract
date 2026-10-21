import datetime
from decimal import Decimal

from invoice_extract.einvoicing.fields import EINVOICE_FIELDS, LINE_FIELDS, from_row
from invoice_extract.results import Cell, Row


def row_of(values, lines=None):
    cells = {k: Cell(value=v) for k, v in values.items()}
    if lines is not None:
        cells["lines"] = Cell(value=lines, kind="table", columns=list(LINE_FIELDS))
    return Row(path="a.pdf", kind="pdf", cells=cells)


def test_fields_have_roles_and_lines():
    keys = {c.key: c for c in EINVOICE_FIELDS}
    assert keys["gross"].role == "gross" and keys["supplier"].role == "supplier" and keys["iban"].type == "iban"
    assert keys["lines"].kind == "table" and keys["lines"].table_columns == LINE_FIELDS
    assert all(c.label and c.hint for c in EINVOICE_FIELDS)


def test_from_row_maps_parties_lines_and_payment():
    row = row_of({
        "invoice_no": "R-7", "doc_type": "invoice", "invoice_date": datetime.date(2026, 10, 21), "currency": "eur",
        "supplier": "ACME GmbH", "seller_city": "Berlin", "seller_country": "Deutschland", "seller_vat_id": "DE123",
        "buyer_name": "Beta AG", "buyer_country": "AT", "payment_means": "direct debit",
        "mandate_reference": "M-1", "creditor_id": "DE98ZZZ09999999999", "iban": "de89 3704 0044 0532 0130 00",
        "net": Decimal("100.00"), "vat": Decimal("19.00"), "gross": Decimal("119.00"), "notes": "Thank you.",
    }, lines=[{"Description": "Consulting", "Quantity": "2", "Unit": "Std", "Unit Price": "50,00",
               "VAT Rate": "19 %", "Amount": "100,00"}])
    inv = from_row(row)
    assert inv.number == "R-7" and inv.type_code == "380" and inv.currency == "EUR"
    assert inv.seller.name == "ACME GmbH" and inv.seller.country == "DE" and inv.buyer.country == "AT"
    line = inv.lines[0]
    assert (line.quantity, line.unit, line.unit_price, line.vat_rate, line.amount) == (
        Decimal("2"), "HUR", Decimal("50.00"), Decimal("19"), Decimal("100.00"))
    assert inv.payment_means == "debit" and inv.mandate_reference == "M-1"
    assert inv.iban == "DE89370400440532013000"
    assert inv.printed_gross == Decimal("119.00") and inv.notes == ["Thank you."]


def test_credit_note_and_fraction_rate():
    inv = from_row(row_of({"doc_type": "Gutschrift"}, lines=[{"Description": "A", "VAT Rate": "0.07", "Unit Price": "1"}]))
    assert inv.type_code == "381" and inv.lines[0].vat_rate == Decimal("7")


def test_from_row_without_lines_builds_one_line():
    inv = from_row(row_of({"invoice_no": "R-1", "net": Decimal("100.00"), "vat": Decimal("19.00")}))
    assert len(inv.lines) == 1
    line = inv.lines[0]
    assert line.description == "Invoice R-1" and line.unit_price == Decimal("100.00") and line.vat_rate == Decimal("19")


def test_from_row_odd_line_values():  # Review Focus 5
    inv = from_row(row_of({}, lines=[{"Description": "A", "Quantity": "", "Unit Price": "abc", "VAT Rate": "",
                                      "Amount": "abc"},
                                     {"Description": "", "Quantity": "", "Unit Price": "", "VAT Rate": "",
                                      "Amount": ""}]))
    assert len(inv.lines) == 1
    line = inv.lines[0]
    assert line.quantity == Decimal("1") and line.unit_price is None and line.vat_rate is None and line.amount is None


def test_error_row_gives_empty_invoice():
    assert from_row(Row(path="a.pdf", error="broken")).lines == []


def test_rate_keeps_plain_notation():
    inv = from_row(row_of({}, lines=[{"Description": "A", "VAT Rate": "10", "Unit Price": "1"}]))
    assert str(inv.lines[0].vat_rate) == "10.00"
