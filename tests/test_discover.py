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


# --- merging across invoices ---

from invoice_extract.columns import Column  # noqa: E402
from invoice_extract.discover import field_counts, fields_of, merge_rows, with_hints  # noqa: E402
from invoice_extract.results import Cell, Row  # noqa: E402


def val(label, value, role=""):
    return Cell(value=value, label=label, kind="value", role=role, type="text", source="text")


def table(label, columns, rows):
    return Cell(value=rows, label=label, kind="table", columns=list(columns), source="text")


def make_row(name, **cells):
    return Row(name, kind="pdf", cells=dict(cells))


def mapping(m):
    return lambda body: json.dumps({"mapping": m})


def merge(rows, settings, known=()):
    merge_rows(rows, settings, _lazy_client(settings, None), known)


def test_merge_maps_labels_across_rows(server):
    a = make_row("a.pdf", kd_nr=val("Kd.-Nr.", "1"), gross=val("Gross", "9", role="gross"))
    b = make_row("b.pdf", customer_number=val("Customer number", "2"))
    server.reply = mapping({"Kd.-Nr.": "Customer No.", "Customer number": "Customer No."})
    merge([a, b], llm_settings(server))
    assert list(a.cells) == ["customer_no", "gross"] and list(b.cells) == ["customer_no"]
    assert a.cells["customer_no"].label == "Customer No." and b.cells["customer_no"].value == "2"
    payload = json.loads(server.requests[0]["body"]["messages"][-1]["content"])
    assert [entry["label"] for entry in payload["labels"]] == ["Kd.-Nr.", "Customer number"]  # roles are not sent


def test_merge_keeps_same_invoice_items_apart(server):  # Review Focus 2
    a = make_row("a.pdf", vat_7=val("VAT 7 %", "7"), vat_19=val("VAT 19 %", "19"))
    b = make_row("b.pdf", tax=val("Tax", "5"))
    server.reply = mapping({"VAT 7 %": "VAT", "VAT 19 %": "VAT", "Tax": "VAT"})
    merge([a, b], llm_settings(server))
    assert set(a.cells) == {"vat_7", "vat_19"}
    assert set(b.cells) == {"vat"} and b.cells["vat"].label == "VAT"


def test_merge_uses_known_keys(server):
    a = make_row("a.pdf", kunden_nr=val("Kunden-Nr.", "1"))
    server.reply = mapping({"Kunden-Nr.": "Customer No."})
    merge([a], llm_settings(server), known=[Column("cust", "Customer No.")])
    assert list(a.cells) == ["cust"]


def test_merge_failure_warns(server):
    a = make_row("a.pdf", kd_nr=val("Kd.-Nr.", "1"))
    server.status = 500
    merge([a], llm_settings(server))
    assert list(a.cells) == ["kd_nr"]
    assert "merge_failed" in [c.name for c in a.checks]


def test_merge_without_model_makes_no_request(server):
    a = make_row("a.pdf", kd_nr=val("Kd.-Nr.", "1"))
    merge([a], resolve())
    assert list(a.cells) == ["kd_nr"] and server.requests == []


def test_merge_table_columns_per_table(server):
    a = make_row("a.pdf", line_items=table("Line Items", ["Artikel", "Betrag EUR"], [{"Artikel": "P", "Betrag EUR": "1"}]))
    b = make_row("b.pdf", line_items=table("Line Items", ["Description", "Amount"], [{"Description": "T", "Amount": "2"}]))

    def reply(body):
        if "column names" in json.dumps(body):
            return json.dumps({"mapping": {"Artikel": "Description", "Betrag EUR": "Amount"}})
        return json.dumps({"mapping": {}})
    server.reply = reply
    merge([a, b], llm_settings(server))
    for row in (a, b):
        assert row.cells["line_items"].columns == ["Description", "Amount"]
    assert a.cells["line_items"].value == [{"Description": "P", "Amount": "1"}]


def test_fields_of_order_and_counts():
    rows = [make_row("a.pdf", note=val("Note", "x"), cust=val("Customer No.", "1")),
            make_row("b.pdf", cust=val("Customer No.", "2"), lines=table("Line Items", ["A"], [{"A": "1"}])),
            make_row("c.pdf", cust=val("Customer No.", ""), lines=table("Line Items", ["B"], [{"B": "2"}])),
            Row("d.pdf", error="broken")]
    assert field_counts(rows) == {"note": 1, "cust": 2, "lines": 2}
    fields = fields_of(rows, known=[Column("note", "Note")])
    assert [f.key for f in fields] == ["note", "cust", "lines"]
    assert fields[2].kind == "table" and fields[2].table_columns == ["A", "B"]


def test_with_hints():
    rows = [make_row("a.pdf", cust=val("Customer No.", "K-1"), lines=table("Line Items", ["A"], [{"A": "1"}])),
            make_row("b.pdf", cust=val("Customer No.", "K-2"))]
    fields = [Column("cust", "Customer No."), Column("lines", "Line Items", kind="table", table_columns=["A", "B"]),
              Column("x", "X", hint="keep me")]
    hinted = with_hints(fields, rows)
    assert hinted[0].hint == 'Example values: "K-1", "K-2".'
    assert hinted[1].hint == "Columns: A, B."
    assert hinted[2].hint == "keep me"
    assert fields[0].hint == ""


def test_einvoice_keeps_xml_when_discovery_fails(tmp_path, server):
    server.reply = lambda body: "{}"
    row = run(write_samples(tmp_path)[2], llm_settings(server))
    assert row.error is None and row.cells["gross"].value == Decimal("119.00")
    assert [c.name for c in row.checks if c.level != "ok"] == ["discovery_failed"]


def test_einvoice_drops_discovered_duplicates_of_xml_fields(tmp_path, server):
    server.reply = items(
        {"label": "Line Items", "kind": "table", "columns": ["Item"], "rows": [["Paper"]], "page": 1},
        {"label": "Customer No.", "kind": "value", "value": "K-1", "page": 1})
    row = run(write_samples(tmp_path)[2], llm_settings(server))
    assert sorted(k for k in row.cells if k.startswith("line_items")) == ["line_items"]
    assert row.cells["line_items"].source == "xml" and "customer_no" in row.cells


def test_with_hints_uses_distinct_examples():
    rows = [make_row("a.pdf", cust=val("Customer No.", "K-1")), make_row("b.pdf", cust=val("Customer No.", "K-1")),
            make_row("c.pdf", cust=val("Customer No.", "K-2"))]
    assert with_hints([Column("cust", "Customer No.")], rows)[0].hint == 'Example values: "K-1", "K-2".'


def test_merge_keeps_label_when_model_answers_a_key(server):
    a = make_row("a.pdf", line_items=table("Line Items", ["A"], [{"A": "1"}]))
    server.reply = mapping({"Line Items": "line_items"})
    merge([a], llm_settings(server))
    assert list(a.cells) == ["line_items"] and a.cells["line_items"].label == "Line Items"


def test_label_key_never_takes_a_role_key(tmp_path, server):
    server.reply = items(
        {"label": "Gross", "kind": "value", "type": "amount", "value": "100.00"},
        {"label": "Total", "kind": "value", "type": "amount", "role": "gross", "value": "119.00"})
    row = run(write_samples(tmp_path)[0], llm_settings(server))
    assert row.cells["gross"].value == Decimal("119.00") and row.cells["gross"].role == "gross"
    assert any(c.value == Decimal("100.00") and c.role == "" for c in row.cells.values())


def test_role_conflict_runs_no_check_on_one_of_them(tmp_path, server):
    server.reply = items(
        {"label": "VAT", "kind": "value", "type": "amount", "role": "vat", "value": "7.00"},
        {"label": "VAT", "kind": "value", "type": "amount", "role": "vat", "value": "19.00"},
        {"label": "Net", "kind": "value", "type": "amount", "role": "net", "value": "100.00"},
        {"label": "Gross", "kind": "value", "type": "amount", "role": "gross", "value": "126.00"})
    row = run(write_samples(tmp_path)[0], llm_settings(server))
    assert "vat" not in row.cells
    assert sorted(str(c.value) for c in row.cells.values() if c.label == "VAT") == ["19.00", "7.00"]
    assert "totals" not in [c.name for c in row.checks]


def test_status_label_is_kept(tmp_path, server):  # M1
    server.reply = items({"label": "Status", "kind": "value", "value": "Paid"},
                         {"label": "File", "kind": "value", "value": "A-7"})
    row = run(write_samples(tmp_path)[0], llm_settings(server))
    assert [f.label for f in fields_of([row])] == ["Status", "File"]


def test_merge_chain_never_overwrites(server):  # I4
    a = make_row("a.pdf", order_date=val("Order Date", "1"), date=val("Date", "2"),
                 document_date=val("Document Date", "3"))
    server.reply = mapping({"Order Date": "Date", "Date": "X", "Document Date": "X"})
    merge([a], llm_settings(server))
    assert sorted(c.value for c in a.cells.values()) == ["1", "2", "3"]


def test_merge_table_columns_never_join_two_columns_of_one_table(server):  # I5b
    a = make_row("a.pdf", line_items=table("Line Items", ["Unit Price", "Total"], [{"Unit Price": "5.00", "Total": "10.00"}]))
    b = make_row("b.pdf", line_items=table("Line Items", ["Amount"], [{"Amount": "3.00"}]))

    def reply(body):
        if "column names" in json.dumps(body):
            return json.dumps({"mapping": {"Unit Price": "Amount", "Total": "Amount"}})
        return json.dumps({"mapping": {}})
    server.reply = reply
    merge([a, b], llm_settings(server))
    assert sorted(a.cells["line_items"].value[0].values()) == ["10.00", "5.00"]
