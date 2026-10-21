"""Read the standard fields of ZUGFeRD/Factur-X (CII) and XRechnung (UBL) invoices."""

import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException

from .errors import UnsupportedFileError

_RSM = "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
_RAM = "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
_UDT = "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100"
_CAC = "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
_CBC = "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"
NS = {"rsm": _RSM, "ram": _RAM, "udt": _UDT, "cac": _CAC, "cbc": _CBC}

CREDIT_NOTE_CODE = "381"
MAX_DESCRIPTION = 80

_S = "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement"
_SUM = f"{_S}/ram:SpecifiedTradeSettlementHeaderMonetarySummation"
_SELLER = "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement/ram:SellerTradeParty"
CII_PATHS: Dict[str, List[str]] = {
    "invoice_no": ["rsm:ExchangedDocument/ram:ID"],
    "invoice_date": ["rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString"],
    "due_date": [f"{_S}/ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString"],
    "supplier": [f"{_SELLER}/ram:Name"],
    "supplier_vat_id": [f"{_SELLER}/ram:SpecifiedTaxRegistration/ram:ID[@schemeID='VA']",
                        f"{_SELLER}/ram:SpecifiedTaxRegistration/ram:ID[@schemeID='FC']"],
    "currency": [f"{_S}/ram:InvoiceCurrencyCode"],
    "net": [f"{_SUM}/ram:TaxBasisTotalAmount"],
    "vat": [f"{_SUM}/ram:TaxTotalAmount"],
    "gross": [f"{_SUM}/ram:GrandTotalAmount"],
    "iban": [
        f"{_S}/ram:SpecifiedTradeSettlementPaymentMeans/ram:PayeePartyCreditorFinancialAccount/ram:IBANID"
    ],
}
_PARTY = "cac:AccountingSupplierParty/cac:Party"
UBL_PATHS: Dict[str, List[str]] = {
    "invoice_no": ["cbc:ID"],
    "invoice_date": ["cbc:IssueDate"],
    "due_date": ["cbc:DueDate", "cac:PaymentMeans/cbc:PaymentDueDate"],
    "supplier": [f"{_PARTY}/cac:PartyLegalEntity/cbc:RegistrationName", f"{_PARTY}/cac:PartyName/cbc:Name"],
    "supplier_vat_id": [f"{_PARTY}/cac:PartyTaxScheme/cbc:CompanyID"],
    "currency": ["cbc:DocumentCurrencyCode"],
    "net": ["cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount"],
    "vat": ["cac:TaxTotal/cbc:TaxAmount"],
    "gross": ["cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount"],
    "iban": ["cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID"],
}
# Fields read from the XML of an e-invoice, so these columns never need a model.
XML_KEYS = frozenset(CII_PATHS) | frozenset(UBL_PATHS) | {"doc_type", "vat_rate", "description"}

LINE_COLUMNS = ["Description", "Quantity", "Unit Price", "Amount"]
_CII_LINE: Dict[str, List[str]] = {
    "Description": ["ram:SpecifiedTradeProduct/ram:Name"],
    "Quantity": ["ram:SpecifiedLineTradeDelivery/ram:BilledQuantity"],
    "Unit Price": ["ram:SpecifiedLineTradeAgreement/ram:NetPriceProductTradePrice/ram:ChargeAmount"],
    "Amount": ["ram:SpecifiedLineTradeSettlement/ram:SpecifiedTradeSettlementLineMonetarySummation/ram:LineTotalAmount"],
}
_UBL_LINE: Dict[str, List[str]] = {
    "Description": ["cac:Item/cbc:Name"],
    "Quantity": ["cbc:InvoicedQuantity", "cbc:CreditedQuantity"],
    "Unit Price": ["cac:Price/cbc:PriceAmount"],
    "Amount": ["cbc:LineExtensionAmount"],
}


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse(data: bytes) -> ET.Element:
    try:
        return DET.fromstring(data)
    except (ET.ParseError, DefusedXmlException, ValueError) as e:
        raise UnsupportedFileError(f"Cannot read e-invoice XML: {e}") from e


def _syntax(root: ET.Element) -> str:
    """"cii" or "ubl" for a supported root element."""
    kind = _localname(root.tag)
    if kind == "CrossIndustryInvoice":
        return "cii"
    if kind in ("Invoice", "CreditNote"):
        return "ubl"
    raise UnsupportedFileError(f"Not a CII or UBL e-invoice (root element <{kind}>)")


def _first(root: ET.Element, paths: List[str]) -> Optional[ET.Element]:
    for path in paths:
        node = root.find(path, NS)
        if node is not None and (node.text or "").strip():
            return node
    return None


def _texts(root: ET.Element, path: str) -> List[str]:
    return [node.text.strip() for node in root.findall(path, NS) if (node.text or "").strip()]


def _cii_date(node: ET.Element) -> str:
    text = node.text.strip()
    if node.get("format", "102") == "102" and len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _lines(root: ET.Element, syntax: str) -> List[ET.Element]:
    if syntax == "cii":
        return root.findall("rsm:SupplyChainTradeTransaction/ram:IncludedSupplyChainTradeLineItem", NS)
    return root.findall("cac:InvoiceLine", NS) + root.findall("cac:CreditNoteLine", NS)


def _description(root: ET.Element, syntax: str) -> str:
    """The line names joined, cut to MAX_DESCRIPTION characters."""
    paths = _CII_LINE if syntax == "cii" else _UBL_LINE
    names = []
    for line in _lines(root, syntax):
        node = _first(line, paths["Description"])
        if node is not None:
            names.append(node.text.strip())
    text = "; ".join(names)
    return text if len(text) <= MAX_DESCRIPTION else text[:MAX_DESCRIPTION - 1].rstrip() + "…"


def _doc_type(root: ET.Element, syntax: str) -> Optional[str]:
    if syntax == "ubl":
        if _localname(root.tag) == "CreditNote":
            return "credit note"
        node = _first(root, ["cbc:InvoiceTypeCode"])
    else:
        node = _first(root, ["rsm:ExchangedDocument/ram:TypeCode"])
    if node is None:
        return None
    return "credit note" if node.text.strip() == CREDIT_NOTE_CODE else "invoice"


def _vat_rates(root: ET.Element, syntax: str) -> List[str]:
    if syntax == "cii":
        rates = _texts(root, f"{_S}/ram:ApplicableTradeTax/ram:RateApplicablePercent")
    else:
        rates = _texts(root, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:Percent")
    return list(dict.fromkeys(rates))


def parse_xml(data: bytes) -> Dict[str, str]:
    """Standard fields of a CII or UBL invoice, by column key. Missing fields are left out."""
    root = _parse(data)
    syntax = _syntax(root)
    paths = CII_PATHS if syntax == "cii" else UBL_PATHS
    result = {}
    for key, candidates in paths.items():
        node = _first(root, candidates)
        if node is None:
            continue
        if syntax == "cii" and key in ("invoice_date", "due_date"):
            result[key] = _cii_date(node)
        else:
            result[key] = node.text.strip()
    doc_type = _doc_type(root, syntax)
    if doc_type is not None:
        result["doc_type"] = doc_type
    rates = _vat_rates(root, syntax)
    if rates:
        result["vat_rate"] = ", ".join(rates)
    description = _description(root, syntax)
    if description:
        result["description"] = description
    return result


# --- the readable document ----------------------------------------------------------------------

MEANS = {"10": "Cash", "20": "Cheque", "30": "Credit transfer", "48": "Bank card", "49": "Direct debit",
         "57": "Standing agreement", "58": "SEPA credit transfer", "59": "SEPA direct debit",
         "97": "Clearing between partners", "ZZZ": "Mutually defined"}
_DELIVERY = "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeDelivery"
_AGREEMENT = "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement"
_MEANS = f"{_S}/ram:SpecifiedTradeSettlementPaymentMeans"
_TERMS = f"{_S}/ram:SpecifiedTradePaymentTerms"
_CII_DOC: Dict[str, List[str]] = {
    "number": ["rsm:ExchangedDocument/ram:ID"],
    "issue_date": ["rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString"],
    "due_date": [f"{_TERMS}/ram:DueDateDateTime/udt:DateTimeString"],
    "delivery_date": [f"{_DELIVERY}/ram:ActualDeliverySupplyChainEvent/ram:OccurrenceDateTime/udt:DateTimeString"],
    "period_start": [f"{_S}/ram:BillingSpecifiedPeriod/ram:StartDateTime/udt:DateTimeString"],
    "period_end": [f"{_S}/ram:BillingSpecifiedPeriod/ram:EndDateTime/udt:DateTimeString"],
    "currency": [f"{_S}/ram:InvoiceCurrencyCode"],
    "buyer_reference": [f"{_AGREEMENT}/ram:BuyerReference"],
    "order_reference": [f"{_AGREEMENT}/ram:BuyerOrderReferencedDocument/ram:IssuerAssignedID"],
    "totals.lines": [f"{_SUM}/ram:LineTotalAmount"],
    "totals.allowances": [f"{_SUM}/ram:AllowanceTotalAmount"],
    "totals.charges": [f"{_SUM}/ram:ChargeTotalAmount"],
    "totals.net": [f"{_SUM}/ram:TaxBasisTotalAmount"],
    "totals.vat": [f"{_SUM}/ram:TaxTotalAmount"],
    "totals.gross": [f"{_SUM}/ram:GrandTotalAmount"],
    "totals.prepaid": [f"{_SUM}/ram:TotalPrepaidAmount"],
    "totals.due": [f"{_SUM}/ram:DuePayableAmount"],
    "payment.means": [f"{_MEANS}/ram:TypeCode"],
    "payment.iban": [f"{_MEANS}/ram:PayeePartyCreditorFinancialAccount/ram:IBANID"],
    "payment.bic": [f"{_MEANS}/ram:PayeeSpecifiedCreditorFinancialInstitution/ram:BICID"],
    "payment.account_name": [f"{_MEANS}/ram:PayeePartyCreditorFinancialAccount/ram:AccountName"],
    "payment.terms": [f"{_TERMS}/ram:Description"],
    "payment.mandate_reference": [f"{_TERMS}/ram:DirectDebitMandateID"],
}
_UBL_DOC: Dict[str, List[str]] = {
    "number": ["cbc:ID"],
    "issue_date": ["cbc:IssueDate"],
    "due_date": ["cbc:DueDate", "cac:PaymentMeans/cbc:PaymentDueDate"],
    "delivery_date": ["cac:Delivery/cbc:ActualDeliveryDate"],
    "period_start": ["cac:InvoicePeriod/cbc:StartDate"],
    "period_end": ["cac:InvoicePeriod/cbc:EndDate"],
    "currency": ["cbc:DocumentCurrencyCode"],
    "buyer_reference": ["cbc:BuyerReference"],
    "order_reference": ["cac:OrderReference/cbc:ID"],
    "totals.lines": ["cac:LegalMonetaryTotal/cbc:LineExtensionAmount"],
    "totals.allowances": ["cac:LegalMonetaryTotal/cbc:AllowanceTotalAmount"],
    "totals.charges": ["cac:LegalMonetaryTotal/cbc:ChargeTotalAmount"],
    "totals.net": ["cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount"],
    "totals.vat": ["cac:TaxTotal/cbc:TaxAmount"],
    "totals.gross": ["cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount"],
    "totals.prepaid": ["cac:LegalMonetaryTotal/cbc:PrepaidAmount"],
    "totals.due": ["cac:LegalMonetaryTotal/cbc:PayableAmount"],
    "payment.means": ["cac:PaymentMeans/cbc:PaymentMeansCode"],
    "payment.iban": ["cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID"],
    "payment.bic": ["cac:PaymentMeans/cac:PayeeFinancialAccount/cac:FinancialInstitutionBranch/cbc:ID"],
    "payment.account_name": ["cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:Name"],
    "payment.terms": ["cac:PaymentTerms/cbc:Note"],
    "payment.mandate_reference": ["cac:PaymentMeans/cac:PaymentMandate/cbc:ID"],
}
_CII_PARTY: Dict[str, List[str]] = {
    "name": ["ram:Name"],
    "street": ["ram:PostalTradeAddress/ram:LineOne"],
    "postcode": ["ram:PostalTradeAddress/ram:PostcodeCode"],
    "city": ["ram:PostalTradeAddress/ram:CityName"],
    "country": ["ram:PostalTradeAddress/ram:CountryID"],
    "vat_id": ["ram:SpecifiedTaxRegistration/ram:ID[@schemeID='VA']"],
    "tax_number": ["ram:SpecifiedTaxRegistration/ram:ID[@schemeID='FC']"],
    "email": ["ram:URIUniversalCommunication/ram:URIID", "ram:DefinedTradeContact/ram:EmailURIUniversalCommunication/ram:URIID"],
    "phone": ["ram:DefinedTradeContact/ram:TelephoneUniversalCommunication/ram:CompleteNumber"],
    "contact": ["ram:DefinedTradeContact/ram:PersonName"],
}
_UBL_PARTY: Dict[str, List[str]] = {
    "name": ["cac:PartyLegalEntity/cbc:RegistrationName", "cac:PartyName/cbc:Name"],
    "street": ["cac:PostalAddress/cbc:StreetName"],
    "postcode": ["cac:PostalAddress/cbc:PostalZone"],
    "city": ["cac:PostalAddress/cbc:CityName"],
    "country": ["cac:PostalAddress/cac:Country/cbc:IdentificationCode"],
    "email": ["cac:Contact/cbc:ElectronicMail", "cbc:EndpointID[@schemeID='EM']"],
    "phone": ["cac:Contact/cbc:Telephone"],
    "contact": ["cac:Contact/cbc:Name"],
}
_CII_TAX: Dict[str, List[str]] = {
    "category": ["ram:CategoryCode"], "rate": ["ram:RateApplicablePercent"], "basis": ["ram:BasisAmount"],
    "tax": ["ram:CalculatedAmount"],
}
_UBL_TAX: Dict[str, List[str]] = {
    "category": ["cac:TaxCategory/cbc:ID"], "rate": ["cac:TaxCategory/cbc:Percent"], "basis": ["cbc:TaxableAmount"],
    "tax": ["cbc:TaxAmount"],
}
_LINE_KEYS = {"Description": "description", "Quantity": "quantity", "Unit Price": "unit_price", "Amount": "amount"}
_CII_LINE_RATE = ["ram:SpecifiedLineTradeSettlement/ram:ApplicableTradeTax/ram:RateApplicablePercent"]
_UBL_LINE_RATE = ["cac:Item/cac:ClassifiedTaxCategory/cbc:Percent"]


def _text_at(root: ET.Element, paths: List[str], syntax: str, date: bool = False) -> str:
    node = _first(root, paths)
    if node is None:
        return ""
    return _cii_date(node) if date and syntax == "cii" else node.text.strip()


def _fields(root: ET.Element, paths: Dict[str, List[str]], syntax: str) -> Dict[str, str]:
    return {key: _text_at(root, candidates, syntax, date=key.endswith("date") or "period" in key)
            for key, candidates in paths.items()}


_PARTY_KEYS = ("name", "street", "postcode", "city", "country", "vat_id", "tax_number", "email", "phone", "contact")


def _ubl_registrations(node: ET.Element) -> Dict[str, str]:
    """VAT ID and tax number of a UBL party: the registration with scheme VAT, and the one with scheme FC."""
    out = {"vat_id": "", "tax_number": ""}
    for scheme in node.findall("cac:PartyTaxScheme", NS):
        company_id = _text_at(scheme, ["cbc:CompanyID"], "ubl")
        kind = _text_at(scheme, ["cac:TaxScheme/cbc:ID"], "ubl").upper()
        key = "tax_number" if kind == "FC" else "vat_id"
        if company_id and not out[key]:
            out[key] = company_id
    return out


def _party(root: ET.Element, path: str, syntax: str) -> Dict[str, str]:
    node = root.find(path, NS)
    if node is None:
        return {key: "" for key in _PARTY_KEYS}
    party = _fields(node, _CII_PARTY if syntax == "cii" else _UBL_PARTY, syntax)
    if syntax == "ubl":
        party.update(_ubl_registrations(node))
    return {key: party[key] for key in _PARTY_KEYS}


def _document_lines(root: ET.Element, syntax: str) -> List[Dict[str, str]]:
    paths = _CII_LINE if syntax == "cii" else _UBL_LINE
    rate = _CII_LINE_RATE if syntax == "cii" else _UBL_LINE_RATE
    out = []
    for line in _lines(root, syntax):
        row = {_LINE_KEYS[column]: _text_at(line, candidates, syntax) for column, candidates in paths.items()}
        quantity = _first(line, paths["Quantity"])
        row["unit"] = quantity.get("unitCode", "") if quantity is not None else ""
        row["vat_rate"] = _text_at(line, rate, syntax)
        out.append({key: row[key] for key in ("description", "quantity", "unit", "unit_price", "vat_rate", "amount")})
    return out


def _taxes(root: ET.Element, syntax: str) -> List[Dict[str, str]]:
    if syntax == "cii":
        nodes, paths = root.findall(f"{_S}/ram:ApplicableTradeTax", NS), _CII_TAX
    else:
        nodes, paths = root.findall("cac:TaxTotal/cac:TaxSubtotal", NS), _UBL_TAX
    return [_fields(node, paths, syntax) for node in nodes]


def parse_document(data: bytes) -> Dict[str, Any]:
    """A CII or UBL invoice as a readable document: parties, lines, taxes, totals, payment and notes.

    Every value is the text from the XML (dates as YYYY-MM-DD); missing values are empty. Nothing is computed.
    """
    root = _parse(data)
    syntax = _syntax(root)
    flat = _fields(root, _CII_DOC if syntax == "cii" else _UBL_DOC, syntax)
    doc: Dict[str, Any] = {key: value for key, value in flat.items() if "." not in key}
    doc["kind"] = _doc_type(root, syntax) or ""
    if syntax == "cii":
        doc["seller"] = _party(root, f"{_AGREEMENT}/ram:SellerTradeParty", syntax)
        doc["buyer"] = _party(root, f"{_AGREEMENT}/ram:BuyerTradeParty", syntax)
        doc["notes"] = _texts(root, "rsm:ExchangedDocument/ram:IncludedNote/ram:Content")
    else:
        doc["seller"] = _party(root, "cac:AccountingSupplierParty/cac:Party", syntax)
        doc["buyer"] = _party(root, "cac:AccountingCustomerParty/cac:Party", syntax)
        doc["notes"] = _texts(root, "cbc:Note")
    doc["lines"] = _document_lines(root, syntax)
    doc["taxes"] = _taxes(root, syntax)
    doc["totals"] = {key.split(".")[1]: value for key, value in flat.items() if key.startswith("totals.")}
    doc["payment"] = {key.split(".")[1]: value for key, value in flat.items() if key.startswith("payment.")}
    code = doc["payment"]["means"]
    doc["payment"]["means"] = MEANS.get(code, code)
    return doc


def parse_line_items(data: bytes) -> List[Dict[str, str]]:
    """The invoice lines of a CII or UBL invoice, one dict per line over LINE_COLUMNS."""
    root = _parse(data)
    syntax = _syntax(root)
    paths = _CII_LINE if syntax == "cii" else _UBL_LINE
    rows = []
    for line in _lines(root, syntax):
        row = {}
        for column, candidates in paths.items():
            node = _first(line, candidates)
            row[column] = node.text.strip() if node is not None else ""
        rows.append(row)
    return rows


def xml_text(data: bytes) -> str:
    """The XML without namespaces and indented, for reading by a language model."""
    root = _parse(data)
    for node in root.iter():
        node.tag = _localname(node.tag)
        node.attrib = {_localname(k): v for k, v in node.attrib.items()}
    ET.indent(root)
    return ET.tostring(root, encoding="unicode")
