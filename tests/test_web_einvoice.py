import time

import pytest
from pdfgen import write_samples
from test_einvoicing_convert import reply

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from invoice_extract.config import resolve  # noqa: E402
from invoice_extract.einvoice import parse_xml  # noqa: E402
from invoice_extract.web.app import create_app  # noqa: E402


def client_for(server):
    return TestClient(create_app(resolve(base_url=server.base_url, model="m", max_retries=0)))


def upload(client, *paths):
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
    return client.post("/api/e/files", files=files)


def wait_done(client, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get("/api/e/state").json()
        if state["files"] and all(f["state"] == "done" for f in state["files"]):
            return state
        time.sleep(0.05)
    raise AssertionError("not done in time")


def test_upload_edit_and_download(tmp_path, server):
    server.reply = reply(buyer_email=None)
    with client_for(server) as client:
        file_id = upload(client, write_samples(tmp_path)[0]).json()["files"][0]["id"]
        entry = wait_done(client)["files"][0]
        assert entry["invoice"]["number"] == "R-2026-001" and entry["invoice"]["seller"]["name"] == "ACME GmbH"
        assert [i["field"] for i in entry["issues"]["xrechnung"] if i["level"] == "error"] == ["buyer.email"]
        assert entry["totals"]["grand_total"] == "119.00"
        assert "issue_date" in entry["boxes"] and "seller.country" in entry["boxes"]
        bad = client.get(f"/api/e/files/{file_id}/download?format=xrechnung")
        assert bad.status_code == 409 and "buyer.email" in bad.text
        invoice = entry["invoice"]
        invoice["buyer"]["email"] = "ap@beta.example"
        res = client.put(f"/api/e/files/{file_id}", json={"invoice": invoice})
        assert res.status_code == 200 and res.json()["issues"]["xrechnung"] == []
        xml = client.get(f"/api/e/files/{file_id}/download?format=xrechnung")
        assert xml.status_code == 200 and "digital.xml" in xml.headers["content-disposition"]
        assert parse_xml(xml.content)["gross"] == "119.00"
        pdf = client.get(f"/api/e/files/{file_id}/download?format=zugferd")
        assert pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
        invoice["issue_date"] = "31.02.2026"
        bad = client.put(f"/api/e/files/{file_id}", json={"invoice": invoice})
        assert bad.status_code == 400 and "issue_date" in bad.text


def test_profile_endpoints(tmp_path, server):
    server.reply = reply()
    with client_for(server) as client:
        upload(client, write_samples(tmp_path)[0])
        wait_done(client)
        res = client.put("/api/e/profile", json={"seller": {"name": "My Shop GmbH"}, "iban": "", "bic": "",
                                                 "payment_terms": ""})
        assert res.status_code == 200
        state = client.get("/api/e/state").json()
        assert state["profile"]["seller"]["name"] == "My Shop GmbH"
        assert state["files"][0]["invoice"]["seller"]["name"] == "My Shop GmbH"
        text = client.get("/api/e/profile.yaml").text
        assert "My Shop GmbH" in text
        loaded = client.post("/api/e/profile", files={"file": ("p.yaml", b"seller:\n  name: Other Ltd\n", "text/yaml")})
        assert loaded.status_code == 200 and client.get("/api/e/state").json()["profile"]["seller"]["name"] == "Other Ltd"
        assert client.put("/api/e/profile", json={"colour": "red"}).status_code == 400


def test_einvoice_input_unknown_id_and_clear(tmp_path, server):
    with client_for(server) as client:
        upload(client, write_samples(tmp_path)[2])
        entry = wait_done(client)["files"][0]
        assert entry["error"] == "Already an e-invoice" and server.requests == []
        assert client.get("/api/e/files/nope/download?format=xrechnung").status_code == 404
        assert client.put("/api/e/files/nope", json={"invoice": {}}).status_code == 404
        assert client.get(f"/api/files/{entry['id']}/pages").json() == {"count": 0}
        assert client.delete("/api/e/files").status_code == 200
        assert client.get("/api/e/state").json()["files"] == []


def test_page_png_for_einvoice_files(tmp_path, server):
    server.reply = reply()
    with client_for(server) as client:
        file_id = upload(client, write_samples(tmp_path)[0]).json()["files"][0]["id"]
        wait_done(client)
        assert client.get(f"/api/files/{file_id}/pages").json() == {"count": 1}
        assert client.get(f"/api/files/{file_id}/pages/1.png").headers["content-type"] == "image/png"


def test_guard_applies(server):
    app = create_app(resolve(), allowed_hosts=["127.0.0.1"])
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/e/state", headers={"Host": "evil.example"}).status_code == 400
        assert client.put("/api/e/profile", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
