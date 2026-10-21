from einvoice_fixtures import valid_invoice
from pdfgen import write_samples

from invoice_extract.config import Settings
from invoice_extract.detect import detect, embedded_xml
from invoice_extract.einvoice import parse_xml
from invoice_extract.einvoicing.cii import build_cii
from invoice_extract.einvoicing.zugferd import write_zugferd


def roundtrip(tmp_path, source):
    out = tmp_path / "out.pdf"
    out.write_bytes(write_zugferd(source, build_cii(valid_invoice(), "zugferd")))
    assert detect(out, Settings()) == "zugferd"
    return parse_xml(embedded_xml(out))


def test_zugferd_from_pdf(tmp_path):
    assert roundtrip(tmp_path, write_samples(tmp_path)[0])["invoice_no"] == "R-2026-001"


def test_zugferd_from_scan(tmp_path):  # Review Focus 4
    assert roundtrip(tmp_path, write_samples(tmp_path)[3])["gross"] == "119.00"
