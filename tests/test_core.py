import json
from decimal import Decimal

import pytest
from conftest import is_vision
from pdfgen import write_samples

from invoice_extract import (
    ConfigError,
    LimitError,
    STANDARD_COLUMNS,
    add_columns,
    column,
    extract,
    extract_many,
)


def llm(server):
    return {"base_url": server.base_url, "model": "m", "max_retries": 0}


def system_prompt(body: dict) -> str:
    content = body["messages"][0]["content"]
    return content if isinstance(content, str) else ""


def user_content(body: dict):
    return body["messages"][-1]["content"]


PDF_REPLY = {
    "supplier": {"value": "ACME GmbH", "source_text": "ACME GmbH", "page": 1},
    "invoice_no": {"value": "RE-2026-001", "source_text": "RE-2026-001", "page": 1},
    "gross": {"value": "119.00", "source_text": "119.00", "page": 1},
}


def test_zugferd_no_model_no_requests(tmp_path, server):
    paths = write_samples(tmp_path)
    for path in (paths[1], paths[2]):
        row = extract(path)
        assert row.status == "ok", row.to_dict()
        assert row.cells["gross"].value == Decimal("119.00")
        assert row.cells["gross"].source == "xml"
        assert row.cells["file"].value == path.name
    assert server.requests == []


def test_zugferd_custom_column_uses_llm(tmp_path, server):
    paths = write_samples(tmp_path)
    server.reply = lambda body: json.dumps({"order_no": {"value": "PO-1", "source_text": "PO-1", "page": 1}})
    columns = STANDARD_COLUMNS + [column("Order No.")]
    row = extract(paths[1], columns, **llm(server))
    assert len(server.requests) == 1
    assert "CrossIndustryInvoice" in user_content(server.requests[0]["body"])
    assert row.cells["order_no"].value == "PO-1"
    assert row.cells["invoice_no"].source == "xml"


def test_pdf_flow_values_and_boxes(tmp_path, server):
    paths = write_samples(tmp_path)
    server.reply = lambda body: json.dumps(PDF_REPLY)
    row = extract(paths[0], **llm(server))
    assert row.kind == "pdf"
    assert "<!-- page 1 -->" in user_content(server.requests[0]["body"])
    cell = row.cells["invoice_no"]
    assert cell.value == "RE-2026-001"
    assert cell.source == "text"
    assert cell.box is not None and cell.box.page == 1
    assert row.cells["gross"].value == Decimal("119.00")
    assert row.cells["file"].source == "text"


def test_pdf_source_not_located_warning(tmp_path, server):
    paths = write_samples(tmp_path)
    server.reply = lambda body: json.dumps({"invoice_no": {"value": "RE-2026-001", "source_text": "NOPE", "page": 1}})
    row = extract(paths[0], **llm(server))
    cell = row.cells["invoice_no"]
    assert cell.box is None
    assert [c.name for c in cell.checks] == ["source_not_located"]
    assert row.status == "warning"


def test_scan_flow(tmp_path, server):
    paths = write_samples(tmp_path)

    def reply(body):
        if system_prompt(body).startswith("You locate"):
            return json.dumps({"invoice_no": [100, 100, 300, 150]})
        if is_vision(body):
            return "Invoice R-9"
        return json.dumps({"invoice_no": {"value": "R-9", "source_text": "R-9", "page": 1}})

    server.reply = reply
    row = extract(paths[3], **llm(server))
    assert row.kind == "scan"
    cell = row.cells["invoice_no"]
    assert cell.value == "R-9"
    assert cell.source == "vision"
    assert cell.box is not None and cell.box.page == 1
    fill = [r for r in server.requests if not is_vision(r["body"])]
    assert len(fill) == 1 and "Invoice R-9" in user_content(fill[0]["body"])


def test_extract_many_isolates_errors(tmp_path, server):
    good = write_samples(tmp_path)[0]
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 not really a pdf")
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    server.reply = lambda body: json.dumps(PDF_REPLY)
    rows = extract_many([good, broken, empty], **llm(server))
    assert [r.path for r in rows] == [str(good), str(broken), str(empty)]
    assert rows[0].error is None
    assert rows[1].error and rows[2].error
    assert rows[2].error.startswith("UnsupportedFileError: ")
    assert all(r.status == "error" for r in rows[1:])


def test_extract_many_limit(tmp_path, server):
    paths = write_samples(tmp_path)[:3]
    with pytest.raises(LimitError):
        extract_many(paths, max_files=2, **llm(server))
    assert server.requests == []


def test_add_columns_keeps_edits(tmp_path, server):
    paths = write_samples(tmp_path)
    server.reply = lambda body: json.dumps(PDF_REPLY)
    row = extract(paths[0], **llm(server))
    row.cells["supplier"].value = "Edited Ltd"
    row.cells["supplier"].edited = True

    server.requests.clear()
    server.reply = lambda body: json.dumps({"customer_no": {"value": "K-42", "source_text": "K-42", "page": 1}})
    row = add_columns(row, [column("Customer No.")], **llm(server))
    assert len(server.requests) == 1
    spec = user_content(server.requests[0]["body"]).split("\n\nInvoice text:")[0]
    assert '"customer_no"' in spec and '"supplier"' not in spec
    assert row.cells["supplier"].value == "Edited Ltd"
    assert row.cells["customer_no"].value == "K-42"
    assert row.cells["invoice_no"].value == "RE-2026-001"


def test_missing_model_for_pdf_raises_config_error(tmp_path, server):
    paths = write_samples(tmp_path)
    with pytest.raises(ConfigError):
        extract(paths[0])
    assert server.requests == []
