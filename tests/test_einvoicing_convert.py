import json

import pytest
from pdfgen import write_samples

from invoice_extract.einvoice import parse_xml
from invoice_extract.einvoicing import convert_many, write
from invoice_extract.einvoicing.profile import profile_loads
from invoice_extract.errors import ConfigError

VALUES = {
    "invoice_no": "R-2026-001", "doc_type": "invoice", "invoice_date": "2026-10-21", "currency": "EUR",
    "supplier": "ACME GmbH", "seller_street": "Main Street 1", "seller_postcode": "12345", "seller_city": "Berlin",
    "seller_country": "DE", "seller_vat_id": "DE123456789", "seller_contact": "Jane Doe",
    "seller_phone": "+49 30 123456", "seller_email": "billing@acme.example", "buyer_name": "Beta AG",
    "buyer_street": "Side Road 2", "buyer_postcode": "54321", "buyer_city": "Hamburg", "buyer_country": "DE",
    "buyer_email": "ap@beta.example", "buyer_reference": "PO-77", "due_date": "2026-11-20",
    "payment_terms": "Payable within 30 days.", "payment_means": "transfer", "iban": "DE89370400440532013000",
    "net": "100.00", "vat": "19.00", "gross": "119.00",
}


def reply(**changes):
    values = {**VALUES, **changes}
    answer = {k: ({"value": v, "source_text": v, "page": 1} if v is not None else None) for k, v in values.items()}
    answer["lines"] = {"rows": [["Consulting", "2", "h", "50.00", "19", "100.00"]], "page": 1}
    return lambda body: json.dumps(answer)


def llm(server):
    return {"base_url": server.base_url, "model": "m", "max_retries": 0}


def test_convert_and_write_xrechnung(tmp_path, server):
    pdf = write_samples(tmp_path)[0]
    server.reply = reply()
    conv = convert_many([pdf], **llm(server))[0]
    assert conv.error is None and conv.issues("xrechnung") == []
    path = write(conv, "xrechnung", tmp_path)
    assert path == tmp_path / "digital.xml"
    assert parse_xml(path.read_bytes())["gross"] == "119.00"
    zugferd = write(conv, "zugferd", tmp_path)
    assert zugferd.name == "digital-zugferd.pdf" and zugferd.read_bytes().startswith(b"%PDF")


def test_profile_replaces_seller(tmp_path, server):
    pdf = write_samples(tmp_path)[0]
    server.reply = reply()
    profile = profile_loads("seller:\n  name: My Shop GmbH\n")
    conv = convert_many([pdf], profile=profile, **llm(server))[0]
    assert conv.invoice.seller.name == "My Shop GmbH"


def test_einvoice_input_is_reported_without_requests(tmp_path, server):
    xml = write_samples(tmp_path)[2]
    conv = convert_many([xml], **llm(server))[0]
    assert conv.error == "Already an e-invoice" and server.requests == []


def test_write_refuses_invalid(tmp_path, server):
    pdf = write_samples(tmp_path)[0]
    server.reply = reply(buyer_email=None)
    conv = convert_many([pdf], **llm(server))[0]
    assert [i.field for i in conv.issues("xrechnung") if i.level == "error"] == ["buyer.email"]
    with pytest.raises(ConfigError, match="buyer.email"):
        write(conv, "xrechnung", tmp_path)
    assert write(conv, "zugferd", tmp_path).exists()
