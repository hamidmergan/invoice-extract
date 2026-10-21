"""The typed e-invoice: parties, lines, VAT groups and totals as EN 16931 defines them."""

import datetime
from dataclasses import asdict, dataclass, field, fields
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from ..errors import ConfigError

CENT = Decimal("0.01")


@dataclass
class Party:
    name: str = ""
    street: str = ""
    postcode: str = ""
    city: str = ""
    country: str = ""  # ISO 3166-1 alpha-2, e.g. "DE"
    vat_id: str = ""
    tax_number: str = ""
    contact_name: str = ""
    phone: str = ""
    email: str = ""


@dataclass
class Line:
    description: str = ""
    quantity: Optional[Decimal] = Decimal("1")
    unit: str = "C62"  # UN/ECE Recommendation 20 code
    unit_price: Optional[Decimal] = None  # net price per unit
    vat_rate: Optional[Decimal] = None  # percent, e.g. 19
    vat_category: str = ""  # "" derives S or E from the rate
    amount: Optional[Decimal] = None  # net line amount as printed


@dataclass
class EInvoice:
    number: str = ""
    type_code: str = "380"  # 380 invoice, 381 credit note
    issue_date: Optional[datetime.date] = None
    currency: str = "EUR"
    seller: Party = field(default_factory=Party)
    buyer: Party = field(default_factory=Party)
    buyer_reference: str = ""  # Leitweg-ID or another buyer reference (BT-10)
    period_start: Optional[datetime.date] = None
    period_end: Optional[datetime.date] = None
    delivery_date: Optional[datetime.date] = None
    due_date: Optional[datetime.date] = None
    payment_terms: str = ""
    payment_means: str = "transfer"  # "transfer" (58) or "debit" (59)
    iban: str = ""  # the seller's account for a transfer
    bic: str = ""
    mandate_reference: str = ""
    creditor_id: str = ""
    debited_iban: str = ""  # the buyer's account for a direct debit
    exemption_reason: str = ""  # required for category E
    notes: List[str] = field(default_factory=list)
    lines: List[Line] = field(default_factory=list)
    printed_net: Optional[Decimal] = None
    printed_vat: Optional[Decimal] = None
    printed_gross: Optional[Decimal] = None


@dataclass
class TaxGroup:
    category: str
    rate: Decimal
    basis: Decimal
    tax: Decimal


@dataclass
class Totals:
    line_total: Decimal
    tax_basis: Decimal
    tax_total: Decimal
    grand_total: Decimal
    due: Decimal


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def line_amount(line: Line) -> Decimal:
    """The printed line amount, else quantity × unit price, rounded to cents."""
    if line.amount is not None:
        return cents(line.amount)
    if line.quantity is None or line.unit_price is None:
        return Decimal("0.00")
    return cents(line.quantity * line.unit_price)


def category(line: Line) -> str:
    if line.vat_category:
        return line.vat_category
    return "S" if line.vat_rate is not None and line.vat_rate > 0 else "E"


def tax_groups(inv: EInvoice) -> List[TaxGroup]:
    """One group per VAT category and rate; tax = basis × rate, rounded to cents."""
    bases: Dict[tuple, Decimal] = {}
    for line in inv.lines:
        key = (category(line), line.vat_rate if line.vat_rate is not None else Decimal("0"))
        bases[key] = bases.get(key, Decimal("0.00")) + line_amount(line)
    return [TaxGroup(cat, rate, cents(basis), cents(basis * rate / 100))
            for (cat, rate), basis in sorted(bases.items(), key=lambda kv: (kv[0][0], kv[0][1]))]


def totals(inv: EInvoice) -> Totals:
    line_total = cents(sum((line_amount(line) for line in inv.lines), Decimal("0.00")))
    tax_total = cents(sum((g.tax for g in tax_groups(inv)), Decimal("0.00")))
    grand = line_total + tax_total
    return Totals(line_total, line_total, tax_total, grand, grand)


# --- JSON-safe dicts, for the web UI ---------------------------------------------------------

def _out(value: Any) -> Any:
    if isinstance(value, (Decimal, datetime.date)):
        return str(value) if isinstance(value, Decimal) else value.isoformat()
    return value


def to_dict(inv: EInvoice) -> Dict[str, Any]:
    data = asdict(inv)

    def clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: clean(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [clean(v) for v in obj]
        return _out(obj)
    return clean(data)


def parse_decimal(value: Any, name: str) -> Optional[Decimal]:
    """A number as typed: "1234.5", "1.234,50", "1234,50" or a number. Empty is None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ConfigError(f"{name}: not a number")
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = str(value).strip().replace(" ", "").replace("%", "")
    if "," in text and "." in text:
        dec = max(text.rfind(","), text.rfind("."))
        text = text[:dec].replace(",", "").replace(".", "") + "." + text[dec + 1:]
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ConfigError(f"{name}: {value!r} is not a number") from None


def parse_date(value: Any, name: str) -> Optional[datetime.date]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value).strip())
    except ValueError:
        raise ConfigError(f"{name}: {value!r} is not a date (YYYY-MM-DD)") from None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _party(data: Any) -> Party:
    data = data if isinstance(data, dict) else {}
    return Party(**{f.name: _text(data.get(f.name)) for f in fields(Party)})


def _line(data: Any, i: int) -> Line:
    data = data if isinstance(data, dict) else {}
    return Line(
        description=_text(data.get("description")),
        quantity=parse_decimal(data.get("quantity"), f"lines.{i}.quantity"),
        unit=_text(data.get("unit")) or "C62",
        unit_price=parse_decimal(data.get("unit_price"), f"lines.{i}.unit_price"),
        vat_rate=parse_decimal(data.get("vat_rate"), f"lines.{i}.vat_rate"),
        vat_category=_text(data.get("vat_category")).upper(),
        amount=parse_decimal(data.get("amount"), f"lines.{i}.amount"),
    )


DATES = ("issue_date", "period_start", "period_end", "delivery_date", "due_date")
AMOUNTS = ("printed_net", "printed_vat", "printed_gross")


def from_dict(data: Dict[str, Any]) -> EInvoice:
    """An EInvoice from a dict as `to_dict` writes it. A bad value raises ConfigError naming the field."""
    inv = EInvoice()
    for f in fields(EInvoice):
        if f.name not in data:
            continue
        value = data[f.name]
        if f.name in DATES:
            setattr(inv, f.name, parse_date(value, f.name))
        elif f.name in AMOUNTS:
            setattr(inv, f.name, parse_decimal(value, f.name))
        elif f.name in ("seller", "buyer"):
            setattr(inv, f.name, _party(value))
        elif f.name == "lines":
            inv.lines = [_line(item, i) for i, item in enumerate(value if isinstance(value, list) else [])]
        elif f.name == "notes":
            inv.notes = [_text(n) for n in (value if isinstance(value, list) else []) if _text(n)]
        else:
            setattr(inv, f.name, _text(value))
    return inv
