"""Write a ZUGFeRD / Factur-X PDF: the invoice as a PDF with its CII XML embedded as factur-x.xml."""

import io
from pathlib import Path
from typing import Union

from drafthorse.pdf import attach_xml
from PIL import Image, ImageSequence

from ..pdf import _is_pdf

PathLike = Union[str, Path]


def _image_pdf(path: PathLike) -> bytes:
    """A scan or photo as a PDF, one page per image frame."""
    with Image.open(path) as img:
        frames = [frame.convert("RGB") for frame in ImageSequence.Iterator(img)]
    out = io.BytesIO()
    frames[0].save(out, format="PDF", save_all=True, append_images=frames[1:], resolution=150)
    return out.getvalue()


def write_zugferd(source: PathLike, xml: bytes) -> bytes:
    """The source invoice (PDF, scan or photo) as a PDF with `xml` embedded, profile EN 16931."""
    pdf = Path(source).read_bytes() if _is_pdf(source) else _image_pdf(source)
    return attach_xml(pdf, xml, level="EN 16931")
