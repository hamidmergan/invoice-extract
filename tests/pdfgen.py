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
