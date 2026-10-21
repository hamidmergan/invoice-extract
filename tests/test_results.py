import datetime
import json
from decimal import Decimal

from invoice_extract.results import Box, Cell, Check, Row


def test_row_status_worst_level():
    assert Row(path="a.pdf").status == "ok"
    row = Row(
        path="a.pdf",
        cells={"iban": Cell(value="DE00", checks=[Check("iban", "warning", "bad checksum")])},
        checks=[Check("sum", "ok", "")],
    )
    assert row.status == "warning"
    row.checks.append(Check("dates", "error", "due before invoice date"))
    assert row.status == "error"


def test_row_status_error_wins():
    row = Row(path="a.pdf", checks=[Check("sum", "ok", "")], error="cannot read file")
    assert row.status == "error"


def test_row_to_dict_json_safe():
    row = Row(
        path="a.pdf",
        kind="pdf",
        cells={
            "invoice_date": Cell(value=datetime.date(2026, 10, 21), raw="21.10.2026", page=1,
                                 box=Box(1, 0.1, 0.2, 0.3, 0.25), source="text"),
            "gross": Cell(value=Decimal("1234.50"), raw="1.234,50", source="xml"),
        },
    )
    text = json.dumps(row.to_dict())
    assert '"2026-10-21"' in text
    assert '"1234.50"' in text
    data = row.to_dict()
    assert data["status"] == "ok"
    assert data["cells"]["invoice_date"]["box"] == {"page": 1, "x0": 0.1, "y0": 0.2, "x1": 0.3, "y1": 0.25}


def test_defaults_not_shared():
    a, b = Row(path="a"), Row(path="b")
    a.checks.append(Check("x", "ok", ""))
    a.cells["k"] = Cell()
    assert b.checks == [] and b.cells == {}
    assert Cell().checks is not Cell().checks


def test_table_cell_to_dict():
    row = Row("a.pdf", cells={"line_items": Cell(kind="table", label="Line Items", columns=["Description"],
                                                 value=[{"Description": "Paper"}])})
    cell = row.to_dict()["cells"]["line_items"]
    assert cell["kind"] == "table" and cell["label"] == "Line Items"
    assert cell["columns"] == ["Description"] and cell["value"] == [{"Description": "Paper"}]
