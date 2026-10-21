"""Discovery: the model lists everything on an invoice; fields are then merged across invoices."""

from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from .cells import cell_from_item
from .checks import run_checks
from .columns import RESERVED, ROLES, Column, make_key, role_column
from .config import Settings
from .core import ClientGetter, _pdf_text, _scan_boxes, _transcribe
from .detect import detect, embedded_xml
from .einvoice import LINE_COLUMNS, parse_line_items, parse_xml, xml_text
from .errors import InvoiceError
from .llm import discover_items, merge_labels, merge_table_columns
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
    fixed = set(cells)  # e-invoice XML fields; the model's copies of them are dropped
    for cell in found:
        if cell.role and counts[cell.role] == 1:
            if cell.role in cells:
                continue  # the e-invoice XML already has this value
            cell.label = role_column(cell.role).label
            cells[cell.role] = cell
        else:
            cell.role = ""
            if make_key(cell.label, ()) in fixed:
                continue
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
    try:
        items = discover_items(client(), settings.model, text)
    except InvoiceError as e:
        if kind not in ("xml", "zugferd"):
            raise
        # the exact XML values stay; only the extra fields are missing
        row.cells = cells
        row.checks = [Check("discovery_failed", "warning", f"Extra fields could not be read: {e}")]
        row.checks += run_checks(row, [])
        return row
    found = [cell_from_item(path, item, item["type"], SOURCE[kind], kind in ("pdf", "zugferd")) for item in items]
    found = [c for c in found if c.value not in (None, "", [])]
    _keyed(found, cells, row)
    if kind == "scan":
        _scan_boxes(path, cells, settings, client)
    row.cells = cells
    row.checks = row.checks + run_checks(row, [])
    return row


MERGE_FAILED = Check("merge_failed", "warning", "Fields could not be merged across invoices")


def _filled(cell: Cell) -> bool:
    return cell.value not in (None, "", [])


def _example(cell: Cell) -> str:
    if cell.kind == "table":
        return ", ".join(cell.columns)
    text = str(cell.value)
    return text if len(text) <= 60 else text[:57] + "..."


def _warn(rows: List[Row]) -> None:
    for row in rows:
        if MERGE_FAILED not in row.checks:
            row.checks.append(MERGE_FAILED)


def _merge_labels(rows: List[Row], settings: Settings, client: ClientGetter, known: Sequence[Column]) -> None:
    entries: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        for key, cell in row.cells.items():
            if cell.role or key in RESERVED:
                continue
            entry = entries.setdefault(cell.label, {"label": cell.label, "kind": cell.kind, "type": cell.type,
                                                    "examples": []})
            if _filled(cell) and len(entry["examples"]) < 2:
                entry["examples"].append(_example(cell))
    if not entries:
        return
    try:
        mapping = merge_labels(client(), settings.model, list(entries.values()), [c.label for c in known])
    except InvoiceError:
        _warn(rows)
        return
    keys_by_label = {role_column(role).label: role for role in ROLES}
    keys_by_label.update({c.label: c.key for c in known})
    new_keys: Dict[str, str] = {}
    for row in rows:
        planned = {}
        for key, cell in row.cells.items():
            if cell.role or key in RESERVED:
                continue
            target = mapping.get(cell.label, cell.label)
            if make_key(target, ()) == make_key(cell.label, ()):
                target = cell.label  # the same name in other spelling, e.g. "line_items": keep the readable label
            if target not in new_keys:
                taken = set(new_keys.values()) | {c.key for c in known}
                new_keys[target] = keys_by_label.get(target) or make_key(target, taken)
            planned[key] = (new_keys[target], target)
        targets = Counter(new for new, _ in planned.values())
        merged = {}
        for key, cell in row.cells.items():
            new, label = planned.get(key, (key, cell.label))
            clash = key in planned and (targets[new] > 1 or (new in row.cells and new not in planned))
            if clash:
                new, label = key, cell.label  # two items of this invoice stay apart
            cell.label = label
            merged[new] = cell
        row.cells = merged


def _merge_tables(rows: List[Row], settings: Settings, client: ClientGetter) -> None:
    by_key: Dict[str, List[Cell]] = {}
    for row in rows:
        for key, cell in row.cells.items():
            if cell.kind == "table":
                by_key.setdefault(key, []).append(cell)
    for key, cells in by_key.items():
        names: List[str] = []
        for cell in cells:
            names.extend(n for n in cell.columns if n not in names)
        if len({tuple(c.columns) for c in cells}) < 2:
            continue
        try:
            mapping = merge_table_columns(client(), settings.model, names)
        except InvoiceError:
            _warn([row for row in rows if key in row.cells])
            continue
        for cell in cells:
            columns: List[str] = []
            for name in cell.columns:
                if mapping.get(name, name) not in columns:
                    columns.append(mapping.get(name, name))
            renamed = []
            for line in cell.value or []:
                new_line = {c: "" for c in columns}
                for name, text in line.items():
                    target = mapping.get(name, name)
                    if target in new_line and not new_line[target]:
                        new_line[target] = text
                renamed.append(new_line)
            cell.columns = columns
            cell.value = renamed


def merge_rows(rows: List[Row], settings: Settings, client: ClientGetter, known: Sequence[Column] = ()) -> None:
    """Give fields that mean the same thing on different invoices one key and label (rows change in place)."""
    live = [row for row in rows if not row.error]
    if not live or not (settings.base_url and settings.model):
        return
    _merge_labels(live, settings, client, known)
    _merge_tables(live, settings, client)


def field_counts(rows: List[Row]) -> Dict[str, int]:
    """In how many readable invoices each field has a value."""
    counts: Counter = Counter()
    for row in rows:
        if not row.error:
            counts.update(key for key, cell in row.cells.items() if key not in RESERVED and _filled(cell))
    return dict(counts)


def fields_of(rows: List[Row], known: Sequence[Column] = ()) -> List[Column]:
    """One column per field found: known fields first, then by count, then by first appearance."""
    found: Dict[str, Column] = {}
    for row in rows:
        if row.error:
            continue
        for key, cell in row.cells.items():
            if key in RESERVED:
                continue
            col = found.get(key)
            if col is None:
                found[key] = Column(key, cell.label or key, type=cell.type if cell.kind == "value" else "text",
                                    kind=cell.kind, role=cell.role, table_columns=list(cell.columns))
            else:
                col.table_columns.extend(n for n in cell.columns if n not in col.table_columns)
    counts = field_counts(rows)
    order = list(found)
    result = []
    for col in known:
        extra = found.get(col.key)
        columns = list(col.table_columns)
        if extra is not None:
            columns.extend(n for n in extra.table_columns if n not in columns)
        result.append(replace(col, table_columns=columns))
    known_keys = {c.key for c in known}
    rest = sorted((k for k in order if k not in known_keys), key=lambda k: (-counts.get(k, 0), order.index(k)))
    return result + [found[k] for k in rest]


def with_hints(fields: List[Column], rows: List[Row]) -> List[Column]:
    """Copies of the fields; those without a hint get one from example values, for saving as a template."""
    hinted = []
    for col in fields:
        hint: Optional[str] = col.hint
        if not hint and col.kind == "table" and col.table_columns:
            hint = f"Columns: {', '.join(col.table_columns)}."
        elif not hint:
            examples = []
            for row in rows:
                cell = row.cells.get(col.key)
                if cell is not None and not row.error and _filled(cell) and len(examples) < 2:
                    example = _example(cell)
                    if example not in examples:
                        examples.append(example)
            if examples:
                hint = "Example values: " + ", ".join(f'"{e}"' for e in examples) + "."
        hinted.append(replace(col, hint=hint or ""))
    return hinted
