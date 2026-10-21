"""Turn one item of a model answer into a Cell: normalized value, source text and box."""

from pathlib import Path
from typing import Union

from .normalize import display_variants, normalize
from .pdf import locate, locate_span
from .results import Cell, Check

PathLike = Union[str, Path]


def _empty(item: dict) -> bool:
    if item["kind"] == "text":
        return not item["text"].strip()
    if item["kind"] == "table":
        return not item["rows"]
    return item["value"] in (None, "")


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
            cell.box = locate(path, raw, item["page"])
            if cell.box is None and cell.value is not None:
                # tables print the label apart from the value; then look for the value as printed
                for variant in display_variants(type, cell.value):
                    cell.box = locate(path, variant, item["page"])
                    if cell.box is not None:
                        break
        else:
            cell.box = locate_span(path, raw, item["end_text"], item["page"])
        if cell.box is None:
            cell.checks.append(Check("source_not_located", "warning", "Source text not found in the PDF"))
        else:
            cell.page = cell.box.page
    return cell
