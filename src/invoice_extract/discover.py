"""Discovery: the model lists everything on an invoice; fields are then merged across invoices."""

from collections import Counter
from pathlib import Path
from typing import Dict, List, Union

from .cells import cell_from_item
from .checks import run_checks
from .columns import make_key, role_column
from .config import Settings
from .core import ClientGetter, _pdf_text, _scan_boxes, _transcribe
from .detect import detect, embedded_xml
from .einvoice import LINE_COLUMNS, parse_line_items, parse_xml, xml_text
from .llm import discover_items
from .normalize import display_variants, normalize
from .pdf import locate
from .results import Cell, Check, Row

PathLike = Union[str, Path]
SOURCE = {"xml": "xml", "zugferd": "xml", "pdf": "text", "scan": "vision"}


def _xml_cells(path: PathLike, kind: str, data: bytes) -> Dict[str, Cell]:
    """The fields the XML mapping knows, keyed by role, plus the line items as a table."""
    cells = {}
    for role, raw in parse_xml(data).items():
        col = role_column(role)
        value, checks = normalize(col.type, raw, raw)
        cell = Cell(value=value, raw=raw, source="xml", checks=checks, kind="value", label=col.label, role=role,
                    type=col.type)
        if kind == "zugferd":
            for variant in display_variants(col.type, value):
                cell.box = locate(path, variant)
                if cell.box is not None:
                    cell.page = cell.box.page
                    break
        cells[role] = cell
    lines = parse_line_items(data)
    if lines:
        cells["line_items"] = Cell(value=lines, source="xml", kind="table", label="Line Items",
                                   columns=list(LINE_COLUMNS))
    return cells


def _keyed(found: List[Cell], cells: Dict[str, Cell], row: Row) -> None:
    """Add discovered cells to `cells`: by role where the role is unique, else by label. Existing keys win."""
    counts = Counter(c.role for c in found if c.role)
    for role, n in counts.items():
        if n > 1:
            label = role_column(role).label
            row.checks.append(Check("role_conflict", "warning",
                                    f"Several values look like {label}; checks that need it were skipped"))
    for cell in found:
        if cell.role and counts[cell.role] == 1:
            if cell.role in cells:
                continue  # the e-invoice XML already has this value
            cell.label = role_column(cell.role).label
            cells[cell.role] = cell
        else:
            cell.role = ""
            cells[make_key(cell.label, cells)] = cell


def discover_file(path: PathLike, settings: Settings, client: ClientGetter) -> Row:
    """Read one invoice and keep everything found, keyed by role or label."""
    kind = detect(path, settings)
    row = Row(path=str(path), kind=kind)
    cells: Dict[str, Cell] = {}
    if kind in ("xml", "zugferd"):
        data = Path(path).read_bytes() if kind == "xml" else embedded_xml(path)
        cells.update(_xml_cells(path, kind, data))
        if not (settings.base_url and settings.model):
            row.cells = cells
            row.checks = run_checks(row, [])
            return row
        text = xml_text(data)
        if kind == "zugferd":
            text += "\n\n" + _pdf_text(path)
    elif kind == "pdf":
        settings.require_llm()
        text = _pdf_text(path)
    else:
        settings.require_llm()
        text = _transcribe(path, settings)
    found = [cell_from_item(path, item, item["type"], SOURCE[kind], kind in ("pdf", "zugferd"))
             for item in discover_items(client(), settings.model, text)]
    found = [c for c in found if c.value not in (None, "", [])]
    _keyed(found, cells, row)
    if kind == "scan":
        _scan_boxes(path, cells, settings, client)
    row.cells = cells
    row.checks = row.checks + run_checks(row, [])
    return row
