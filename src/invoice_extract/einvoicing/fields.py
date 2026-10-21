"""The fields an EN 16931 e-invoice needs, as columns for the model, and the mapping of a read row to an EInvoice."""

import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..columns import Column
from ..errors import ConfigError
from ..results import Row
from .model import EInvoice, Line, Party, parse_decimal

LINE_FIELDS = ["Description", "Quantity", "Unit", "Unit Price", "VAT Rate", "Amount"]


def _f(key: str, label: str, hint: str, type: str = "text", role: str = "") -> Column:
    return Column(key, label, hint=hint, type=type, role=role)


EINVOICE_FIELDS: List[Column] = [
    _f("invoice_no", "Invoice No.", "The invoice number.", role="invoice_no"),
    _f("doc_type", "Document Type", 'Write "credit note" if the document is a credit note (Gutschrift), '
                                     'otherwise "invoice".'),
    _f("invoice_date", "Invoice Date", "The issue date of the invoice.", "date", "invoice_date"),
    _f("currency", "Currency", "3-letter ISO currency code, e.g. EUR.", role="currency"),
    _f("supplier", "Seller", "Name of the company that issued the invoice.", role="supplier"),
    _f("seller_street", "Seller Street", "Street and house number of the seller."),
    _f("seller_postcode", "Seller Postcode", "Postcode of the seller."),
    _f("seller_city", "Seller City", "City of the seller."),
    _f("seller_country", "Seller Country", "Country of the seller as a 2-letter ISO code, e.g. DE."),
    _f("seller_vat_id", "Seller VAT ID", "VAT identification number of the seller (USt-IdNr.), e.g. DE123456789."),
    _f("seller_tax_number", "Seller Tax Number", "Tax number of the seller (Steuernummer), if printed."),
    _f("seller_contact", "Seller Contact", "Contact person at the seller, if printed."),
    _f("seller_phone", "Seller Phone", "Phone number of the seller."),
    _f("seller_email", "Seller E-mail", "E-mail address of the seller."),
    _f("buyer_name", "Buyer", "Name of the invoice recipient (company or person)."),
    _f("buyer_street", "Buyer Street", "Street and house number of the buyer."),
    _f("buyer_postcode", "Buyer Postcode", "Postcode of the buyer."),
    _f("buyer_city", "Buyer City", "City of the buyer."),
    _f("buyer_country", "Buyer Country", "Country of the buyer as a 2-letter ISO code, e.g. DE."),
    _f("buyer_vat_id", "Buyer VAT ID", "VAT identification number of the buyer, if printed."),
    _f("buyer_email", "Buyer E-mail", "E-mail address of the buyer, if printed."),
    _f("buyer_reference", "Buyer Reference", "Leitweg-ID, or the buyer's order or reference number, if printed."),
    _f("period_start", "Service Period Start", "First day of the billing or service period.", "date"),
    _f("period_end", "Service Period End", "Last day of the billing or service period.", "date"),
    _f("delivery_date", "Delivery Date", "Delivery or service date, if printed.", "date"),
    _f("due_date", "Due Date", "Date by which the amount is paid or debited.", "date", "due_date"),
    _f("payment_terms", "Payment Terms", "The payment terms as printed, e.g. 'Payable within 14 days'."),
    _f("payment_means", "Payment Means", 'Write "direct debit" if the amount is debited from the buyer '
                                         '(Lastschrift, SEPA mandate), otherwise "transfer".'),
    _f("iban", "Seller IBAN", "The seller's bank account (IBAN) for transfers.", "iban", "iban"),
    _f("bic", "BIC", "BIC of the seller's bank."),
    _f("mandate_reference", "Mandate Reference", "SEPA mandate reference (Mandatsreferenz) for a direct debit."),
    _f("creditor_id", "Creditor ID", "SEPA creditor identifier (Gläubiger-ID) for a direct debit."),
    _f("debited_iban", "Buyer IBAN", "The buyer's account that is debited, if printed.", "iban"),
    _f("net", "Net Total", "Total amount without VAT.", "amount", "net"),
    _f("vat", "VAT Total", "Total VAT amount.", "amount", "vat"),
    _f("gross", "Gross Total", "Total amount including VAT.", "amount", "gross"),
    _f("exemption_reason", "VAT Exemption Reason",
       "Why no VAT is charged, if printed, e.g. 'Kleinunternehmer gemäß § 19 UStG' or 'Reverse charge'."),
    Column("notes", "Notes", hint="Notes for the buyer that are printed on the invoice.", kind="text"),
    Column("lines", "Invoice Lines", hint="Every invoice line (position): description, quantity, unit, net unit "
                                          "price, VAT rate in percent, net line amount.",
           kind="table", table_columns=list(LINE_FIELDS)),
]

UNIT_CODES = {
    "c62": "C62", "stk": "C62", "stk.": "C62", "stück": "C62", "piece": "C62", "pieces": "C62", "pcs": "C62",
    "x": "C62", "pauschal": "C62", "psch": "C62", "flat": "C62",
    "h": "HUR", "std": "HUR", "std.": "HUR", "stunde": "HUR", "stunden": "HUR", "hour": "HUR", "hours": "HUR",
    "hur": "HUR", "tag": "DAY", "tage": "DAY", "day": "DAY", "days": "DAY", "monat": "MON", "monate": "MON",
    "month": "MON", "months": "MON", "kg": "KGM", "l": "LTR", "liter": "LTR", "litre": "LTR", "m": "MTR",
    "meter": "MTR", "metre": "MTR", "km": "KMT", "m2": "MTK", "m²": "MTK", "qm": "MTK",
}

COUNTRIES = {
    "deutschland": "DE", "germany": "DE", "österreich": "AT", "austria": "AT", "schweiz": "CH",
    "switzerland": "CH", "frankreich": "FR", "france": "FR", "niederlande": "NL", "netherlands": "NL",
    "belgien": "BE", "belgium": "BE", "italien": "IT", "italy": "IT", "spanien": "ES", "spain": "ES",
    "polen": "PL", "poland": "PL", "luxemburg": "LU", "luxembourg": "LU", "dänemark": "DK", "denmark": "DK",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _decimal(value: Any) -> Optional[Decimal]:
    if isinstance(value, Decimal):
        return value
    try:
        return parse_decimal(value, "value")
    except ConfigError:
        return None


def _rate(value: Any) -> Optional[Decimal]:
    rate = _decimal(value)
    if rate is not None and 0 < rate <= 1:
        rate *= 100  # written as a fraction, e.g. 0.19
    return rate.quantize(Decimal("0.01")) if rate is not None else None


def _date(value: Any) -> Optional[datetime.date]:
    return value if isinstance(value, datetime.date) else None


def country(value: Any) -> str:
    text = _text(value)
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return COUNTRIES.get(text.lower(), text)


def unit_code(value: Any) -> str:
    return UNIT_CODES.get(_text(value).lower(), "C62")


def _line(data: Dict[str, Any]) -> Optional[Line]:
    description = _text(data.get("Description"))
    amount = _decimal(data.get("Amount"))
    if not description and amount is None:
        return None
    quantity = _decimal(data.get("Quantity"))
    return Line(description=description, quantity=quantity if quantity is not None else Decimal("1"),
                unit=unit_code(data.get("Unit")), unit_price=_decimal(data.get("Unit Price")),
                vat_rate=_rate(data.get("VAT Rate")), amount=amount)


def from_row(row: Row) -> EInvoice:
    """The EInvoice a read row describes. Values the model could not read stay empty for the user to fill."""
    def value(key: str) -> Any:
        cell = row.cells.get(key)
        return None if cell is None else cell.value

    inv = EInvoice()
    if row.error:
        return inv
    inv.number = _text(value("invoice_no"))
    doc_type = _text(value("doc_type")).lower()
    inv.type_code = "381" if "credit" in doc_type or "gutschrift" in doc_type else "380"
    inv.issue_date = _date(value("invoice_date"))
    inv.currency = _text(value("currency")).upper() or "EUR"
    inv.seller = Party(name=_text(value("supplier")), street=_text(value("seller_street")),
                       postcode=_text(value("seller_postcode")), city=_text(value("seller_city")),
                       country=country(value("seller_country")), vat_id=_text(value("seller_vat_id")),
                       tax_number=_text(value("seller_tax_number")), contact_name=_text(value("seller_contact")),
                       phone=_text(value("seller_phone")), email=_text(value("seller_email")))
    inv.buyer = Party(name=_text(value("buyer_name")), street=_text(value("buyer_street")),
                      postcode=_text(value("buyer_postcode")), city=_text(value("buyer_city")),
                      country=country(value("buyer_country")), vat_id=_text(value("buyer_vat_id")),
                      email=_text(value("buyer_email")))
    inv.buyer_reference = _text(value("buyer_reference"))
    for key in ("period_start", "period_end", "delivery_date", "due_date"):
        setattr(inv, key, _date(value(key)))
    inv.payment_terms = _text(value("payment_terms"))
    inv.iban = "".join(_text(value("iban")).split()).upper()
    inv.bic = _text(value("bic")).upper()
    inv.mandate_reference = _text(value("mandate_reference"))
    inv.creditor_id = _text(value("creditor_id"))
    inv.debited_iban = "".join(_text(value("debited_iban")).split()).upper()
    means = _text(value("payment_means")).lower()
    debit = any(word in means for word in ("debit", "lastschrift", "einzug")) or inv.mandate_reference
    inv.payment_means = "debit" if debit else "transfer"
    inv.printed_net, inv.printed_vat, inv.printed_gross = (_decimal(value(k)) for k in ("net", "vat", "gross"))
    inv.exemption_reason = _text(value("exemption_reason"))
    notes = _text(value("notes"))
    inv.notes = [notes] if notes else []
    lines = value("lines")
    for data in lines if isinstance(lines, list) else []:
        line = _line(data) if isinstance(data, dict) else None
        if line is not None:
            inv.lines.append(line)
    if not inv.lines and inv.printed_net is not None:
        rate = None
        if inv.printed_vat is not None and inv.printed_net > 0:
            rate = (inv.printed_vat / inv.printed_net * 100).quantize(Decimal("1"))
        inv.lines.append(Line(description=f"Invoice {inv.number}".strip(), unit_price=inv.printed_net,
                              vat_rate=rate, amount=inv.printed_net))
    return inv
