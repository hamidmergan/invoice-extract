"""Turn normal invoices into e-invoices: XRechnung (CII XML) and ZUGFeRD (PDF with embedded CII)."""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Union

import openai

from ..config import resolve
from ..core import extract_many
from ..detect import detect
from ..errors import ConfigError
from ..results import Row
from .cii import build_cii
from .fields import EINVOICE_FIELDS, from_row
from .model import EInvoice
from .profile import Profile, apply_profile
from .rules import FORMATS, Issue, check
from .zugferd import write_zugferd

PathLike = Union[str, Path]
ALREADY = "Already an e-invoice"


@dataclass
class Conversion:
    path: str
    row: Row
    invoice: Optional[EInvoice] = None
    error: Optional[str] = None

    def issues(self, format: str) -> List[Issue]:
        return [] if self.invoice is None else check(self.invoice, format)


def convert_many(paths: Sequence[PathLike], *, profile: Optional[Profile] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None, api_key: Optional[str] = None, vision_model: Optional[str] = None,
                 client: Optional[openai.OpenAI] = None, **limits) -> List[Conversion]:
    """Read every field an e-invoice needs from each invoice. Existing e-invoices are reported, not read."""
    settings = resolve(base_url=base_url, model=model, vision_model=vision_model, api_key=api_key, **limits)
    paths = list(paths)
    readable = []
    for path in paths:
        try:
            if detect(path, settings) in ("xml", "zugferd"):
                continue
        except Exception:
            pass  # extract_many reports the problem for this file
        readable.append(path)
    rows = iter(extract_many(readable, EINVOICE_FIELDS, base_url=base_url, model=model, api_key=api_key,
                             vision_model=vision_model, client=client, **limits)) if readable else iter(())
    out = []
    for path in paths:
        if path not in readable:
            out.append(Conversion(str(path), Row(path=str(path)), error=ALREADY))
            continue
        row = next(rows)
        if row.error:
            out.append(Conversion(str(path), row, error=row.error))
            continue
        invoice = from_row(row)
        out.append(Conversion(str(path), row, apply_profile(invoice, profile) if profile else invoice))
    return out


def output_name(source: PathLike, format: str) -> str:
    stem = Path(source).stem
    return f"{stem}.xml" if format == "xrechnung" else f"{stem}-zugferd.pdf"


def write(conversion: Conversion, format: str, out_dir: PathLike) -> Path:
    """Write the e-invoice next to the others in `out_dir`. Raises ConfigError if it is not valid."""
    if format not in FORMATS:
        raise ConfigError(f"Unknown e-invoice format {format!r}; use one of: {', '.join(FORMATS)}")
    if conversion.invoice is None:
        raise ConfigError(conversion.error or "Nothing was read from this file.")
    xml = build_cii(conversion.invoice, format)
    path = Path(out_dir) / output_name(conversion.path, format)
    path.write_bytes(xml if format == "xrechnung" else write_zugferd(conversion.path, xml))
    return path


__all__ = ["ALREADY", "EINVOICE_FIELDS", "FORMATS", "Conversion", "EInvoice", "Issue", "Profile", "build_cii",
           "check", "convert_many", "output_name", "write", "write_zugferd"]
