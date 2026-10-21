import io
import json
import time

import openpyxl
import pytest
from pdfgen import make_pdf, write_samples

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from invoice_extract.config import resolve  # noqa: E402
from invoice_extract.web.app import create_app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"


def client_for(settings):
    return TestClient(create_app(settings))


def upload(client, *paths):
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
    return client.post("/api/files", files=files)


def wait_done(client, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get("/api/state").json()
        if state["files"] and all(f["state"] == "done" for f in state["files"]):
            return state
        time.sleep(0.05)
    raise AssertionError("files did not finish in time")


def test_upload_xml_and_state(tmp_path, server):
    xml = write_samples(tmp_path)[2]
    with client_for(resolve()) as client:
        res = upload(client, xml)
        assert res.status_code == 200
        assert res.json()["files"][0]["name"] == "xrechnung.xml"
        state = wait_done(client)
    entry = state["files"][0]
    assert entry["kind"] == "xml"
    assert entry["status"] == "ok"
    assert entry["row"]["cells"]["invoice_no"]["value"] == "RE-2026-001"
    assert state["summary"] == {"files": 1, "columns": len(state["columns"]), "to_check": 0}
    assert server.requests == []


def test_missing_model_is_row_error(tmp_path, server):
    pdf = write_samples(tmp_path)[0]
    with client_for(resolve()) as client:
        assert upload(client, pdf).status_code == 200
        state = wait_done(client)
    entry = state["files"][0]
    assert entry["status"] == "error"
    assert "--model" in entry["row"]["error"]
    assert state["summary"]["to_check"] == 1


def test_page_png_and_segments(tmp_path, server):
    pdf = tmp_path / "click.pdf"
    pdf.write_bytes(make_pdf([[(50, 780, "ACME GmbH"), (50, 700, "Kd.-Nr."), (120, 700, "40711")]]))
    server.reply = lambda body: "{}"
    with client_for(resolve(base_url=server.base_url, model="m")) as client:
        file_id = upload(client, pdf).json()["files"][0]["id"]
        assert client.get(f"/api/files/{file_id}/pages").json() == {"count": 1}
        png = client.get(f"/api/files/{file_id}/pages/1.png")
        assert png.status_code == 200
        assert png.headers["content-type"] == "image/png"
        assert png.content.startswith(PNG)
        segs = client.get(f"/api/files/{file_id}/pages/1/segments").json()
        assert any("40711" in s["text"] for s in segs)
        assert set(segs[0]) == {"index", "text", "box"}
        assert client.get(f"/api/files/{file_id}/pages/2.png").status_code == 404


def test_add_column_from_click(tmp_path, server):
    pdf = tmp_path / "click.pdf"
    pdf.write_bytes(make_pdf([[(50, 780, "ACME GmbH"), (50, 700, "Kd.-Nr."), (120, 700, "40711"),
                               (50, 650, "Total 1.234,50 EUR")]]))
    server.reply = lambda body: json.dumps({"customer_no": {"value": "40711", "source_text": "40711", "page": 1}})
    with client_for(resolve(base_url=server.base_url, model="m")) as client:
        file_id = upload(client, pdf).json()["files"][0]["id"]
        wait_done(client)
        segs = client.get(f"/api/files/{file_id}/pages/1/segments").json()
        index = next(s["index"] for s in segs if "40711" in s["text"])
        before = len(server.requests)
        res = client.post("/api/columns", json={"label": "Customer No.", "from": {"file_id": file_id, "page": 1,
                                                                                   "index": index}})
        assert res.status_code == 200
        col = res.json()
        assert col["key"] == "customer_no"
        assert "Kd.-Nr." in col["hint"]
        deadline = time.time() + 5
        while time.time() < deadline:
            row = client.get("/api/state").json()["files"][0]["row"]
            if "customer_no" in row["cells"]:
                break
            time.sleep(0.05)
        assert row["cells"]["customer_no"]["value"] == "40711"
        assert "Kd.-Nr." in json.dumps(server.requests[before]["body"])


def test_delete_column_keeps_cells(tmp_path, server):
    xml = write_samples(tmp_path)[2]
    with client_for(resolve()) as client:
        upload(client, xml)
        wait_done(client)
        assert client.delete("/api/columns/iban").status_code == 200
        state = client.get("/api/state").json()
        assert "iban" not in [c["key"] for c in state["columns"]]
        assert "iban" in state["files"][0]["row"]["cells"]
        assert client.delete("/api/columns/nope").status_code == 404


def test_patch_cell_marks_edited_and_rechecks(tmp_path, server):
    xml = write_samples(tmp_path)[2]
    with client_for(resolve()) as client:
        file_id = upload(client, xml).json()["files"][0]["id"]
        wait_done(client)
        res = client.patch(f"/api/files/{file_id}/cells/gross", json={"value": "500.00"})
        assert res.status_code == 200
        entry = client.get("/api/state").json()["files"][0]
    cell = entry["row"]["cells"]["gross"]
    assert cell["edited"] is True
    assert cell["source"] == "manual"
    assert cell["value"] == "500.00"
    assert cell["raw"] == "119.00"
    assert entry["status"] == "error"
    assert any(c["name"] == "totals" and c["level"] == "error" for c in entry["row"]["checks"])


def test_export_xlsx_download(tmp_path, server):
    xml = write_samples(tmp_path)[2]
    with client_for(resolve()) as client:
        upload(client, xml)
        wait_done(client)
        res = client.get("/api/export?format=xlsx")
        assert res.status_code == 200
        assert "invoices.xlsx" in res.headers["content-disposition"]
        ws = openpyxl.load_workbook(io.BytesIO(res.content))["Invoices"]
        assert ws.max_row == 2
        assert client.get("/api/export?format=txt").status_code == 400


def test_template_roundtrip(tmp_path, server):
    with client_for(resolve()) as client:
        client.post("/api/columns", json={"label": "Order No.", "hint": "PO number"})
        text = client.get("/api/template").text
        assert "Order No." in text
        client.delete("/api/columns/order_no")
        res = client.post("/api/template", files={"file": ("t.yaml", text.encode(), "application/x-yaml")})
        assert res.status_code == 200
        keys = [c["key"] for c in client.get("/api/state").json()["columns"]]
        assert "order_no" in keys
        bad = client.post("/api/template", files={"file": ("t.yaml", b"columns: 5", "application/x-yaml")})
        assert bad.status_code == 400


def test_unknown_id_404(server):
    with client_for(resolve()) as client:
        assert client.get("/api/files/nope/pages").status_code == 404
        assert client.get("/api/files/nope/pages/1.png").status_code == 404
        assert client.patch("/api/files/nope/cells/gross", json={"value": "1"}).status_code == 404


def test_upload_limit_413(tmp_path, server):
    paths = write_samples(tmp_path)
    with client_for(resolve(max_files=1)) as client:
        assert upload(client, paths[2], paths[1]).status_code == 413
        assert client.get("/api/state").json()["files"] == []
        assert upload(client, paths[2]).status_code == 200
        assert upload(client, paths[2]).status_code == 413
        assert client.delete("/api/files").status_code == 200
        assert client.get("/api/state").json()["files"] == []


def test_index_served(server):
    with client_for(resolve()) as client:
        res = client.get("/")
        assert res.status_code == 200
        assert "text/html" in res.headers["content-type"]
