"""Decide how a file is read: e-invoice XML, ZUGFeRD PDF, digital PDF or scan."""

from pathlib import Path
from typing import Optional

from .config import Settings
from .errors import LimitError, UnsupportedFileError
from .pdf import PathLike, _is_pdf, _open_pdf, serialized, text_pages

SUFFIXES = {".pdf", ".xml", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
EMBEDDED_NAMES = {"factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml", "zugferd.xml"}
MIN_CHARS_PER_PAGE = 30


@serialized
def embedded_xml(path: PathLike) -> Optional[bytes]:
    """The e-invoice XML attached to a ZUGFeRD/Factur-X PDF, or None."""
    if not _is_pdf(path):
        return None
    doc = _open_pdf(path)
    try:
        for i in range(doc.count_attachments()):
            attachment = doc.get_attachment(i)
            if attachment.get_name().lower() in EMBEDDED_NAMES:
                return bytes(attachment.get_data())
        return None
    finally:
        doc.close()


def detect(path: PathLike, settings: Settings) -> str:
    """Return "xml", "zugferd", "pdf" or "scan" for a supported invoice file."""
    path = Path(path)
    if not path.is_file():
        raise UnsupportedFileError(f"File not found: {path}")
    if path.suffix.lower() not in SUFFIXES:
        raise UnsupportedFileError(
            f"Unsupported file type {path.suffix or '(none)'}: {path.name}. "
            f"Supported: {', '.join(sorted(SUFFIXES))}"
        )
    size = path.stat().st_size
    if size == 0:
        raise UnsupportedFileError(f"File is empty: {path.name}")
    if size > settings.max_file_mb * 1024 * 1024:
        raise LimitError(f"{path.name} is larger than {settings.max_file_mb} MB (max_file_mb)")
    if path.suffix.lower() == ".xml":
        return "xml"
    if not _is_pdf(path):
        return "scan"
    if embedded_xml(path) is not None:
        return "zugferd"
    pages = text_pages(path)
    chars = sum(len("".join(text.split())) for text in pages)
    if pages and chars / len(pages) >= MIN_CHARS_PER_PAGE:
        return "pdf"
    return "scan"
