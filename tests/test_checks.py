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


def test_vat_rate_matches_and_mismatches():
    ok = run_checks(make_row(**FULL, net=Decimal("100.00"), vat=Decimal("19.00"), vat_rate="19"), STANDARD_COLUMNS)
    assert [c.level for c in by_name(ok, "vat_rate")] == ["ok"]
    row = make_row(**FULL, net=Decimal("100.00"), vat=Decimal("19.00"), vat_rate="7")
    [bad] = by_name(run_checks(row, STANDARD_COLUMNS), "vat_rate")
    assert bad.level == "warning"
    assert bad.message == "VAT (19.00) is not 7 % of net (100.00); that would be 7.00"
    rounding = make_row(**FULL, net=Decimal("10.01"), vat=Decimal("1.91"), vat_rate="19")  # 1.9019 → within 2 cents
    assert [c.level for c in by_name(run_checks(rounding, STANDARD_COLUMNS), "vat_rate")] == ["ok"]


def test_vat_rate_mixed_or_missing_not_checked():
    mixed = make_row(**FULL, net=Decimal("100.00"), vat=Decimal("13.00"), vat_rate="19, 7")
    assert by_name(run_checks(mixed, STANDARD_COLUMNS), "vat_rate") == []
    assert by_name(run_checks(make_row(**FULL, vat_rate="19"), STANDARD_COLUMNS), "vat_rate") == []
    assert by_name(run_checks(make_row(**FULL, net=Decimal("1"), vat=Decimal("0"), vat_rate="x"), STANDARD_COLUMNS),
                   "vat_rate") == []


def test_vat_id_missing_from_250():
    small = run_checks(make_row(**FULL), STANDARD_COLUMNS)
    assert by_name(small, "vat_id_missing") == []
    big = make_row(**{**FULL, "gross": Decimal("250.00")})
    [check] = by_name(run_checks(big, STANDARD_COLUMNS), "vat_id_missing")
    assert check.level == "warning"
    assert check.message == "No supplier VAT ID or tax number; needed from 250.00 upwards for input VAT deduction"
    with_id = make_row(**{**FULL, "gross": Decimal("250.00")}, supplier_vat_id="DE123456789")
    assert by_name(run_checks(with_id, STANDARD_COLUMNS), "vat_id_missing") == []
    credit = make_row(**{**FULL, "gross": Decimal("250.00")}, doc_type="credit note")
    assert by_name(run_checks(credit, STANDARD_COLUMNS), "vat_id_missing") == []
    without_column = [c for c in STANDARD_COLUMNS if c.key != "supplier_vat_id"]
    assert by_name(run_checks(big, without_column), "vat_id_missing") == []


def test_checks_use_the_roles_of_own_columns():
    from invoice_extract.columns import Column
    columns = [Column("netto", "Netto", type="amount", role="net"), Column("ust", "USt", type="amount", role="vat"),
               Column("gesamt", "Gesamt", type="amount", role="gross")]
    row = Row(path="a.pdf", cells={"netto": Cell(value=Decimal("100.00")), "ust": Cell(value=Decimal("19.00")),
                                   "gesamt": Cell(value=Decimal("119.00"))})
    assert [c.level for c in by_name(run_checks(row, columns), "totals")] == ["ok"]


def test_duplicates_same_supplier_and_number():
    from invoice_extract.checks import duplicate_checks
    a = Row(path="a.pdf", cells={"supplier": Cell(value="ACME GmbH"), "invoice_no": Cell(value="RE-1")})
    b = Row(path="b.pdf", cells={"supplier": Cell(value="acme  gmbh"), "invoice_no": Cell(value=" re-1 ")})
    c = Row(path="c.pdf", cells={"supplier": Cell(value="Other"), "invoice_no": Cell(value="RE-1")})
    duplicate_checks([a, b, c])
    assert [x.message for x in by_name(a.checks, "duplicate")] == ["Same invoice number as b.pdf"]
    assert [x.message for x in by_name(b.checks, "duplicate")] == ["Same invoice number as a.pdf"]
    assert by_name(c.checks, "duplicate") == []
    assert a.status == "warning"


def test_duplicates_skip_empty_number_and_errors():
    from invoice_extract.checks import duplicate_checks
    a = Row(path="a.pdf", cells={"supplier": Cell(value="ACME"), "invoice_no": Cell(value=None)})
    b = Row(path="b.pdf", cells={"supplier": Cell(value="ACME"), "invoice_no": Cell(value="")})
    c = Row(path="c.pdf", cells={"supplier": Cell(value="ACME"), "invoice_no": Cell(value="1")}, error="broken")
    d = Row(path="d.pdf", cells={"supplier": Cell(value="ACME"), "invoice_no": Cell(value="1")})
    duplicate_checks([a, b, c, d])
    assert all(by_name(r.checks, "duplicate") == [] for r in (a, b, c, d))


def test_duplicates_rerun_does_not_stack():
    from invoice_extract.checks import duplicate_checks
    a = Row(path="a.pdf", cells={"supplier": Cell(value="ACME"), "invoice_no": Cell(value="1")})
    b = Row(path="b.pdf", cells={"supplier": Cell(value="ACME"), "invoice_no": Cell(value="1")})
    duplicate_checks([a, b])
    duplicate_checks([a, b])
    assert len(by_name(a.checks, "duplicate")) == 1
    b.cells["invoice_no"].value = "2"
    duplicate_checks([a, b])
    assert by_name(a.checks, "duplicate") == [] and by_name(b.checks, "duplicate") == []


def test_duplicates_with_own_columns_use_roles():
    from invoice_extract.checks import duplicate_checks
    from invoice_extract.columns import Column
    columns = [Column("firma", "Firma", role="supplier"), Column("nr", "Nr", role="invoice_no")]
    a = Row(path="a.pdf", cells={"firma": Cell(value="ACME"), "nr": Cell(value="1")})
    b = Row(path="b.pdf", cells={"firma": Cell(value="ACME"), "nr": Cell(value="1")})
    duplicate_checks([a, b], columns)
    assert len(by_name(a.checks, "duplicate")) == 1
