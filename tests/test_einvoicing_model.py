import datetime
from decimal import Decimal

import pytest

from invoice_extract.einvoicing.model import EInvoice, Line, Party, category, from_dict, tax_groups, to_dict, totals
from invoice_extract.errors import ConfigError


def test_totals_with_two_rates():  # Review Focus 1
    inv = EInvoice(lines=[Line(description="A", quantity=Decimal("3"), unit_price=Decimal("3.333"), vat_rate=Decimal("19")),
                          Line(description="B", quantity=Decimal("1"), unit_price=Decimal("10.00"), vat_rate=Decimal("7"))])
    groups = tax_groups(inv)
    assert [(g.category, g.rate, g.basis, g.tax) for g in groups] == [
        ("S", Decimal("7"), Decimal("10.00"), Decimal("0.70")), ("S", Decimal("19"), Decimal("10.00"), Decimal("1.90"))]
    t = totals(inv)
    assert (t.line_total, t.tax_basis, t.tax_total, t.grand_total, t.due) == (
        Decimal("20.00"), Decimal("20.00"), Decimal("2.60"), Decimal("22.60"), Decimal("22.60"))


def test_printed_line_amount_wins():
    line = Line(description="A", quantity=Decimal("3"), unit_price=Decimal("3.333"), amount=Decimal("9.99"))
    assert totals(EInvoice(lines=[line])).line_total == Decimal("9.99")


def test_zero_rate_is_exempt():
    assert category(Line(vat_rate=Decimal("0"))) == "E"
    assert category(Line(vat_rate=Decimal("19"))) == "S"
    assert category(Line(vat_rate=Decimal("0"), vat_category="AE")) == "AE"


def test_dict_roundtrip():
    inv = EInvoice(number="R-1", issue_date=datetime.date(2026, 10, 21), seller=Party(name="ACME", country="DE"),
                   notes=["Thanks"], lines=[Line(description="A", unit_price=Decimal("1.50"), vat_rate=Decimal("19"))],
                   printed_gross=Decimal("1.79"))
    data = to_dict(inv)
    assert data["issue_date"] == "2026-10-21" and data["lines"][0]["unit_price"] == "1.50"
    assert from_dict(data) == inv
    with pytest.raises(ConfigError, match="issue_date"):
        from_dict({**data, "issue_date": "31.02.2025"})
    with pytest.raises(ConfigError, match="lines.0.quantity"):
        from_dict({**data, "lines": [{**data["lines"][0], "quantity": "two"}]})
