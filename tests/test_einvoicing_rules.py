import dataclasses
from decimal import Decimal

from einvoice_fixtures import valid_invoice

from invoice_extract.einvoicing.model import Line
from invoice_extract.einvoicing.rules import FORMATS, check


def by_field(issues):
    return {i.field: i.level for i in issues}


def test_valid_invoice_has_no_issues():
    for fmt in FORMATS:
        assert check(valid_invoice(), fmt) == []


def test_missing_buyer_email_blocks_only_xrechnung():  # Review Focus 2
    inv = valid_invoice()
    inv.buyer = dataclasses.replace(inv.buyer, email="")
    assert by_field(check(inv, "xrechnung"))["buyer.email"] == "error"
    assert by_field(check(inv, "zugferd"))["buyer.email"] == "warning"


def test_xrechnung_needs_reference_and_seller_contact():
    inv = valid_invoice(buyer_reference="")
    inv.seller = dataclasses.replace(inv.seller, contact_name="", phone="")
    fields = by_field(check(inv, "xrechnung"))
    assert fields["buyer_reference"] == fields["seller.contact_name"] == fields["seller.phone"] == "error"


def test_mandatory_fields():
    inv = valid_invoice(number="", issue_date=None, currency="Euro", lines=[])
    inv.buyer = dataclasses.replace(inv.buyer, name="", country="Germany")
    fields = by_field(check(inv, "zugferd"))
    for name in ("number", "issue_date", "currency", "buyer.name", "buyer.country", "lines"):
        assert fields[name] == "error", name


def test_totals_mismatch():
    fields = by_field(check(valid_invoice(printed_gross=Decimal("120.00")), "zugferd"))
    assert fields == {"gross": "error"}


def test_line_amount_mismatch_and_missing_values():
    inv = valid_invoice(lines=[Line(description="A", quantity=Decimal("2"), unit_price=Decimal("50"),
                                    vat_rate=Decimal("19"), amount=Decimal("90.00")),
                               Line(description="", quantity=None, unit_price=None, vat_rate=None)],
                        printed_net=None, printed_vat=None, printed_gross=None)
    fields = by_field(check(inv, "zugferd"))
    assert fields["lines.0.amount"] == "error"
    for name in ("lines.1.description", "lines.1.quantity", "lines.1.unit_price", "lines.1.vat_rate"):
        assert fields[name] == "error", name


def test_exempt_needs_reason_and_standard_needs_vat_id():
    inv = valid_invoice(lines=[Line(description="A", unit_price=Decimal("10"), vat_rate=Decimal("0"))],
                        printed_net=None, printed_vat=None, printed_gross=None)
    assert by_field(check(inv, "zugferd")) == {"exemption_reason": "error"}
    inv = valid_invoice()
    inv.seller = dataclasses.replace(inv.seller, vat_id="")
    assert by_field(check(inv, "zugferd")) == {"seller.vat_id": "error", "seller.legal_id": "error"}
    inv.seller = dataclasses.replace(inv.seller, tax_number="12/345/67890", legal_id="HRB 12345")
    assert check(inv, "zugferd") == []


def test_direct_debit_needs_mandate():  # Review Focus 3
    inv = valid_invoice(payment_means="debit", iban="")
    fields = by_field(check(inv, "xrechnung"))
    assert fields["mandate_reference"] == fields["creditor_id"] == fields["debited_iban"] == "error"
    assert "iban" not in fields
    ok = valid_invoice(payment_means="debit", iban="", mandate_reference="M-1", creditor_id="DE98ZZZ09999999999",
                       debited_iban="DE89370400440532013000")
    assert check(ok, "xrechnung") == []


def test_bad_iban():
    assert by_field(check(valid_invoice(iban="DE00123"), "zugferd")) == {"iban": "error"}


def test_due_date_or_terms():
    assert by_field(check(valid_invoice(due_date=None, payment_terms=""), "zugferd")) == {"due_date": "error"}


def test_delivery_date_or_period_is_recommended():  # KoSIT: BR-DE-TMP-32
    import datetime
    issues = check(valid_invoice(), "xrechnung")
    assert issues == []  # the fixture has no period; the recommendation only shows as an info-level hint
    inv = valid_invoice()
    from invoice_extract.einvoicing.rules import hints
    assert [i.rule for i in hints(inv)] == ["BR-DE-TMP-32"]
    assert hints(valid_invoice(delivery_date=datetime.date(2026, 10, 1))) == []
