"""Read the standard fields of ZUGFeRD/Factur-X (CII) and XRechnung (UBL) invoices."""

import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

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
