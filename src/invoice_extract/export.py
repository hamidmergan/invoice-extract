"""Write rows to Excel, CSV or JSON."""

import csv
import datetime
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from typing import Any, List, Optional, Union

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .columns import STANDARD_COLUMNS, Column
from .errors import ConfigError
from .results import Row, _value

FORMATS = ("xlsx", "csv", "json")
DETAILS_HEADER = ["File", "Column", "Value", "Raw text", "Source", "Page", "Edited", "Checks"]
# Spreadsheet apps run cells that start with these as formulas.
DANGEROUS = ("=", "+", "-", "@", "\t", "\r")
MAX_WIDTH = 50


def _dangerous(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(DANGEROUS)


def _cell_value(row: Row, column: Column) -> Any:
    if column.key == "file":
        return Path(row.path).name
    if column.key == "status":
        return row.status
    if row.error:
        return None
    cell = row.cells.get(column.key)
    return None if cell is None else cell.value


def _checks(checks) -> str:
    return "; ".join(f"{c.level}: {c.message}" for c in checks)


def _details(rows: List[Row], columns: List[Column]) -> List[List[Any]]:
    labels = {c.key: c.label for c in columns}
    out = []
    for row in rows:
        name = Path(row.path).name
        if row.error:
            out.append([name, "(error)", None, "", "", None, False, f"error: {row.error}"])
            continue
        for key, cell in row.cells.items():
            out.append([name, labels.get(key, key), cell.value, cell.raw, cell.source, cell.page, cell.edited,
                        _checks(cell.checks)])
        if row.checks:
            out.append([name, "(invoice)", None, "", "", None, False, _checks(row.checks)])
    return out


def _put(ws, row: int, col: int, value: Any):
    if isinstance(value, Decimal):
        cell = ws.cell(row, col, float(value))
        cell.number_format = "#,##0.00"
    elif isinstance(value, datetime.date):
        cell = ws.cell(row, col, value)
        cell.number_format = "yyyy-mm-dd"
    else:
        cell = ws.cell(row, col, value)
        if _dangerous(value):
            cell.data_type = "s"
            cell.quotePrefix = True
    return cell


def _sheet(ws, header: List[str], records: List[List[Any]]) -> None:
    widths = [len(h) for h in header]
    for j, label in enumerate(header, start=1):
        ws.cell(1, j, label).font = Font(bold=True)
    for i, record in enumerate(records, start=2):
        for j, value in enumerate(record, start=1):
            _put(ws, i, j, value)
            if value is not None:
                widths[j - 1] = max(widths[j - 1], len(str(_value(value))))
    for j, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(j)].width = min(width + 2, MAX_WIDTH)
    ws.freeze_panes = "A2"


def _xlsx(rows: List[Row], path: Path, columns: List[Column]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoices"
    _sheet(ws, [c.label for c in columns], [[_cell_value(r, c) for c in columns] for r in rows])
    _sheet(wb.create_sheet("Details"), DETAILS_HEADER, _details(rows, columns))
    wb.save(path)


def _csv_text(value: Any) -> str:
    if value is None:
        return ""
    if _dangerous(value):
        return "'" + value
    return str(_value(value))


def _csv(rows: List[Row], path: Path, columns: List[Column]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([_csv_text(c.label) for c in columns])
        for row in rows:
            writer.writerow([_csv_text(_cell_value(row, c)) for c in columns])


def _json(rows: List[Row], path: Path, columns: List[Column]) -> None:
    data = {"columns": [asdict(c) for c in columns], "rows": [r.to_dict() for r in rows]}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


WRITERS = {"xlsx": _xlsx, "csv": _csv, "json": _json}


def export(rows: List[Row], path: Union[str, Path], columns: Optional[List[Column]] = None,
           format: Optional[str] = None) -> Path:
    """Write `rows` to `path`. The format comes from the suffix unless `format` is given."""
    path = Path(path)
    fmt = (format or path.suffix).lower().lstrip(".")
    if fmt not in WRITERS:
        raise ConfigError(f"Unknown export format {fmt!r}; use one of: {', '.join(FORMATS)}")
    WRITERS[fmt](list(rows), path, list(columns or STANDARD_COLUMNS))
    return path
