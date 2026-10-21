import json
import sys

import openpyxl
from pdfgen import write_samples

from invoice_extract.cli import main
from invoice_extract.columns import STANDARD_COLUMNS


def llm_flags(server):
    return ["--base-url", server.base_url, "--model", "m"]


def test_cli_xml_only_no_model(tmp_path, server, capsys):
    paths = write_samples(tmp_path)
    out = tmp_path / "out.xlsx"
    assert main([str(paths[2]), "-o", str(out)]) == 0
    assert out.exists()
    assert server.requests == []
    err = capsys.readouterr().err
    assert "xrechnung.xml: ok" in err
    assert f"Wrote {out} (1 files, 0 to check)" in err


def test_cli_column_flag_and_template(tmp_path, server, capsys):
    paths = write_samples(tmp_path)
    template = tmp_path / "t.yaml"
    template.write_text("name: t\ncolumns:\n  - label: Supplier\n  - label: Gross\n    type: amount\n", encoding="utf-8")
    server.reply = lambda body: json.dumps({"order_no": {"value": "PO-1", "source_text": "PO-1", "page": 1}})
    out = tmp_path / "out.xlsx"
    code = main([str(paths[0]), "-o", str(out), "--template", str(template), "--column", "Order No."]
                + llm_flags(server))
    assert code == 0, capsys.readouterr().err
    body = json.dumps(server.requests[0]["body"])
    assert "order_no" in body and "supplier" in body
    ws = openpyxl.load_workbook(out)["Invoices"]
    assert [c.value for c in ws[1]] == ["Supplier", "Gross", "Order No."]


def test_cli_exit_code_on_error(tmp_path, server, capsys):
    good = write_samples(tmp_path)[2]
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 not really a pdf")
    out = tmp_path / "out.csv"
    server.reply = lambda body: '{"items": []}'
    assert main([str(good), str(broken), "-o", str(out)] + llm_flags(server)) == 1
    assert out.exists()
    err = capsys.readouterr().err
    assert "broken.pdf: error" in err
    assert "(2 files, 1 to check)" in err


def test_cli_missing_model_for_pdf(tmp_path, server, capsys):
    pdf = write_samples(tmp_path)[0]
    assert main([str(pdf), "-o", str(tmp_path / "out.xlsx")]) == 1
    err = capsys.readouterr().err
    assert "digital.pdf: error" in err and "--model" in err
    assert server.requests == []


def test_cli_errors_before_processing(tmp_path, server, capsys):
    paths = write_samples(tmp_path)
    assert main([str(p) for p in paths[1:3]] + ["--max-files", "1"]) == 2
    assert "Error: 2 files given" in capsys.readouterr().err
    assert main([str(paths[2]), "-o", str(tmp_path / "out.txt")]) == 2
    assert "Error: Unknown export format" in capsys.readouterr().err
    assert main([str(paths[2]), "--template", str(tmp_path / "missing.yaml")]) == 2
    assert "Error: Cannot read template" in capsys.readouterr().err
    assert not (tmp_path / "out.txt").exists()


def test_cli_serve_without_ui_extra(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    assert main(["serve"]) == 2
    assert 'pip install "invoice-extract[ui]"' in capsys.readouterr().err


def test_default_output_not_overwritten(tmp_path, server, monkeypatch, capsys):
    xml = write_samples(tmp_path / "in")[2]
    monkeypatch.chdir(tmp_path)
    (tmp_path / "invoices.xlsx").write_bytes(b"keep me")
    assert main([str(xml)]) == 0
    assert (tmp_path / "invoices.xlsx").read_bytes() == b"keep me"
    assert (tmp_path / "invoices (1).xlsx").exists()
    assert main([str(xml)]) == 0
    assert (tmp_path / "invoices (2).xlsx").exists()


def test_cli_missing_output_folder_fails_before_processing(tmp_path, server, capsys):
    pdf = write_samples(tmp_path)[0]
    out = tmp_path / "missing" / "out.xlsx"
    assert main([str(pdf), "-o", str(out)] + llm_flags(server)) == 2
    assert "Error: Output folder does not exist" in capsys.readouterr().err
    assert server.requests == []


def test_cli_serve_guards_host_on_loopback(monkeypatch):
    import types

    from fastapi.testclient import TestClient
    started = {}
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda app, **kw: started.update(app=app)))
    assert main(["serve"]) == 0
    client = TestClient(started["app"], base_url="http://127.0.0.1:8000")
    assert client.get("/api/state").status_code == 200
    assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 400


def test_cli_serve_passes_template(monkeypatch, tmp_path):
    import types
    from fastapi.testclient import TestClient
    template = tmp_path / "t.yaml"
    template.write_text("name: t\ncolumns:\n  - label: Gross\n    type: amount\n", encoding="utf-8")
    started = {}
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda app, **kw: started.update(app=app)))
    assert main(["serve", "--template", str(template)]) == 0
    client = TestClient(started["app"], base_url="http://127.0.0.1:8000")
    assert [c["key"] for c in client.get("/api/state").json()["columns"]] == ["gross"]
    assert main(["serve", "--template", str(tmp_path / "missing.yaml")]) == 2


def test_cli_default_columns_are_standard(tmp_path, server, capsys):
    xml = write_samples(tmp_path)[2]
    out = tmp_path / "out.xlsx"
    assert main([str(xml), "-o", str(out)]) == 0, capsys.readouterr().err
    ws = openpyxl.load_workbook(out)["Invoices"]
    assert [c.value for c in ws[1]] == [c.label for c in STANDARD_COLUMNS]
    assert server.requests == []


def test_cli_einvoice(tmp_path, server, capsys):
    from test_einvoicing_convert import reply
    pdf = write_samples(tmp_path)[0]
    server.reply = reply()
    profile = tmp_path / "company.yaml"
    profile.write_text("seller:\n  name: My Shop GmbH\n", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    code = main(["einvoice", str(pdf), "-o", str(out), "--profile", str(profile)] + llm_flags(server))
    assert code == 0, capsys.readouterr().err
    assert (out / "digital.xml").exists()
    assert b"My Shop GmbH" in (out / "digital.xml").read_bytes()
    assert "digital.pdf: written" in capsys.readouterr().err


def test_cli_einvoice_problems(tmp_path, server, capsys):
    from test_einvoicing_convert import reply
    pdf = write_samples(tmp_path)[0]
    server.reply = reply(buyer_email=None)
    assert main(["einvoice", str(pdf), "-o", str(tmp_path)] + llm_flags(server)) == 1
    err = capsys.readouterr().err
    assert "digital.pdf: 1 problem" in err and "buyer.email" in err
    assert main(["einvoice", str(pdf), "--profile", str(tmp_path / "missing.yaml")] + llm_flags(server)) == 2


def test_cli_einvoice_same_names_do_not_overwrite(tmp_path, server, capsys):
    from test_einvoicing_convert import reply
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, second = tmp_path / "a" / "x.pdf", tmp_path / "b" / "x.pdf"
    sample = write_samples(tmp_path)[0].read_bytes()
    first.write_bytes(sample)
    second.write_bytes(sample)
    server.reply = reply()
    out = tmp_path / "out"
    out.mkdir()
    assert main(["einvoice", str(first), str(second), "-o", str(out)] + llm_flags(server)) == 0
    assert sorted(p.name for p in out.iterdir()) == ["x (2).xml", "x.xml"]


def test_cli_einvoice_write_error_does_not_stop_the_batch(tmp_path, server, capsys, monkeypatch):
    from test_einvoicing_convert import reply
    import invoice_extract.cli as cli
    paths = [tmp_path / "one.pdf", tmp_path / "two.pdf"]
    for p in paths:
        p.write_bytes(write_samples(tmp_path)[0].read_bytes())
    server.reply = reply()
    real = cli.write
    calls = []

    def flaky(conv, fmt, out_dir, name=None):
        calls.append(conv.path)
        if len(calls) == 1:
            raise RuntimeError("disk full")
        return real(conv, fmt, out_dir, name)
    monkeypatch.setattr(cli, "write", flaky)
    assert main(["einvoice", *map(str, paths), "-o", str(tmp_path)] + llm_flags(server)) == 1
    err = capsys.readouterr().err
    assert "one.pdf: error: RuntimeError: disk full" in err and "two.pdf: written" in err
