"""Hand-written minimal PDFs for tests. No dependency except for attachments."""

import io
from typing import List, Tuple


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: List[List[Tuple[float, float, str]]], size=(595, 842)) -> bytes:
    """Build a PDF with Helvetica text items `(x, y, text)` in points from the bottom-left."""
    w, h = size
    n = len(pages)
    # Object numbers: 1 catalog, 2 pages, 3 font, then (page, content) pairs.
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    kids = []
    for i, items in enumerate(pages):
        page_no, content_no = 4 + 2 * i, 5 + 2 * i
        kids.append(f"{page_no} 0 R")
        stream = "".join(
            f"BT /F1 10 Tf {x} {y} Td ({_escape(text)}) Tj ET\n" for x, y, text in items
        ).encode("latin-1")
        objects[page_no] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_no} 0 R >>"
        ).encode("ascii")
        objects[content_no] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii") + stream + b"endstream"
        )
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {n} >>".encode("ascii")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = out.tell()
        out.write(f"{num} 0 obj\n".encode("ascii") + objects[num] + b"\nendobj\n")
    xref = out.tell()
    count = max(objects) + 1
    out.write(f"xref\n0 {count}\n0000000000 65535 f \n".encode("ascii"))
    for num in range(1, count):
        out.write(f"{offsets[num]:010d} 00000 n \n".encode("ascii"))
    out.write(f"trailer\n<< /Size {count} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return out.getvalue()


def add_attachment(pdf_bytes: bytes, name: str, data: bytes) -> bytes:
    """Embed `data` as a file attachment called `name`."""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        doc.new_attachment(name).set_data(data)
        out = io.BytesIO()
        doc.save(out)
        return out.getvalue()
    finally:
        doc.close()


SAMPLE = {
    "invoice_no": "RE-2026-001",
    "invoice_date": "2026-10-21",
    "due_date": "2026-11-20",
    "supplier": "ACME GmbH",
    "currency": "EUR",
    "net": "100.00",
    "vat": "19.00",
    "gross": "119.00",
    "iban": "DE89370400440532013000",
}


def cii_xml(**values) -> bytes:
    """A minimal ZUGFeRD/Factur-X (CII) invoice with the standard fields."""
    v = {**SAMPLE, **values}
    issue = v["invoice_date"].replace("-", "")
    due = v["due_date"].replace("-", "")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
    xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
    xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
    xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocument>
    <ram:ID>{v["invoice_no"]}</ram:ID>
    <ram:IssueDateTime><udt:DateTimeString format="102">{issue}</udt:DateTimeString></ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:SpecifiedTradeProduct><ram:Name>Paper</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeAgreement>
        <ram:NetPriceProductTradePrice><ram:ChargeAmount>10.00</ram:ChargeAmount></ram:NetPriceProductTradePrice>
      </ram:SpecifiedLineTradeAgreement>
      <ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="C62">10</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>
      <ram:SpecifiedLineTradeSettlement>
        <ram:SpecifiedTradeSettlementLineMonetarySummation>
          <ram:LineTotalAmount>100.00</ram:LineTotalAmount>
        </ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:SellerTradeParty><ram:Name>{v["supplier"]}</ram:Name></ram:SellerTradeParty>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>{v["currency"]}</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradeSettlementPaymentMeans>
        <ram:PayeePartyCreditorFinancialAccount><ram:IBANID>{v["iban"]}</ram:IBANID></ram:PayeePartyCreditorFinancialAccount>
      </ram:SpecifiedTradeSettlementPaymentMeans>
      <ram:SpecifiedTradePaymentTerms>
        <ram:DueDateDateTime><udt:DateTimeString format="102">{due}</udt:DateTimeString></ram:DueDateDateTime>
      </ram:SpecifiedTradePaymentTerms>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:TaxBasisTotalAmount>{v["net"]}</ram:TaxBasisTotalAmount>
        <ram:TaxTotalAmount currencyID="{v["currency"]}">{v["vat"]}</ram:TaxTotalAmount>
        <ram:GrandTotalAmount>{v["gross"]}</ram:GrandTotalAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
""".encode("utf-8")


def ubl_xml(party_name_only: bool = False, **values) -> bytes:
    """A minimal XRechnung (UBL) invoice. `party_name_only` omits the legal name."""
    v = {**SAMPLE, **values}
    legal = (
        ""
        if party_name_only
        else f"<cac:PartyLegalEntity><cbc:RegistrationName>{v['supplier']}</cbc:RegistrationName></cac:PartyLegalEntity>"
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
    xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:ID>{v["invoice_no"]}</cbc:ID>
  <cbc:IssueDate>{v["invoice_date"]}</cbc:IssueDate>
  <cbc:DueDate>{v["due_date"]}</cbc:DueDate>
  <cbc:DocumentCurrencyCode>{v["currency"]}</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty>
    <cac:Party>
      <cac:PartyName><cbc:Name>{v["supplier"]}</cbc:Name></cac:PartyName>
      {legal}
    </cac:Party>
  </cac:AccountingSupplierParty>
  <cac:PaymentMeans>
    <cac:PayeeFinancialAccount><cbc:ID>{v["iban"]}</cbc:ID></cac:PayeeFinancialAccount>
  </cac:PaymentMeans>
  <cac:TaxTotal><cbc:TaxAmount currencyID="{v["currency"]}">{v["vat"]}</cbc:TaxAmount></cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:TaxExclusiveAmount currencyID="{v["currency"]}">{v["net"]}</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="{v["currency"]}">{v["gross"]}</cbc:TaxInclusiveAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="C62">10</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="{v["currency"]}">100.00</cbc:LineExtensionAmount>
    <cac:Item><cbc:Name>Paper</cbc:Name></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="{v["currency"]}">10.00</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>
</Invoice>
""".encode("utf-8")


def _invoice_lines(v) -> List[Tuple[float, float, str]]:
    return [
        (50, 780, v["supplier"]),
        (50, 740, f"Invoice {v['invoice_no']}"),
        (50, 720, f"Date {v['invoice_date']}"),
        (50, 700, f"Net {v['net']} {v['currency']}"),
        (50, 680, f"VAT {v['vat']} {v['currency']}"),
        (50, 660, f"Total {v['gross']} {v['currency']}"),
        (50, 640, f"IBAN {v['iban']}"),
    ]


def write_samples(folder) -> List["Path"]:
    """Write a digital PDF, a ZUGFeRD PDF, an XRechnung XML and a PNG scan into `folder`."""
    from pathlib import Path

    from PIL import Image, ImageDraw

    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    pdf = make_pdf([_invoice_lines(SAMPLE)])
    paths = [folder / "digital.pdf", folder / "zugferd.pdf", folder / "xrechnung.xml", folder / "scan.png"]
    paths[0].write_bytes(pdf)
    paths[1].write_bytes(add_attachment(pdf, "factur-x.xml", cii_xml()))
    paths[2].write_bytes(ubl_xml())
    img = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(img)
    for i, (_, _, text) in enumerate(_invoice_lines(SAMPLE)):
        draw.text((40, 40 + 30 * i), text, fill="black")
    img.save(paths[3])
    return paths
