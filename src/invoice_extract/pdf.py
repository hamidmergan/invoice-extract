"""Read text, find values and render pages of PDFs and images."""

import io
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageSequence

from .errors import UnsupportedFileError
from .results import Box

PathLike = Union[str, Path]


@dataclass
class Segment:
    """One text run on a page."""

    text: str
    box: Box


def _is_pdf(path: PathLike) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(1024).lstrip().startswith(b"%PDF")
    except OSError as e:
        raise UnsupportedFileError(f"Cannot read {path}: {e}") from e


def _open_pdf(path: PathLike) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as e:
        raise UnsupportedFileError(f"Cannot open PDF {path}: {e}") from e


def _open_image(path: PathLike) -> Image.Image:
    try:
        return Image.open(path)
    except (OSError, Image.UnidentifiedImageError) as e:
        raise UnsupportedFileError(f"{path} is neither a PDF nor a readable image.") from e


def _box(page_no: int, rect: Tuple[float, float, float, float], w: float, h: float) -> Box:
    left, bottom, right, top = rect
    return Box(page=page_no, x0=left / w, y0=1 - top / h, x1=right / w, y1=1 - bottom / h)


def _check_page(page: int, count: int) -> None:
    if not 1 <= page <= count:
        raise ValueError(f"Page {page} does not exist; the file has {count} page(s).")


def page_count(path: PathLike) -> int:
    if _is_pdf(path):
        doc = _open_pdf(path)
        try:
            return len(doc)
        finally:
            doc.close()
    with _open_image(path) as img:
        return getattr(img, "n_frames", 1)


def render_page(path: PathLike, page: int, scale: float = 1.5) -> bytes:
    """Render a 1-based page as PNG."""
    out = io.BytesIO()
    if _is_pdf(path):
        doc = _open_pdf(path)
        try:
            _check_page(page, len(doc))
            image = doc[page - 1].render(scale=scale).to_pil()
        finally:
            doc.close()
        image.save(out, format="PNG")
    else:
        with _open_image(path) as img:
            _check_page(page, getattr(img, "n_frames", 1))
            frame = ImageSequence.Iterator(img)[page - 1]
            image = frame.convert("RGBA" if "A" in frame.getbands() else "RGB")
        image.save(out, format="PNG")
    return out.getvalue()


def text_pages(path: PathLike) -> List[str]:
    """The text layer of each page. Images have none."""
    if not _is_pdf(path):
        return []
    doc = _open_pdf(path)
    try:
        texts = []
        for page in doc:
            textpage = page.get_textpage()
            texts.append(textpage.get_text_range())
            textpage.close()
        return texts
    finally:
        doc.close()


def segments(path: PathLike, page: int) -> List[Segment]:
    """Text runs of a 1-based page with normalized boxes."""
    if not _is_pdf(path):
        return []
    doc = _open_pdf(path)
    try:
        _check_page(page, len(doc))
        pdf_page = doc[page - 1]
        w, h = pdf_page.get_size()
        textpage = pdf_page.get_textpage()
        result = []
        for i in range(textpage.count_rects()):
            rect = textpage.get_rect(i)
            text = textpage.get_text_bounded(*rect)
            if text.strip():
                result.append(Segment(text=text, box=_box(page, rect, w, h)))
        textpage.close()
        return result
    finally:
        doc.close()


def _locate_on_page(pdf_page, page_no: int, needle: str) -> Optional[Box]:
    w, h = pdf_page.get_size()
    textpage = pdf_page.get_textpage()
    try:
        chars, indices = [], []
        for i in range(textpage.count_chars()):
            ch = chr(pdfium_c.FPDFText_GetUnicode(textpage.raw, i))
            if not ch.isspace():
                chars.append(ch)
                indices.append(i)
        start = "".join(chars).find(needle)
        if start < 0:
            return None
        boxes = [textpage.get_charbox(i) for i in indices[start:start + len(needle)]]
        rect = (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )
        return _box(page_no, rect, w, h)
    finally:
        textpage.close()


def locate(path: PathLike, source_text: str, page: Optional[int] = None) -> Optional[Box]:
    """Find `source_text` ignoring whitespace. Searches `page` first, then all pages."""
    needle = "".join(source_text.split())
    if not needle or not _is_pdf(path):
        return None
    doc = _open_pdf(path)
    try:
        order = list(range(1, len(doc) + 1))
        if page in order:
            order.remove(page)
            order.insert(0, page)
        for page_no in order:
            box = _locate_on_page(doc[page_no - 1], page_no, needle)
            if box is not None:
                return box
        return None
    finally:
        doc.close()


def click_hint(segs: List[Segment], index: int) -> str:
    """Describe a clicked value by its label: left on the same line, else directly above."""
    value = segs[index]
    vb = value.box
    line_h = vb.y1 - vb.y0
    centre = (vb.y0 + vb.y1) / 2

    left = [
        s for i, s in enumerate(segs)
        if i != index and s.box.page == vb.page and s.box.x1 <= vb.x0
        and abs((s.box.y0 + s.box.y1) / 2 - centre) <= line_h / 2
    ]
    above = [
        s for i, s in enumerate(segs)
        if i != index and s.box.page == vb.page and s.box.y1 <= vb.y0
        and s.box.x0 < vb.x1 and s.box.x1 > vb.x0
    ]
    label = None
    if left:
        label = max(left, key=lambda s: s.box.x1)
    elif above:
        label = max(above, key=lambda s: s.box.y1)

    example = f'Example value: "{value.text.strip()}".'
    if label is None:
        return example
    return f'Label near the value: "{label.text.strip()}". {example}'
