import json
from decimal import Decimal

from conftest import is_vision
from pdfgen import write_samples

from invoice_extract.config import resolve
from invoice_extract.core import _lazy_client
from invoice_extract.discover import discover_file


def llm_settings(server):
    return resolve(base_url=server.base_url, model="m", max_retries=0)


def run(path, settings):
    return discover_file(path, settings, _lazy_client(settings, None))


def items(*entries):
    return lambda body: json.dumps({"items": list(entries)})


def test_xrechnung_without_model_has_roles_and_lines(tmp_path, server):
    row = run(write_samples(tmp_path)[2], resolve())
    assert row.cells["gross"].value == Decimal("119.00") and row.cells["gross"].role == "gross"
    assert row.cells["gross"].label == "Gross" and row.cells["gross"].source == "xml"
    lines = row.cells["line_items"]
    assert lines.kind == "table" and lines.label == "Line Items" and lines.value[0]["Description"] == "Paper"
    assert row.status == "ok"
    assert server.requests == []


def test_xml_wins_over_discovery(tmp_path, server):
    server.reply = items(
        {"label": "Gross", "kind": "value", "type": "amount", "role": "gross", "value": "1.00", "page": 1},
        {"label": "Customer No.", "kind": "value", "value": "K-1", "source_text": "K-1", "page": 1})
    row = run(write_samples(tmp_path)[2], llm_settings(server))
    assert row.cells["gross"].value == Decimal("119.00")
    assert row.cells["customer_no"].value == "K-1"


def test_pdf_duplicate_labels_and_roles(tmp_path, server):  # Review Focus 1
    server.reply = items(
        {"label": "Date", "kind": "value", "type": "date", "value": "2026-10-21", "source_text": "Date 2026-10-21"},
        {"label": "Date", "kind": "value", "type": "date", "value": "2026-11-20", "source_text": "Due 2026-11-20"},
        {"label": "Total", "kind": "value", "type": "amount", "role": "gross", "value": "119.00",
         "source_text": "Total 119.00 EUR"})
    row = run(write_samples(tmp_path)[0], llm_settings(server))
    assert {"date", "date_2", "gross"} <= set(row.cells)
    assert row.cells["date"].value != row.cells["date_2"].value
    assert row.cells["gross"].label == "Gross" and row.cells["date_2"].label == "Date"


def test_role_conflict(tmp_path, server):
    from invoice_extract.columns import make_key
    server.reply = items(
        {"label": "VAT 7 %", "kind": "value", "type": "amount", "role": "vat", "value": "7.00"},
        {"label": "VAT 19 %", "kind": "value", "type": "amount", "role": "vat", "value": "19.00"})
    row = run(write_samples(tmp_path)[0], llm_settings(server))
    assert "vat" not in row.cells
    assert {make_key("VAT 7 %", []), make_key("VAT 19 %", [])} <= set(row.cells)
    assert all(c.role == "" for c in row.cells.values())
    assert [c.name for c in row.checks if c.level != "ok"] == ["role_conflict"]


def test_pdf_without_model_is_config_error(tmp_path, server):
    import pytest
    from invoice_extract.errors import ConfigError
    with pytest.raises(ConfigError, match="--model"):
        run(write_samples(tmp_path)[0], resolve())


def test_scan_discovery_boxes(tmp_path, server):
    scan = write_samples(tmp_path)[3]

    def reply(body):
        if is_vision(body):
            text = json.dumps(body)
            if "locate printed text" in text:
                return json.dumps({"gross": [100, 100, 300, 140]})
            return "Invoice RE-2026-001\nTotal 119.00 EUR"
        return json.dumps({"items": [{"label": "Gross", "kind": "value", "type": "amount", "value": "119.00",
                                      "source_text": "Total 119.00 EUR", "page": 1}]})
    server.reply = reply
    row = run(scan, llm_settings(server))
    assert row.kind == "scan"
    assert row.cells["gross"].box is not None and row.cells["gross"].source == "vision"
