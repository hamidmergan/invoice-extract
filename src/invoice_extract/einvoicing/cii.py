"""Build the UN/CEFACT CII XML of an e-invoice (EN 16931), as XRechnung or as the XML inside a ZUGFeRD PDF."""

from decimal import Decimal

from drafthorse.models.accounting import ApplicableTradeTax
from drafthorse.models.document import Document
from drafthorse.models.note import IncludedNote
from drafthorse.models.party import TaxRegistration
from drafthorse.models.payment import PaymentMeans, PaymentTerms
from drafthorse.models.tradelines import LineItem

from ..errors import ConfigError
from .model import EInvoice, Party, category, line_amount, tax_groups, totals
from .rules import errors

GUIDELINES = {
    "xrechnung": "urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0",
    "zugferd": "urn:cen.eu:en16931:2017",
}
BUSINESS_PROCESS = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"  # BT-23, required by XRechnung (PEPPOL-EN16931-R001)
PAYMENT_CODES = {"transfer": "58", "debit": "59"}


def _party(target, party: Party, contact: bool = False) -> None:
    target.name = party.name
    if party.postcode:
        target.address.postcode = party.postcode
    if party.street:
        target.address.line_one = party.street
    if party.city:
        target.address.city_name = party.city
    target.address.country_id = party.country
    if party.email:
        target.electronic_address.uri_ID = ("EM", party.email)
    if party.vat_id:
        target.tax_registrations.add(TaxRegistration(id=("VA", party.vat_id)))
    if party.tax_number:
        target.tax_registrations.add(TaxRegistration(id=("FC", party.tax_number)))
    if contact and (party.contact_name or party.phone or party.email):
        if party.contact_name:
            target.contact.person_name = party.contact_name
        if party.phone:
            target.contact.telephone.number = party.phone
        if party.email:
            target.contact.email.address = party.email


def build_cii(inv: EInvoice, format: str) -> bytes:
    """The CII XML of `inv`, checked against the Factur-X EN 16931 schema. Raises ConfigError if `inv` has errors."""
    problems = errors(inv, format)
    if problems:
        raise ConfigError("Cannot write the e-invoice: " + "; ".join(f"{i.field}: {i.message}" for i in problems))
    doc = Document()
    doc.context.business_parameter.id = BUSINESS_PROCESS
    doc.context.guideline_parameter.id = GUIDELINES[format]
    doc.header.id = inv.number
    doc.header.type_code = inv.type_code
    doc.header.issue_date_time = inv.issue_date
    for text in inv.notes:
        note = IncludedNote()
        note.content = text
        doc.header.notes.add(note)

    agreement = doc.trade.agreement
    if inv.buyer_reference:
        agreement.buyer_reference = inv.buyer_reference
    _party(agreement.seller, inv.seller, contact=True)
    _party(agreement.buyer, inv.buyer)
    if inv.delivery_date is not None:
        doc.trade.delivery.event.occurrence = inv.delivery_date

    settlement = doc.trade.settlement
    settlement.currency_code = inv.currency
    if inv.period_start is not None and inv.period_end is not None:
        settlement.period.start = inv.period_start
        settlement.period.end = inv.period_end
    means = PaymentMeans()
    means.type_code = PAYMENT_CODES.get(inv.payment_means, "58")
    if inv.payment_means == "debit":
        if inv.debited_iban:
            means.payer_account.iban = inv.debited_iban
        if inv.creditor_id:
            settlement.creditor_reference_id = inv.creditor_id
    else:
        if inv.iban:
            means.payee_account.iban = inv.iban
        if inv.bic:
            means.payee_institution.bic = inv.bic
    settlement.payment_means.add(means)
    if inv.payment_terms or inv.due_date is not None or inv.mandate_reference:
        terms = PaymentTerms()
        if inv.payment_terms:
            terms.description = inv.payment_terms
        if inv.due_date is not None:
            terms.due = inv.due_date
        if inv.payment_means == "debit" and inv.mandate_reference:
            terms.debit_mandate_id = inv.mandate_reference
        settlement.terms.add(terms)

    for group in tax_groups(inv):
        tax = ApplicableTradeTax()
        tax.calculated_amount = group.tax
        tax.type_code = "VAT"
        if group.category == "E" and inv.exemption_reason:
            tax.exemption_reason = inv.exemption_reason
        tax.basis_amount = group.basis
        tax.category_code = group.category
        tax.rate_applicable_percent = group.rate
        settlement.trade_tax.add(tax)

    t = totals(inv)
    summary = settlement.monetary_summation
    summary.line_total = t.line_total
    summary.charge_total = Decimal("0.00")
    summary.allowance_total = Decimal("0.00")
    summary.tax_basis_total = t.tax_basis
    summary.tax_total = (t.tax_total, inv.currency)
    summary.grand_total = t.grand_total
    if t.rounding:
        summary.rounding_amount = t.rounding
    summary.due_amount = t.due

    for i, line in enumerate(inv.lines, 1):
        item = LineItem()
        item.document.line_id = str(i)
        item.product.name = line.description
        item.agreement.net.amount = line.unit_price
        item.delivery.billed_quantity = (line.quantity, line.unit)
        item.settlement.trade_tax.type_code = "VAT"
        item.settlement.trade_tax.category_code = category(line)
        item.settlement.trade_tax.rate_applicable_percent = line.vat_rate if line.vat_rate is not None else Decimal("0")
        item.settlement.monetary_summation.total_amount = line_amount(line)
        doc.trade.items.add(item)
    return doc.serialize(schema="FACTUR-X_EN16931")
