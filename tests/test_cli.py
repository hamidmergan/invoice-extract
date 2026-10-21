import json
import sys

import openpyxl
from pdfgen import write_samples

from invoice_extract.cli import main


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
