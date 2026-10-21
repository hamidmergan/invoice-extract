"""Plain checks on finished rows: totals, VAT rate, IBAN checksum, dates, required columns, duplicates."""

import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .columns import RESERVED, STANDARD_COLUMNS, Column
from .normalize import percent_rates
from .results import Check, Row

SMALL_AMOUNT = Decimal("250.00")  # invoices up to this amount need no VAT ID (§ 33 UStDV)
RATE_TOLERANCE = Decimal("0.02")


def _keys(columns: Sequence[Column]) -> Dict[str, str]:
    """Role -> key of the column with that meaning; a column without a role is found by its key."""
    return {c.role: c.key for c in columns if c.role}


def _value(row: Row, role: str, keys: Dict[str, str]) -> Any:
    cell = row.cells.get(keys.get(role, role))
    return None if cell is None else cell.value


def iban_valid(iban: str) -> bool:
    """ISO 13616 mod-97 check."""
    if not 15 <= len(iban) <= 34 or not iban.isascii() or not iban.isalnum():
        return False
    moved = iban[4:] + iban[:4]
    return int("".join(str(int(ch, 36)) for ch in moved)) % 97 == 1


def _single_rate(value: Any) -> Optional[Decimal]:
    if not isinstance(value, (str, int, float, Decimal)):
        return None
    rates = percent_rates(value)
    return Decimal(rates[0]) if len(rates) == 1 else None


def run_checks(row: Row, columns: List[Column]) -> List[Check]:
    """Checks that apply to `row`; checks without the values they need are skipped."""
    checks = []
    keys = _keys(columns)
    present = {c.key for c in columns} | set(keys)

    net, vat, gross = (_value(row, k, keys) for k in ("net", "vat", "gross"))
    if all(isinstance(v, Decimal) for v in (net, vat, gross)):
        if abs(net + vat - gross) <= Decimal("0.01"):
            checks.append(Check("totals", "ok", "Net + VAT equals gross"))
        else:
            checks.append(Check("totals", "error", f"Net + VAT ({net} + {vat}) does not equal gross ({gross})"))

    rate = _single_rate(_value(row, "vat_rate", keys))
    if rate is not None and isinstance(net, Decimal) and isinstance(vat, Decimal):
        expected = (net * rate / 100).quantize(Decimal("0.01"))
        if abs(expected - vat) <= RATE_TOLERANCE:
            checks.append(Check("vat_rate", "ok", f"VAT is {rate} % of net"))
        else:
            checks.append(Check("vat_rate", "warning",
                                f"VAT ({vat}) is not {rate} % of net ({net}); that would be {expected}"))

    iban = _value(row, "iban", keys)
    if isinstance(iban, str) and iban:
        if iban_valid(iban):
            checks.append(Check("iban", "ok", "IBAN checksum is valid"))
        else:
            checks.append(Check("iban", "error", "IBAN checksum is invalid"))

    invoice_date, due_date = _value(row, "invoice_date", keys), _value(row, "due_date", keys)
    if isinstance(invoice_date, datetime.date) and isinstance(due_date, datetime.date) and due_date < invoice_date:
        checks.append(Check("due_before_invoice", "warning", f"Due date {due_date} is before invoice date {invoice_date}"))

    if ("supplier_vat_id" in present and isinstance(gross, Decimal) and gross > SMALL_AMOUNT
            and not _value(row, "supplier_vat_id", keys) and _value(row, "doc_type", keys) != "credit note"):
        checks.append(Check("vat_id_missing", "warning", "No supplier VAT ID or tax number; needed above "
                                                         f"{SMALL_AMOUNT} for input VAT deduction"))

    for col in columns:
        if col.required and col.key not in RESERVED and _value(row, col.key, {}) in (None, ""):
            checks.append(Check("required", "warning", f"Missing: {col.label}"))

    return checks


def _folded(value: Any) -> str:
    return " ".join(str(value).split()).casefold() if value not in (None, "") else ""


def duplicate_checks(rows: List[Row], columns: Optional[Sequence[Column]] = None) -> None:
    """Warn on rows that share supplier and invoice number. Earlier duplicate checks are replaced."""
    keys = _keys(columns if columns is not None else STANDARD_COLUMNS)
    groups: Dict[tuple, List[Row]] = {}
    for row in rows:
        row.checks = [c for c in row.checks if c.name != "duplicate"]
        if row.error:
            continue
        number = _folded(_value(row, "invoice_no", keys))
        if number:
            groups.setdefault((_folded(_value(row, "supplier", keys)), number), []).append(row)
    for members in groups.values():
        if len(members) < 2:
            continue
        for row in members:
            others = ", ".join(Path(other.path).name for other in members if other is not row)
            row.checks.append(Check("duplicate", "warning", f"Same invoice number as {others}"))
