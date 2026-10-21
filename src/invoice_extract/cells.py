"""Turn one item of a model answer into a Cell: normalized value, source text and box."""

from pathlib import Path
from typing import List, Optional, Union

from .normalize import display_variants, normalize
from .pdf import locate, locate_span
from .results import Box, Cell, Check

PathLike = Union[str, Path]


def _empty(item: dict) -> bool:
    if item["kind"] == "text":
        return not item["text"].strip()
    if item["kind"] == "table":
        return not item["rows"]
    return item["value"] in (None, "")


def _words(text: str, first: bool, n: int = 6) -> str:
    words = text.split()
    return " ".join(words[:n] if first else words[-n:])


def _value_box(path: PathLike, raw: str, variants: List[str], page: Optional[int]) -> Optional[Box]:
    """The value as printed within its source text; else the value anywhere; else the source text itself.

    Models often name a nearby header or a whole row as source, so the value itself decides where the box goes.
    """
    variants = [v for v in variants if v.strip()]
    for find in (lambda v: locate(path, v, page, inside=raw), lambda v: locate(path, v, page)):
        for variant in variants:
            box = find(variant)
            if box is not None:
                return box
    return locate(path, raw, page)


def cell_from_item(path: PathLike, item: dict, type: str, source: str, locate_boxes: bool) -> Cell:
    """One cell for a value, text block or table. `type` is the column type used for values."""
    kind = item["kind"]
    cell = Cell(source=source, kind=kind, label=item["label"], role=item["role"],
                type=type if kind == "value" else "text", columns=list(item["columns"]))
    if _empty(item):
        return cell
    raw = item["source_text"]
    if kind == "value":
        cell.value, cell.checks = normalize(type, item["value"], raw)
    else:
        cell.value = item["text"] if kind == "text" else item["rows"]
    cell.raw = raw
    cell.page = item["page"]
    if locate_boxes and raw:
        if kind == "value":
            cell.box = _value_box(path, raw, display_variants(type, cell.value), item["page"])
        else:
            # long blocks are often copied with a slip; their first and last words are enough to find them
            end = item["end_text"] or (item["text"] if kind == "text" else "")
            cell.box = locate_span(path, _words(raw, first=True), _words(end, first=False), item["page"])
        if cell.box is None:
            cell.checks.append(Check("source_not_located", "warning", "Source text not found in the PDF"))
        else:
            cell.page = cell.box.page
    return cell
