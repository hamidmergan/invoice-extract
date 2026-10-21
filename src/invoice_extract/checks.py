"""Plain checks on a finished row: totals, IBAN checksum, dates, required columns."""

import datetime
from decimal import Decimal
from typing import Any, List

from .columns import RESERVED, Column
from .results import Check, Row


def _value(row: Row, key: str) -> Any:
    cell = row.cells.get(key)
    return None if cell is None else cell.value


def iban_valid(iban: str) -> bool:
    """ISO 13616 mod-97 check."""
    if not 15 <= len(iban) <= 34 or not iban.isascii() or not iban.isalnum():
        return False
    moved = iban[4:] + iban[:4]
    return int("".join(str(int(ch, 36)) for ch in moved)) % 97 == 1


def run_checks(row: Row, columns: List[Column]) -> List[Check]:
    """Checks that apply to `row`; checks without the values they need are skipped."""
    checks = []

    net, vat, gross = (_value(row, k) for k in ("net", "vat", "gross"))
    if all(isinstance(v, Decimal) for v in (net, vat, gross)):
        if abs(net + vat - gross) <= Decimal("0.01"):
            checks.append(Check("totals", "ok", "Net + VAT equals gross"))
        else:
            checks.append(Check("totals", "error", f"Net + VAT ({net} + {vat}) does not equal gross ({gross})"))

    iban = _value(row, "iban")
    if isinstance(iban, str) and iban:
        if iban_valid(iban):
            checks.append(Check("iban", "ok", "IBAN checksum is valid"))
        else:
            checks.append(Check("iban", "error", "IBAN checksum is invalid"))

    invoice_date, due_date = _value(row, "invoice_date"), _value(row, "due_date")
    if isinstance(invoice_date, datetime.date) and isinstance(due_date, datetime.date) and due_date < invoice_date:
        checks.append(Check("due_before_invoice", "warning", f"Due date {due_date} is before invoice date {invoice_date}"))

    for col in columns:
        if col.required and col.key not in RESERVED and _value(row, col.key) in (None, ""):
            checks.append(Check("required", "warning", f"Missing: {col.label}"))

    return checks
