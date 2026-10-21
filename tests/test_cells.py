from pdfgen import make_pdf

from invoice_extract.cells import cell_from_item
from invoice_extract.pdf import locate

EMPTY = {"label": "", "kind": "value", "type": "text", "role": "", "value": None, "text": "", "columns": [],
         "rows": [], "source_text": "", "end_text": "", "page": None}


def item_of(**fields):
    return {**EMPTY, **fields}


def test_text_block_box_spans_lines(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(make_pdf([[(50, 700, "We debit 119.00 EUR"), (50, 686, "on 2026-11-20 from your account.")]]))
    item = item_of(label="Payment Note", kind="text", text="We debit 119.00 EUR on 2026-11-20 from your account.",
                   source_text="We debit", end_text="your account.", page=1)
    cell = cell_from_item(pdf, item, "text", "text", locate_boxes=True)
    first, last = locate(pdf, "We debit"), locate(pdf, "your account.")
    assert cell.kind == "text" and cell.label == "Payment Note" and cell.value.startswith("We debit")
    assert cell.box.y0 <= first.y0 and cell.box.y1 >= last.y1
    assert cell.checks == []


def test_table_box_covers_rows(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(make_pdf([[(50, 700, "Paper 100,00"), (50, 686, "Toner 19,00")]]))
    item = item_of(kind="table", columns=["Description", "Amount"],
                   rows=[{"Description": "Paper", "Amount": "100,00"}, {"Description": "Toner", "Amount": "19,00"}],
                   source_text="Paper 100,00", end_text="Toner 19,00", page=1)
    cell = cell_from_item(pdf, item, "text", "text", locate_boxes=True)
    assert cell.kind == "table" and cell.columns == ["Description", "Amount"] and len(cell.value) == 2
    assert cell.box.y1 >= locate(pdf, "Toner 19,00").y1


def test_block_not_located_warns(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(make_pdf([[(50, 700, "Hello")]]))
    cell = cell_from_item(pdf, item_of(kind="text", text="Nope", source_text="Nope", page=1), "text", "text", True)
    assert cell.box is None and [c.name for c in cell.checks] == ["source_not_located"]


def test_value_keeps_role_and_type(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(make_pdf([[(50, 700, "Total 119,00 EUR")]]))
    item = item_of(label="Gross", role="gross", type="amount", value="119.00", source_text="Total 119,00 EUR", page=1)
    cell = cell_from_item(pdf, item, "amount", "text", locate_boxes=True)
    assert str(cell.value) == "119.00" and cell.role == "gross" and cell.type == "amount" and cell.box is not None


def test_empty_item_gives_empty_cell(tmp_path):
    cell = cell_from_item(tmp_path / "x.pdf", item_of(kind="table", columns=["A"]), "text", "text", True)
    assert cell.value is None and cell.kind == "table" and cell.checks == []


def test_value_box_is_the_value_inside_its_source(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(make_pdf([[(50, 700, "Invoice RE-7 dated 2026-10-21")]]))
    item = item_of(label="Invoice No.", value="RE-7", source_text="Invoice RE-7 dated 2026-10-21", page=1)
    cell = cell_from_item(pdf, item, "text", "text", locate_boxes=True)
    whole = locate(pdf, "Invoice RE-7 dated 2026-10-21")
    assert cell.box is not None and cell.box.x0 > whole.x0 and cell.box.x1 < whole.x1


def test_value_box_ignores_a_source_without_the_value(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(make_pdf([[(50, 700, "Net VAT Gross"), (50, 650, "100,00 19,00 119,00")]]))
    item = item_of(label="Net", type="amount", value="100.00", source_text="Net VAT Gross", page=1)
    cell = cell_from_item(pdf, item, "amount", "text", locate_boxes=True)
    value = locate(pdf, "100,00")
    assert cell.box is not None and abs(cell.box.y0 - value.y0) < 0.005


def test_long_text_block_with_a_slip_is_still_located(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(make_pdf([[(50, 700, "Please note that this invoice counts as accepted"),
                               (50, 686, "unless you object within eight weeks of receipt.")]]))
    text = "Please note that this invoice counts as accepted unless you objct within eight weeks of receipt."
    item = item_of(kind="text", text=text, source_text=text, end_text="within eight weeks of receipt.", page=1)
    cell = cell_from_item(pdf, item, "text", "text", locate_boxes=True)
    assert cell.box is not None and cell.box.y1 >= locate(pdf, "of receipt.").y1
