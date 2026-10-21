"""Rules an e-invoice must meet: EN 16931 for both formats, the German XRechnung rules (BR-DE) on top."""

import re
from dataclasses import dataclass
from typing import List, Optional

from ..checks import iban_valid
from .codes import COUNTRIES, CURRENCIES, SUPPORTED_CATEGORIES, UNITS, ZERO_CATEGORIES
from .model import CENT, EInvoice, cents, category, tax_groups, totals

FORMATS = ("xrechnung", "zugferd")
# XRechnung's Skonto line (BR-DE-18); every line of the payment terms that starts with "#" must be one
SKONTO_LINE = re.compile(r"#SKONTO#TAGE=[0-9]+#PROZENT=[0-9]+\.[0-9]{2}(#BASISBETRAG=-?[0-9]+\.[0-9]{2})?#")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@dataclass
class Issue:
    field: str  # dotted path, e.g. "buyer.email" or "lines.2.vat_rate"
    level: str  # "error" or "warning"
    rule: str
    message: str


def check(inv: EInvoice, format: str) -> List[Issue]:
    """Everything that keeps `inv` from being a valid e-invoice in `format` ("xrechnung" or "zugferd")."""
    issues: List[Issue] = []

    def need(ok: bool, field: str, rule: str, message: str, german: bool = False) -> None:
        if not ok:
            level = "error" if not german or format == "xrechnung" else "warning"
            issues.append(Issue(field, level, rule, message))

    need(bool(inv.number), "number", "BR-02", "The invoice number is missing.")
    need(inv.issue_date is not None, "issue_date", "BR-03", "The invoice date is missing.")
    need(inv.type_code in ("380", "381"), "type_code", "BR-04", "The document type must be 380 or 381.")
    need(inv.currency in CURRENCIES, "currency", "BR-CL-03", "The currency must be an ISO 4217 code, e.g. EUR.")
    need(bool(inv.seller.name), "seller.name", "BR-06", "The seller name is missing.")
    need(bool(inv.buyer.name), "buyer.name", "BR-07", "The buyer name is missing.")
    need(inv.seller.country in COUNTRIES, "seller.country", "BR-CL-14",
         "The seller country must be an ISO country code, e.g. DE (GB for the United Kingdom).")
    need(inv.buyer.country in COUNTRIES, "buyer.country", "BR-CL-14",
         "The buyer country must be an ISO country code, e.g. DE (GB for the United Kingdom).")
    for who in ("seller", "buyer"):
        vat_id = getattr(inv, who).vat_id
        if vat_id:
            need(len(vat_id) > 2 and (vat_id[:2] in COUNTRIES or vat_id[:2] == "EL"), f"{who}.vat_id", "BR-CO-09",
                 "A VAT ID starts with its country code, e.g. DE123456789.")
    need(bool(inv.seller.vat_id or inv.seller.legal_id), "seller.legal_id", "BR-CO-26",
         "Without a VAT ID the seller needs a legal registration ID, e.g. the commercial register number HRB 12345.")
    if inv.period_start is not None and inv.period_end is not None:
        need(inv.period_start <= inv.period_end, "period_end", "BR-29", "The service period ends before it starts.")
    need(bool(inv.lines), "lines", "BR-16", "The invoice needs at least one line.")

    for i, line in enumerate(inv.lines):
        path = f"lines.{i}"
        need(bool(line.description), f"{path}.description", "BR-25", "The line has no description.")
        need(line.quantity is not None, f"{path}.quantity", "BR-22", "The line has no quantity.")
        need(line.unit_price is not None, f"{path}.unit_price", "BR-26", "The line has no unit price.")
        need(line.unit in UNITS, f"{path}.unit", "BR-CL-23", "Unknown unit; use a unit code such as C62 (piece), "
             "HUR (hour), DAY, MON (month), KGM (kg), LTR (litre), MTR (metre) or KWH.")
        if line.unit_price is not None:
            need(line.unit_price >= 0, f"{path}.unit_price", "BR-27",
                 "The unit price must not be negative; put the minus on the quantity.")
        cat = category(line)
        if cat not in SUPPORTED_CATEGORIES:
            issues.append(Issue(f"{path}.vat_category", "error", "BR-CL-18",
                                f"VAT category {cat!r} is not supported; use S, Z, E or AE."))
        elif line.vat_rate is None and not line.vat_category:
            issues.append(Issue(f"{path}.vat_rate", "error", "BR-CO-04", "The line has no VAT rate."))
        elif cat == "S" and line.vat_rate is not None and line.vat_rate <= 0:
            issues.append(Issue(f"{path}.vat_rate", "error", "BR-S-05", "A standard-rated line needs a rate above 0."))
        elif cat in ZERO_CATEGORIES and line.vat_rate is not None and line.vat_rate != 0:
            issues.append(Issue(f"{path}.vat_rate", "error", f"BR-{cat}-05",
                                f"VAT category {cat} needs a rate of 0."))
        if line.amount is not None and line.quantity is not None and line.unit_price is not None:
            if abs(cents(line.amount) - cents(line.quantity * line.unit_price)) > CENT:
                issues.append(Issue(f"{path}.amount", "error", "BR-LINE",
                                    f"The line amount {line.amount} is not quantity × price "
                                    f"({cents(line.quantity * line.unit_price)})."))

    groups = tax_groups(inv)
    cats = {g.category for g in groups}
    if "S" in cats:
        need(bool(inv.seller.vat_id or inv.seller.tax_number), "seller.vat_id", "BR-S-02",
             "Standard-rated VAT needs the seller's VAT ID or tax number.")
    if cats & {"E", "Z"}:
        need(bool(inv.seller.vat_id or inv.seller.tax_number), "seller.tax_number", "BR-E-02",
             "Lines without VAT need the seller's VAT ID or tax number.")
    if cats & {"E", "AE"}:
        need(bool(inv.exemption_reason), "exemption_reason", "BR-E-10",
             "Lines without VAT need an exemption reason, e.g. 'Kleinunternehmer gemäß § 19 UStG' or "
             "'Reverse charge'.")
    if "AE" in cats:
        need(bool(inv.seller.vat_id), "seller.vat_id", "BR-AE-02", "Reverse charge needs the seller's VAT ID.")
        need(bool(inv.buyer.vat_id), "buyer.vat_id", "BR-AE-02", "Reverse charge needs the buyer's VAT ID.")

    t = totals(inv)
    for field, printed, computed, label in (("net", inv.printed_net, t.tax_basis, "net total"),
                                             ("vat", inv.printed_vat, t.tax_total, "VAT total"),
                                             ("gross", inv.printed_gross, t.grand_total, "gross total")):
        if printed is not None and inv.lines and abs(cents(printed) - computed) > CENT:
            issues.append(Issue(field, "error", "BR-CO-15",
                                f"The printed {label} {printed} does not match the lines ({computed})."))
    if t.due > 0:
        need(inv.due_date is not None or bool(inv.payment_terms), "due_date", "BR-CO-25",
             "An amount due needs a due date or payment terms.")

    for field, iban in (("iban", inv.iban), ("debited_iban", inv.debited_iban)):
        if iban and not iban_valid(iban):
            issues.append(Issue(field, "error", "IBAN", "The IBAN checksum is invalid."))

    # German XRechnung rules: errors for XRechnung, warnings for ZUGFeRD
    hash_lines = [line.strip() for line in inv.payment_terms.splitlines() if line.strip().startswith("#")]
    need(all(SKONTO_LINE.fullmatch(line) for line in hash_lines), "payment_terms", "BR-DE-18",
         "Lines starting with # must be Skonto terms like #SKONTO#TAGE=10#PROZENT=2.00#.", german=True)
    need(bool(inv.buyer_reference), "buyer_reference", "BR-DE-15",
         "XRechnung needs a buyer reference (Leitweg-ID or any reference agreed with the buyer).", german=True)
    need(bool(inv.seller.city), "seller.city", "BR-DE-3", "XRechnung needs the seller's city.", german=True)
    need(bool(inv.seller.postcode), "seller.postcode", "BR-DE-4", "XRechnung needs the seller's postcode.",
         german=True)
    need(bool(inv.buyer.city), "buyer.city", "BR-DE-8", "XRechnung needs the buyer's city.", german=True)
    need(bool(inv.buyer.postcode), "buyer.postcode", "BR-DE-9", "XRechnung needs the buyer's postcode.", german=True)
    need(bool(inv.seller.contact_name), "seller.contact_name", "BR-DE-5", "XRechnung needs a seller contact person.",
         german=True)
    need(bool(inv.seller.phone), "seller.phone", "BR-DE-6", "XRechnung needs the seller's phone number.", german=True)
    need(bool(_EMAIL.fullmatch(inv.seller.email)), "seller.email", "BR-DE-7",
         "XRechnung needs the seller's e-mail address.", german=True)
    need(bool(_EMAIL.fullmatch(inv.buyer.email)), "buyer.email", "BT-49",
         "XRechnung needs the buyer's e-mail address (electronic address).", german=True)
    if inv.payment_means == "debit":
        need(bool(inv.mandate_reference), "mandate_reference", "BR-DE-24", "A direct debit needs the mandate reference.",
             german=True)
        need(bool(inv.creditor_id), "creditor_id", "BR-DE-30", "A direct debit needs the creditor ID.", german=True)
        need(bool(inv.debited_iban), "debited_iban", "BR-DE-31", "A direct debit needs the debited IBAN.", german=True)
    else:
        need(bool(inv.iban), "iban", "BR-DE-23", "A transfer needs the seller's IBAN.", german=True)
    return issues


def errors(inv: EInvoice, format: str) -> List[Issue]:
    return [i for i in check(inv, format) if i.level == "error"]


def first_error(inv: EInvoice, format: str) -> Optional[Issue]:
    found = errors(inv, format)
    return found[0] if found else None


def hints(inv: EInvoice) -> List[Issue]:
    """Recommendations that do not block writing (level "info")."""
    out = []
    if inv.delivery_date is None and (inv.period_start is None or inv.period_end is None):
        out.append(Issue("delivery_date", "info", "BR-DE-TMP-32",
                         "Recommended: a delivery date or a service period (start and end)."))
    return out
