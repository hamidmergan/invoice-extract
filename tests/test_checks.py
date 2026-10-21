import datetime
from decimal import Decimal

from invoice_extract.checks import run_checks
from invoice_extract.columns import STANDARD_COLUMNS
from invoice_extract.results import Cell, Row


def make_row(**values):
    return Row(path="a.pdf", cells={k: Cell(value=v) for k, v in values.items()})


FULL = dict(
    supplier="ACME GmbH",
    invoice_no="RE-1",
    invoice_date=datetime.date(2026, 10, 21),
    gross=Decimal("119.00"),
)


def by_name(checks, name):
    return [c for c in checks if c.name == name]


def test_totals_ok_and_error():
    ok = run_checks(make_row(**FULL, net=Decimal("100.00"), vat=Decimal("19.00")), STANDARD_COLUMNS)
    assert [c.level for c in by_name(ok, "totals")] == ["ok"]
    row = make_row(**{**FULL, "gross": Decimal("119.02")}, net=Decimal("100.00"), vat=Decimal("19.00"))
    [bad] = by_name(run_checks(row, STANDARD_COLUMNS), "totals")
    assert bad.level == "error"
    assert bad.message == "Net + VAT (100.00 + 19.00) does not equal gross (119.02)"


def test_totals_skipped_without_all_amounts():
    assert by_name(run_checks(make_row(**FULL, net=Decimal("100.00")), STANDARD_COLUMNS), "totals") == []


def test_iban_mod97():
    [ok] = by_name(run_checks(make_row(**FULL, iban="DE89370400440532013000"), STANDARD_COLUMNS), "iban")
    assert ok.level == "ok"
    [bad] = by_name(run_checks(make_row(**FULL, iban="DE89370400440532013001"), STANDARD_COLUMNS), "iban")
    assert (bad.level, bad.message) == ("error", "IBAN checksum is invalid")
    assert by_name(run_checks(make_row(**FULL, iban=""), STANDARD_COLUMNS), "iban") == []


def test_due_before_invoice():
    row = make_row(**FULL, due_date=datetime.date(2026, 10, 1))
    [check] = by_name(run_checks(row, STANDARD_COLUMNS), "due_before_invoice")
    assert check.level == "warning"
    later = make_row(**FULL, due_date=datetime.date(2026, 11, 20))
    assert by_name(run_checks(later, STANDARD_COLUMNS), "due_before_invoice") == []


def test_required_missing():
    row = make_row(supplier="", invoice_no="RE-1", invoice_date=datetime.date(2026, 10, 21))
    missing = by_name(run_checks(row, STANDARD_COLUMNS), "required")
    assert [c.message for c in missing] == ["Missing: Supplier", "Missing: Gross"]
    assert {c.level for c in missing} == {"warning"}
    assert run_checks(make_row(**FULL), STANDARD_COLUMNS) == []
