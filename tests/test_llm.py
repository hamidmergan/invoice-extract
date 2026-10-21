import json

import pytest

from invoice_extract.columns import STANDARD_COLUMNS, Column
from invoice_extract.config import Settings
from invoice_extract.errors import ConfigError, ModelError
from invoice_extract.llm import fill_columns, make_client, vision_boxes
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
    assert result["invoice_no"] == {"value": "R-1", "source_text": "R-1", "page": 1}
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
