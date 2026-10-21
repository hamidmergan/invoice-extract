import json

import pytest

from invoice_extract.columns import STANDARD_COLUMNS, Column
from invoice_extract.config import Settings
from invoice_extract.errors import ConfigError, ModelError
from invoice_extract.llm import (
    discover_items,
    fill_columns,
    make_client,
    merge_labels,
    merge_table_columns,
    vision_boxes,
)
from invoice_extract.results import Box

COLUMNS = [Column("invoice_no", "Invoice No."), Column("gross", "Gross", type="amount")]
TEXT = "<!-- page 1 -->\nInvoice R-1\nTotal 119.00"


def client_for(server, api_key=None):
    return make_client(Settings(base_url=server.base_url, model="m", api_key=api_key, max_retries=0))


def answer(**fields):
    return lambda body: json.dumps(fields)


def test_make_client_requires_settings():
    with pytest.raises(ConfigError):
        make_client(Settings())


def test_fill_columns_basic(server):
    server.reply = answer(invoice_no={"value": "R-1", "source_text": "R-1", "page": 1})
    result = fill_columns(client_for(server), "m", COLUMNS, TEXT)
    item = result["invoice_no"]
    assert {k: item[k] for k in ("value", "source_text", "page")} == {"value": "R-1", "source_text": "R-1", "page": 1}
    body = server.requests[0]["body"]
    assert body["temperature"] == 0
    assert body["model"] == "m"
    assert "Invoice R-1" in body["messages"][-1]["content"]


def test_fill_columns_strips_fences_and_extra_keys(server):
    data = {"invoice_no": {"value": "R-1", "source_text": "R-1", "page": 1},
            "gross": {"value": 119.0, "source_text": "119.00", "page": 1}, "bogus": 1}
    server.reply = lambda body: "Here you go:\n```json\n" + json.dumps(data) + "\n```\nDone."
    result = fill_columns(client_for(server), "m", COLUMNS, TEXT)
    assert set(result) == {"invoice_no", "gross"}
    assert result["gross"]["value"] == 119.0


def test_fill_columns_missing_key_is_none(server):
    server.reply = answer(invoice_no={"value": "R-1", "source_text": "R-1", "page": 1})
    assert fill_columns(client_for(server), "m", COLUMNS, TEXT)["gross"] is None


def test_fill_columns_bad_page(server):
    server.reply = answer(invoice_no={"value": "R-1", "source_text": "R-1", "page": "one"})
    assert fill_columns(client_for(server), "m", COLUMNS, TEXT)["invoice_no"]["page"] is None


def test_fill_columns_not_json_raises(server):
    server.reply = lambda body: "sorry"
    with pytest.raises(ModelError):
        fill_columns(client_for(server), "m", COLUMNS, TEXT)


def test_fill_columns_server_error_raises(server):
    server.status = 500
    with pytest.raises(ModelError):
        fill_columns(client_for(server), "m", COLUMNS, TEXT)


def test_reserved_not_sent(server):
    fill_columns(client_for(server), "m", STANDARD_COLUMNS, TEXT)
    content = server.requests[0]["body"]["messages"][-1]["content"]
    sent = json.loads(content[content.index("["):content.index("]") + 1])
    keys = [c["key"] for c in sent]
    assert "status" not in keys and "file" not in keys
    assert "supplier" in keys


def test_api_key_header(server):
    fill_columns(client_for(server), "m", COLUMNS, TEXT)
    assert server.requests[0]["headers"]["authorization"] == "Bearer not-needed"
    fill_columns(client_for(server, api_key="sk-x"), "m", COLUMNS, TEXT)
    assert server.requests[1]["headers"]["authorization"] == "Bearer sk-x"


def test_vision_boxes_valid_and_invalid(server):
    server.reply = answer(invoice_no=[100, 200, 300, 250], gross=[500, 0, 100, 10])
    boxes = vision_boxes(client_for(server), "m", b"png", {"invoice_no": "R-1", "gross": "119.00"}, 2)
    assert boxes == {"invoice_no": Box(2, 0.1, 0.2, 0.3, 0.25)}
    parts = server.requests[0]["body"]["messages"][-1]["content"]
    assert any(p.get("type") == "image_url" for p in parts)


def test_vision_boxes_error_returns_empty(server):
    server.status = 500
    assert vision_boxes(client_for(server), "m", b"png", {"invoice_no": "R-1"}, 1) == {}
    server.status = 200
    server.reply = lambda body: "no idea"
    assert vision_boxes(client_for(server), "m", b"png", {"invoice_no": "R-1"}, 1) == {}


def test_fill_prompt_has_keyed_answer_skeleton(server):
    # small models otherwise answer one unkeyed object per line
    server.reply = answer()
    fill_columns(client_for(server), "m", COLUMNS, TEXT)
    content = server.requests[0]["body"]["messages"][-1]["content"]
    start = content.index("Answer skeleton:\n") + len("Answer skeleton:\n")
    skeleton = json.loads(content[start:content.index("\n", start)])
    assert list(skeleton) == ["invoice_no", "gross"]
    assert skeleton["gross"] == {"value": None, "source_text": "", "page": None}


@pytest.mark.parametrize("content", [
    '<think>The shape is {"invoice_no": ...}, careful.</think>\n{"invoice_no": {"value": "R-1", "page": 1}}',
    '{"invoice_no": {"value": "R-1", "page": 1}}\nNote: values in {braces} are copied as printed.',
])
def test_fill_columns_reasoning_and_trailing_text(server, content):
    server.reply = lambda body: content
    out = fill_columns(client_for(server), "m", COLUMNS, TEXT)
    assert out["invoice_no"]["value"] == "R-1"


ITEMS = {"items": [
    {"label": "Invoice No.", "kind": "value", "type": "text", "role": "invoice_no", "value": "R-1",
     "source_text": "Invoice R-1", "page": 1},
    {"label": "Payment Note", "kind": "text", "text": "We debit 119.00 EUR on 2026-11-20.",
     "source_text": "We debit", "end_text": "2026-11-20.", "page": 1},
    {"label": "Line Items", "kind": "table", "columns": ["Description", "Amount"],
     "rows": [["Paper", "100,00"], {"Description": "Toner", "Amount": 19}], "source_text": "Paper 100,00",
     "end_text": "Toner 19", "page": 1},
]}


def test_discover_items_three_kinds(server):
    server.reply = lambda body: json.dumps(ITEMS)
    items = discover_items(client_for(server), "m", TEXT)
    assert [i["kind"] for i in items] == ["value", "text", "table"]
    assert items[0]["role"] == "invoice_no" and items[0]["value"] == "R-1"
    assert items[1]["text"].startswith("We debit") and items[1]["end_text"] == "2026-11-20."
    assert items[2]["rows"] == [{"Description": "Paper", "Amount": "100,00"}, {"Description": "Toner", "Amount": "19"}]
    assert set(items[0]) == {"label", "kind", "type", "role", "value", "text", "columns", "rows", "source_text",
                             "end_text", "page"}


def test_discover_items_sloppy(server):  # Review Focus 3
    server.reply = lambda body: json.dumps({"items": [
        {"label": "", "kind": "value", "value": "x"},
        {"label": "Pic", "kind": "picture"},
        {"label": "Ref", "kind": "value", "type": "money", "role": "boss", "value": "A", "page": "one"},
        {"label": "T", "kind": "table", "columns": ["A", "B"], "rows": [["1"], ["1", "2", "3"], None]},
        "junk",
    ]})
    items = discover_items(client_for(server), "m", TEXT)
    assert [i["label"] for i in items] == ["Ref", "T"]
    assert items[0]["type"] == "text" and items[0]["role"] == "" and items[0]["page"] is None
    assert items[1]["rows"] == [{"A": "1", "B": ""}, {"A": "1", "B": "2"}]


def test_discover_items_needs_items_list(server):
    server.reply = lambda body: '{"answer": 1}'
    with pytest.raises(ModelError):
        discover_items(client_for(server), "m", TEXT)


def test_fill_columns_text_and_table(server):
    cols = [Column("note", "Note", kind="text"),
            Column("lines", "Line Items", kind="table", table_columns=["Description", "Amount"])]
    server.reply = lambda body: json.dumps({
        "note": {"text": "Pay soon.", "source_text": "Pay", "end_text": "soon.", "page": 1},
        "lines": {"rows": [["Paper", "100,00"]], "source_text": "Paper", "page": 1}})
    out = fill_columns(client_for(server), "m", cols, TEXT)
    assert out["note"]["text"] == "Pay soon." and out["note"]["kind"] == "text"
    assert out["lines"]["rows"] == [{"Description": "Paper", "Amount": "100,00"}]
    sent = server.requests[0]["body"]["messages"][-1]["content"]
    assert '"kind": "table"' in sent and "Description" in sent


def test_merge_labels(server):
    server.reply = lambda body: json.dumps({"mapping": {"Kd.-Nr.": "Customer No.", "Customer number": "Customer No.",
                                                        "Odd": 5}})
    labels = [{"label": label, "kind": "value", "type": "text", "examples": ["1"]}
              for label in ("Kd.-Nr.", "Customer number", "Odd", "Missing")]
    out = merge_labels(client_for(server), "m", labels, known=["Customer No."])
    assert out == {"Kd.-Nr.": "Customer No.", "Customer number": "Customer No.", "Odd": "Odd", "Missing": "Missing"}
    assert "Customer No." in json.dumps(server.requests[0]["body"])


def test_merge_table_columns(server):
    server.reply = lambda body: json.dumps({"mapping": {"Betrag EUR": "Amount"}})
    out = merge_table_columns(client_for(server), "m", ["Betrag EUR", "Amount"])
    assert out == {"Betrag EUR": "Amount", "Amount": "Amount"}


def test_discover_items_infers_roles_from_labels(server):
    server.reply = lambda body: json.dumps({"items": [
        {"label": "Net", "kind": "value", "type": "amount", "value": "100.00"},
        {"label": "Total Amount", "kind": "value", "type": "amount", "value": "119.00"},
        {"label": "IBAN", "kind": "value", "type": "iban", "value": "DE89"},
        {"label": "Gross", "kind": "value", "type": "amount", "role": "net", "value": "1"},  # explicit role wins
        {"label": "VAT", "kind": "text", "text": "VAT is included."},  # only values get roles
    ]})
    items = discover_items(client_for(server), "m", TEXT)
    assert [i["role"] for i in items] == ["", "gross", "iban", "net", ""]
