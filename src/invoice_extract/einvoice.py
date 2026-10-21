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

_S = "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement"
_SUM = f"{_S}/ram:SpecifiedTradeSettlementHeaderMonetarySummation"
CII_PATHS: Dict[str, List[str]] = {
    "invoice_no": ["rsm:ExchangedDocument/ram:ID"],
    "invoice_date": ["rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString"],
    "due_date": [f"{_S}/ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString"],
    "supplier": [
        "rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement/ram:SellerTradeParty/ram:Name"
    ],
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
    "currency": ["cbc:DocumentCurrencyCode"],
    "net": ["cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount"],
    "vat": ["cac:TaxTotal/cbc:TaxAmount"],
    "gross": ["cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount"],
    "iban": ["cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID"],
}


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse(data: bytes) -> ET.Element:
    try:
        return DET.fromstring(data)
    except (ET.ParseError, DefusedXmlException, ValueError) as e:
        raise UnsupportedFileError(f"Cannot read e-invoice XML: {e}") from e


def _first(root: ET.Element, paths: List[str]) -> Optional[ET.Element]:
    for path in paths:
        node = root.find(path, NS)
        if node is not None and (node.text or "").strip():
            return node
    return None


def _cii_date(node: ET.Element) -> str:
    text = node.text.strip()
    if node.get("format", "102") == "102" and len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def parse_xml(data: bytes) -> Dict[str, str]:
    """Standard fields of a CII or UBL invoice. Missing fields are left out."""
    root = _parse(data)
    kind = _localname(root.tag)
    if kind == "CrossIndustryInvoice":
        paths = CII_PATHS
    elif kind in ("Invoice", "CreditNote"):
        paths = UBL_PATHS
    else:
        raise UnsupportedFileError(f"Not a CII or UBL e-invoice (root element <{kind}>)")
    result = {}
    for key, candidates in paths.items():
        node = _first(root, candidates)
        if node is None:
            continue
        if paths is CII_PATHS and key in ("invoice_date", "due_date"):
            result[key] = _cii_date(node)
        else:
            result[key] = node.text.strip()
    return result


def xml_text(data: bytes) -> str:
    """The XML without namespaces and indented, for reading by a language model."""
    root = _parse(data)
    for node in root.iter():
        node.tag = _localname(node.tag)
        node.attrib = {_localname(k): v for k, v in node.attrib.items()}
    ET.indent(root)
    return ET.tostring(root, encoding="unicode")
