"""Turn LLM answers into typed values and compare them with the text on the page."""

import datetime
import re
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional, Tuple

from .results import Check

DATE_FORMATS = (
    "%d.%m.%Y",
    "%d.%m.%y",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%d %B %Y",
)


def _unique(items: list) -> list:
    out = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def _iso_date(value: Any) -> Optional[datetime.date]:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _raw_dates(raw: str, value: Optional[datetime.date] = None) -> List[datetime.date]:
    found = []
    for fmt in DATE_FORMATS:
        try:
            found.append(datetime.datetime.strptime(raw.strip(), fmt).date())
        except ValueError:
            pass
    return _unique(found)


def _decimal(value: Any) -> Optional[Decimal]:
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        value = repr(value)
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


# A number as printed, with an optional minus in front or behind; a hyphen inside a word ("Netto-Betrag") is no minus.
_NUMBER = re.compile(r"(?<![\w-])(-\s?)?(\d(?:[\d.,]*\d)?)(-(?!\w))?")


def _readings(digits: str) -> List[str]:
    """Plausible readings of one printed number, e.g. "1.234" gives two."""
    if "," in digits and "." in digits:
        dec = max(digits.rfind(","), digits.rfind("."))
        return [digits[:dec].replace(",", "").replace(".", "") + "." + digits[dec + 1:]]
    if "," in digits or "." in digits:
        sep = "," if "," in digits else "."
        parts = digits.split(sep)
        if len(parts) > 2:
            return ["".join(parts)]
        if len(parts[1]) == 3:
            return ["".join(parts), parts[0] + "." + parts[1]]
        return [parts[0] + "." + parts[1]]
    return [digits]


def _raw_amounts(raw: str, value: Optional[Decimal] = None) -> List[Decimal]:
    """Readings of the amount printed in `raw`. With several numbers, those of the one that matches `value`."""
    numbers = []
    for match in _NUMBER.finditer(raw):
        sign = "-" if match.group(1) or match.group(3) else ""
        readings = _unique([d for d in (_decimal(sign + r) for r in _readings(match.group(2))) if d is not None])
        if value is not None and value in readings:
            return readings
        if readings:
            numbers.append(readings)
    return _unique([n for readings in numbers for n in readings])


def _compare(kind: str, value: Any, candidates: list, raw: str) -> List[Check]:
    checks = []
    if len(candidates) > 1 and value in candidates:
        name = "date_ambiguous" if kind == "date" else "amount_ambiguous"
        checks.append(Check(name, "warning", f'"{raw.strip()}" can be read in more than one way'))
    if candidates and value not in candidates:
        checks.append(Check("value_mismatch", "warning", f'Value {value} does not match "{raw.strip()}" on the page'))
    return checks


def _typed(kind: str, value: Any, raw: Optional[str]) -> Tuple[Any, List[Check]]:
    parse, from_raw = (_iso_date, _raw_dates) if kind == "date" else (_decimal, _raw_amounts)
    raw = raw or ""
    parsed = parse(value)
    if parsed is None and isinstance(value, str):
        # The LLM may hand back the printed form, e.g. "21.10.2026" or "1.234,50".
        own = from_raw(value)
        if own:
            parsed = own[0]
    if parsed is None and raw.strip():
        found = from_raw(raw)
        if found:
            parsed = found[0]
    if parsed is None:
        return value, [Check("unparsed", "warning", f'Could not read "{value}" as {"a date" if kind == "date" else "an amount"}')]
    candidates = from_raw(raw, parsed) if raw.strip() else []
    return parsed, _compare(kind, parsed, candidates, raw)


def normalize(type: str, value: Any, raw: Optional[str]) -> Tuple[Any, List[Check]]:
    """Clean `value` for a column of `type`; `raw` is the text as printed, used for checks."""
    if value is None or (isinstance(value, str) and not value.strip() and type in ("date", "amount", "iban")):
        return None, []
    if type in ("date", "amount"):
        return _typed(type, value, raw)
    if type == "iban":
        return "".join(str(value).split()).upper(), []
    if type == "text":
        return str(value).strip(), []
    return value, []


def display_variants(type: str, value: Any) -> List[str]:
    """Ways a value may be printed, used to find it on the page."""
    if type == "date" and isinstance(value, datetime.date):
        return [value.strftime(f) for f in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d", "%m/%d/%Y")]
    if type == "amount" and isinstance(value, Decimal):
        places = max(2, -value.as_tuple().exponent)
        english = f"{value:,.{places}f}"
        plain = english.replace(",", "")
        german = english.replace(",", " ").replace(".", ",").replace(" ", ".")
        return _unique([german, english, plain.replace(".", ","), plain, english.replace(",", " ").replace(".", ",")])
    return [str(value)]
