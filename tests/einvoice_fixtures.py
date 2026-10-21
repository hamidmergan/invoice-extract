"""A complete, valid e-invoice for the tests (invented data)."""

import datetime
from decimal import Decimal

from invoice_extract.einvoicing.model import EInvoice, Line, Party


def valid_invoice(**changes) -> EInvoice:
    inv = EInvoice(
        number="R-2026-001", issue_date=datetime.date(2026, 10, 21), currency="EUR",
        seller=Party(name="ACME GmbH", street="Main Street 1", postcode="12345", city="Berlin", country="DE",
                     vat_id="DE123456789", contact_name="Jane Doe", phone="+49 30 123456",
                     email="billing@acme.example"),
        buyer=Party(name="Beta AG", street="Side Road 2", postcode="54321", city="Hamburg", country="DE",
                    email="ap@beta.example"),
        buyer_reference="PO-77", due_date=datetime.date(2026, 11, 20), payment_terms="Payable within 30 days.",
        payment_means="transfer", iban="DE89370400440532013000", bic="COBADEFFXXX",
        lines=[Line(description="Consulting", quantity=Decimal("2"), unit="HUR", unit_price=Decimal("50.00"),
                    vat_rate=Decimal("19"), amount=Decimal("100.00"))],
        printed_net=Decimal("100.00"), printed_vat=Decimal("19.00"), printed_gross=Decimal("119.00"),
    )
    for key, value in changes.items():
        setattr(inv, key, value)
    return inv
