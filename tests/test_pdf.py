import pytest
from PIL import Image

from invoice_extract.pdf import click_hint, locate, page_count, render_page, segments, text_pages

from pdfgen import make_pdf


@pytest.fixture
def pdf_path(tmp_path):
    data = make_pdf([[
        (50, 780, "ACME GmbH"),
        (50, 700, "Kd.-Nr."),
        (120, 700, "40711"),
        (50, 650, "Total 1.234,50 EUR"),
    ]])
    path = tmp_path / "invoice.pdf"
    path.write_bytes(data)
    return path


def test_text_pages_contains_text(pdf_path):
    assert "40711" in text_pages(pdf_path)[0]


def test_locate_box_position(pdf_path):
    box = locate(pdf_path, "40711")
    assert box is not None
    assert box.page == 1
    assert 0.19 < box.x0 < 0.22
    assert abs((box.y0 + box.y1) / 2 - (1 - 700 / 842)) < 0.02


def test_locate_whitespace_insensitive(pdf_path):
    assert locate(pdf_path, "Total  1.234,50\nEUR") is not None


def test_locate_missing_returns_none(pdf_path):
    assert locate(pdf_path, "99999") is None


def test_click_hint_finds_left_label(pdf_path):
    segs = segments(pdf_path, 1)
    index = next(i for i, s in enumerate(segs) if s.text.strip() == "40711")
    hint = click_hint(segs, index)
    assert "Kd.-Nr." in hint
    assert "40711" in hint


def test_render_page_png(pdf_path):
    assert render_page(pdf_path, 1).startswith(b"\x89PNG")


def test_image_has_no_text(tmp_path):
    path = tmp_path / "scan.png"
    Image.new("RGB", (100, 100), "white").save(path)
    assert text_pages(path) == []
    assert page_count(path) == 1
