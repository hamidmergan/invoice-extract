import io
import time
import zipfile

import openpyxl
import pytest
from pdfgen import write_samples

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from invoice_extract.columns import STANDARD_COLUMNS, Column  # noqa: E402
from invoice_extract.config import resolve  # noqa: E402
from invoice_extract.einvoice import LINE_COLUMNS  # noqa: E402
from invoice_extract.web.app import create_app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n"


def client_for(settings, columns=None):
    return TestClient(create_app(settings, columns=columns))


def upload(client, *paths):
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
    return client.post("/api/files", files=files)


def wait_done(client, timeout=10.0):
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
    assert entry["kind"] == "xml" and entry["status"] == "ok"
    assert entry["row"]["cells"]["invoice_no"]["value"] == "RE-2026-001"
    assert entry["row"]["path"] == "xrechnung.xml"
    assert [c["key"] for c in state["columns"]] == [c.key for c in STANDARD_COLUMNS]
    assert state["summary"] == {"files": 1, "to_check": 0, "reading": 0}
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


def test_page_png(tmp_path, server):
    pdf = write_samples(tmp_path)[0]
    with client_for(resolve()) as client:
        file_id = upload(client, pdf).json()["files"][0]["id"]
        assert client.get(f"/api/files/{file_id}/pages").json() == {"count": 1}
        png = client.get(f"/api/files/{file_id}/pages/1.png")
        assert png.status_code == 200
        assert png.headers["content-type"] == "image/png"
        assert png.content.startswith(PNG)
        assert client.get(f"/api/files/{file_id}/pages/2.png").status_code == 404


def test_export_xlsx_download(tmp_path, server):
    xml = write_samples(tmp_path)[2]
    with client_for(resolve()) as client:
        upload(client, xml)
        wait_done(client)
        res = client.get("/api/export?format=xlsx")
        assert res.status_code == 200
        assert "invoices.xlsx" in res.headers["content-disposition"]
        wb = openpyxl.load_workbook(io.BytesIO(res.content))
        assert wb["Invoices"].max_row == 2
        assert wb["Invoices"]["A2"].value == "xrechnung.xml"
        assert client.get("/api/export?format=txt").status_code == 400


def test_own_columns_with_a_table_export_csv_as_zip(tmp_path, server):
    xml = write_samples(tmp_path)[2]
    columns = [Column("gross", "Gross", type="amount", role="gross"),
               Column("line_items", "Line Items", kind="table", table_columns=list(LINE_COLUMNS))]
    with client_for(resolve(), columns) as client:
        upload(client, xml)
        state = wait_done(client)
        assert [c["key"] for c in state["columns"]] == ["gross", "line_items"]
        assert state["files"][0]["row"]["cells"]["line_items"]["value"][0]["Description"] == "Paper"
        file_id = state["files"][0]["id"]
        assert client.patch(f"/api/files/{file_id}/cells/line_items", json={"value": "x"}).status_code == 400
        res = client.get("/api/export?format=csv")
    assert res.headers["content-type"] == "application/zip"
    assert "invoices.zip" in res.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(res.content)).namelist()
    assert sorted(names) == ["invoices-line_items.csv", "invoices.csv"]
    assert server.requests == []


def test_patch_cell_marks_edited_and_rechecks(tmp_path, server):
    xml = write_samples(tmp_path)[2]
    with client_for(resolve()) as client:
        file_id = upload(client, xml).json()["files"][0]["id"]
        wait_done(client)
        res = client.patch(f"/api/files/{file_id}/cells/gross", json={"value": "500.00"})
        assert res.status_code == 200
        assert client.patch(f"/api/files/{file_id}/cells/file", json={"value": "x"}).status_code == 400
        assert client.patch(f"/api/files/{file_id}/cells/nope", json={"value": "x"}).status_code == 404
        entry = client.get("/api/state").json()["files"][0]
    cell = entry["row"]["cells"]["gross"]
    assert cell["edited"] is True and cell["source"] == "manual"
    assert cell["value"] == "500.00" and cell["raw"] == "119.00" and cell["label"] == "Gross"
    assert entry["status"] == "error"
    assert any(c["name"] == "totals" and c["level"] == "error" for c in entry["row"]["checks"])


def test_unknown_id_404(server):
    with client_for(resolve()) as client:
        assert client.get("/api/files/nope/pages").status_code == 404
        assert client.get("/api/files/nope/pages/1.png").status_code == 404
        assert client.patch("/api/files/nope/cells/gross", json={"value": "1"}).status_code == 404


def test_upload_limit_413_and_clear(tmp_path, server):
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
        assert "<title>invoice-extract</title>" in res.text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/style.css").status_code == 200
        assert client.get("/table").status_code == 404


def test_host_and_origin_guard(server):
    app = create_app(resolve(), allowed_hosts=["127.0.0.1", "localhost", "::1"])
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/state").status_code == 200
        assert client.get("/api/state", headers={"Host": "[::1]:8000"}).status_code == 200
        assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 400
        evil = {"Origin": "http://evil.example"}
        assert client.delete("/api/files", headers=evil).status_code == 403
        same = {"Origin": "http://127.0.0.1:8000"}
        assert client.delete("/api/files", headers=same).status_code == 200
    with client_for(resolve()) as client:
        evil = {"Origin": "http://evil.example"}
        assert client.delete("/api/files", headers=evil).status_code == 403


def test_document_route(tmp_path, server):
    paths = write_samples(tmp_path)
    with client_for(resolve()) as client:
        ids = {p.name: upload(client, p).json()["files"][0]["id"] for p in paths}
        wait_done(client)
        doc = client.get(f"/api/files/{ids['xrechnung.xml']}/document")
        assert doc.status_code == 200 and doc.json()["number"] == "RE-2026-001"
        assert client.get(f"/api/files/{ids['zugferd.pdf']}/document").json()["seller"]["name"] == "ACME GmbH"
        assert client.get(f"/api/files/{ids['digital.pdf']}/document").status_code == 404
        assert client.get(f"/api/files/{ids['scan.png']}/document").status_code == 404
        assert client.get("/api/files/nope/document").status_code == 404


def test_duplicates_across_uploads(tmp_path, server):
    xml = write_samples(tmp_path)[2]
    with client_for(resolve()) as client:
        upload(client, xml)
        second = upload(client, xml).json()["files"][0]["id"]
        state = wait_done(client)
        assert [f["status"] for f in state["files"]] == ["warning", "warning"]
        messages = [c["message"] for c in state["files"][0]["row"]["checks"]]
        assert "Same invoice number as xrechnung.xml" in messages
        assert client.patch(f"/api/files/{second}/cells/invoice_no", json={"value": "RE-2"}).status_code == 200
        state = client.get("/api/state").json()
        assert [f["status"] for f in state["files"]] == ["ok", "ok"]
