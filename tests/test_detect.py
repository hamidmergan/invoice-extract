import pytest

from invoice_extract.config import Settings
from invoice_extract.detect import detect, embedded_xml
from invoice_extract.errors import LimitError, UnsupportedFileError

from pdfgen import cii_xml, make_pdf, write_samples


@pytest.fixture
def samples(tmp_path):
    return write_samples(tmp_path / "samples")


def test_detect_kinds(samples, tmp_path):
    settings = Settings()
    digital, zugferd, xrechnung, scan = samples
    assert detect(digital, settings) == "pdf"
    assert detect(zugferd, settings) == "zugferd"
    assert detect(xrechnung, settings) == "xml"
    assert detect(scan, settings) == "scan"
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(make_pdf([[]]))
    assert detect(empty, settings) == "scan"


def test_embedded_xml(samples):
    digital, zugferd, _, _ = samples
    assert embedded_xml(zugferd) == cii_xml()
    assert embedded_xml(digital) is None


def test_detect_zero_byte_and_wrong_suffix(tmp_path):
    zero = tmp_path / "zero.pdf"
    zero.write_bytes(b"")
    with pytest.raises(UnsupportedFileError):
        detect(zero, Settings())
    doc = tmp_path / "invoice.docx"
    doc.write_bytes(b"not supported")
    with pytest.raises(UnsupportedFileError):
        detect(doc, Settings())
    with pytest.raises(UnsupportedFileError):
        detect(tmp_path / "missing.pdf", Settings())


def test_detect_too_large(samples):
    with pytest.raises(LimitError):
        detect(samples[0], Settings(max_file_mb=0.000001))


def test_detect_broken_pdf(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 garbage")
    with pytest.raises(UnsupportedFileError):
        detect(broken, Settings())
