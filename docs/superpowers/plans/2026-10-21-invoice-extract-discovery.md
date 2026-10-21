# invoice-extract v2 (discovery) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The model finds and names every value, text block and table on each invoice, merges equal fields across invoices, and the user only picks which fields go into the export (layout C), with templates for later runs.

**Architecture:** A new discovery path sits next to the v1 column path. Per file, `discover_file` turns one LLM answer (or the e-invoice XML) into cells keyed by role or label. `merge_rows` then unifies keys across files with one label-only LLM request. Fields are derived from the rows (`fields_of`), so the library API stays `List[Row]`. The template path (v1 `fill_columns`) learns the kinds `text` and `table`. Export gets one sheet per table field. The web workspace holds the fields, a selection, and a mode (`discover` or `template`).

**Tech Stack:** Python 3.9+, openai, pypdfium2, Pillow, openpyxl, PyYAML, defusedxml, doc-extract-llm; FastAPI and Uvicorn for the UI; plain HTML/JS/CSS; pytest with the local fake OpenAI server in `tests/conftest.py`.

**Spec:** `docs/superpowers/specs/2026-10-21-invoice-extract-discovery-design.md` (v2). It builds on `docs/superpowers/specs/2026-10-21-invoice-extract-design.md` (v1). Read both.

## Global Constraints

- Python >=3.9: `Optional`/`List`/`Dict`, no `X | Y`, no `match`.
- AI only through an OpenAI-compatible API (`base_url`, `model`, optional `api_key`); no provider-specific code; no default `base_url`/`model`.
- E-invoices (XRechnung, ZUGFeRD) work without any model and then make no HTTP request.
- Everything outward-facing is English: code, comments, messages, labels the model is asked to produce, README, tests, commits.
- Every pdfium call runs under `PDFIUM_LOCK` (use the `@serialized` helpers in `pdf.py`; never open a `PdfDocument` elsewhere).
- Export stays formula-safe: every written string goes through `export._put` / `_csv_text`.
- No real invoices in the repo; fixtures come from `tests/pdfgen.py`. The internal lab server URL never appears in the repo (checked before every push).
- v1 callers that pass `columns` keep working unchanged.
- Test command: `.venv/bin/pytest -q` (limit output with `| tail -n 50`).

## Review Focus

1. **Duplicate labels on one invoice** (the model returns "Date" twice): both values are kept under unique keys (`date`, `date_2`); nothing is overwritten. Test in Task 4.
2. **The merge would put two items of one invoice into one field** ("VAT 7 %" and "VAT 19 %" both mapped to "VAT"): they stay separate fields. Test in Task 5.
3. **Ragged or odd table rows from the model** (missing cells, extra cells, numbers or null instead of strings): rows are padded or cut to the columns, values become strings, and export never crashes. Tests in Tasks 2 and 7.
4. **Field labels that are illegal or too long as Excel sheet names** (`[]:*?/\`, more than 31 characters, two tables with the same name): sheet titles are cleaned, cut and made unique. Test in Task 7.
5. **A v1 template (no `kind`) and e-invoices without a model in template mode**: the template loads as value fields; an e-invoice stack runs without any HTTP request. Tests in Tasks 1 and 6.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/invoice_extract/columns.py` | modify | `Column` gains `kind`, `role`, `table_columns`; `KINDS`, `ROLES`, `FILE_COLUMN`, `STATUS_COLUMN`, `export_columns()`; template v2 fields |
| `src/invoice_extract/results.py` | modify | `Cell` gains `kind`, `label`, `role`, `type`, `columns`; `to_dict` carries them |
| `src/invoice_extract/llm.py` | modify | `discover_items`, kinds in `fill_columns`, `merge_labels`, `merge_table_columns` |
| `src/invoice_extract/pdf.py` | modify | `union_box`, `locate_span` |
| `src/invoice_extract/cells.py` | create | `cell_from_item`: one LLM item to one `Cell` with normalization and boxes (used by both paths) |
| `src/invoice_extract/einvoice.py` | modify | `parse_line_items`, `LINE_COLUMNS` |
| `src/invoice_extract/discover.py` | create | `discover_file`, `merge_rows`, `fields_of`, `field_counts` |
| `src/invoice_extract/core.py` | modify | `extract`/`extract_many` with `columns=None` means discovery; `_llm_cells` uses `cell_from_item` |
| `src/invoice_extract/export.py` | modify | table sheets, CSV side files, JSON with selected fields only |
| `src/invoice_extract/cli.py` | modify | discovery by default |
| `src/invoice_extract/web/app.py` | modify | fields, selection, merge job, template mode, ZIP export; remove the column endpoints |
| `src/invoice_extract/web/static/*` | rewrite | layout C |
| `README.md`, `CHANGELOG.md` | modify | v2 behaviour |

---

### Task 1: Data model and templates

**Files:**
- Modify: `src/invoice_extract/columns.py`, `src/invoice_extract/results.py`, `src/invoice_extract/__init__.py`
- Test: `tests/test_columns.py`, `tests/test_results.py`

**Interfaces:**
- Produces:
  - `KINDS = ("value", "text", "table")`
  - `ROLES = ("supplier", "invoice_no", "invoice_date", "due_date", "net", "vat", "gross", "currency", "iban")`. Each role equals the key of the `STANDARD_COLUMNS` entry with the same meaning.
  - `Column(key, label, hint="", required=False, type="text", kind="value", role="", table_columns=[])`
  - `FILE_COLUMN`, `STATUS_COLUMN`: the `file`/`status` entries of `STANDARD_COLUMNS`.
  - `role_column(role: str) -> Column`: a copy of the standard column for that role, with `role` set.
  - `export_columns(fields: List[Column]) -> List[Column]`: `[FILE_COLUMN] + fields + [STATUS_COLUMN]`.
  - `Cell(..., kind="value", label="", role="", type="text", columns=[])`. For `kind="table"`, `value` is `List[Dict[str, str]]` and `columns` lists the column names in order. For `kind="text"`, `value` is the whole text.
  - `Row.to_dict()` cell entries also carry `kind`, `label`, `role`, `type`, `columns`; table values are lists of dicts.
  - `Template.loads`/`dumps` read and write `kind`, `role`, `table_columns`.

- [ ] **Step 1: Write the failing tests**

```python
def test_v1_template_loads_as_values():
    t = Template.loads("name: t\ncolumns:\n  - label: Order No.\n")
    assert t.columns[0].kind == "value" and t.columns[0].role == "" and t.columns[0].table_columns == []

def test_template_v2_roundtrip():
    cols = [Column("net", "Net", type="amount", role="net"),
            Column("payment_note", "Payment Note", kind="text"),
            Column("line_items", "Line Items", kind="table", table_columns=["Description", "Amount"])]
    back = Template.loads(Template("t", cols).dumps()).columns
    assert back == cols

def test_template_rejects_unknown_kind_and_role():
    with pytest.raises(ConfigError, match="kind"):
        Template.loads("columns:\n  - label: X\n    kind: picture\n")
    with pytest.raises(ConfigError, match="role"):
        Template.loads("columns:\n  - label: X\n    role: boss\n")

def test_export_columns_and_role_column():
    assert [c.key for c in export_columns([role_column("gross")])] == ["file", "gross", "status"]
    assert role_column("gross").label == "Gross" and role_column("gross").type == "amount"

def test_table_cell_to_dict():
    row = Row("a.pdf", cells={"line_items": Cell(kind="table", label="Line Items", columns=["Description"],
                                                 value=[{"Description": "Paper"}])})
    cell = row.to_dict()["cells"]["line_items"]
    assert cell["kind"] == "table" and cell["columns"] == ["Description"] and cell["value"] == [{"Description": "Paper"}]
```

- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_columns.py tests/test_results.py`. Expected: FAIL (`unexpected keyword argument 'kind'`).
- [ ] **Step 3: Implement** the fields and helpers above. Validate `kind` in `KINDS` and `role` in `ROLES` or `""` with `ConfigError` messages that contain "kind" or "role". Export `KINDS`, `ROLES`, `export_columns` and `role_column` from `__init__`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: add field kinds and roles to columns, cells and templates`

---

### Task 2: LLM discovery, kinds in fill_columns, merge requests

**Files:**
- Modify: `src/invoice_extract/llm.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- Consumes: `Column` with `kind`/`table_columns` (Task 1), `ROLES`, `KINDS`, `TYPES`.
- Produces:
  - `discover_items(client, model: str, text: str) -> List[dict]`. Each item dict has exactly these keys: `label: str`, `kind: str`, `type: str`, `role: str`, `value: Any`, `text: str`, `columns: List[str]`, `rows: List[Dict[str, str]]`, `source_text: str`, `end_text: str`, `page: Optional[int]`.
  - `fill_columns(...)` returns the same item shape per key (or `None`). The v1 keys `value`, `source_text` and `page` keep their meaning.
  - `merge_labels(client, model: str, labels: List[dict], known: List[str]) -> Dict[str, str]`. The input entries are `{"label", "kind", "type", "examples": [str, ...]}`. The result maps every input label to a field name; a missing or non-string answer maps a label to itself.
  - `merge_table_columns(client, model: str, names: List[str]) -> Dict[str, str]`, with the same fallback.
- The model answers `{"items": [...]}` for discovery and `{"mapping": {...}}` for merges. The prompts tell it:
  - labels are short English names, whatever the invoice language;
  - `role` is set only for the invoice's single total or main value of that meaning;
  - text blocks give `source_text` and `end_text` as the first and last words, exactly as printed;
  - tables give `columns` and `rows`, with the first and last row as printed in `source_text`/`end_text`;
  - in merges, two labels get the same name only when they mean the same thing.

- [ ] **Step 1: Write the failing tests** (fake server, `client_for` as in the file):

```python
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

def test_discover_items_sloppy(server):  # Review Focus 3
    server.reply = lambda body: json.dumps({"items": [
        {"label": "", "kind": "value", "value": "x"},                       # no label: dropped
        {"label": "Pic", "kind": "picture"},                                # unknown kind: dropped
        {"label": "Ref", "kind": "value", "type": "money", "role": "boss", "value": "A", "page": "one"},
        {"label": "T", "kind": "table", "columns": ["A", "B"], "rows": [["1"], ["1", "2", "3"], None]},
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
    assert out["note"]["text"] == "Pay soon."
    assert out["lines"]["rows"] == [{"Description": "Paper", "Amount": "100,00"}]
    sent = server.requests[0]["body"]["messages"][-1]["content"]
    assert '"kind": "table"' in sent and "Description" in sent

def test_merge_labels(server):
    server.reply = lambda body: json.dumps({"mapping": {"Kd.-Nr.": "Customer No.", "Customer number": "Customer No.",
                                                        "Odd": 5}})
    labels = [{"label": l, "kind": "value", "type": "text", "examples": ["1"]}
              for l in ("Kd.-Nr.", "Customer number", "Odd", "Missing")]
    out = merge_labels(client_for(server), "m", labels, known=["Customer No."])
    assert out == {"Kd.-Nr.": "Customer No.", "Customer number": "Customer No.", "Odd": "Odd", "Missing": "Missing"}
    assert "Customer No." in json.dumps(server.requests[0]["body"])

def test_merge_table_columns(server):
    server.reply = lambda body: json.dumps({"mapping": {"Betrag EUR": "Amount"}})
    assert merge_table_columns(client_for(server), "m", ["Betrag EUR", "Amount"]) == {"Betrag EUR": "Amount",
                                                                                       "Amount": "Amount"}
```

- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_llm.py`. Expected: FAIL (`cannot import name 'discover_items'`).
- [ ] **Step 3: Implement** in `llm.py`:
  - Rows may come as lists (aligned to `columns`) or dicts. Normalize each row to `Dict[str, str]` over `columns`: pad missing cells with `""`, drop extra cells, turn `None` rows into nothing, and turn non-string cells into `str` (`None` becomes `""`).
  - Unknown `type` becomes `"text"`. Unknown `role` becomes `""`. A non-int or bool `page` becomes `None`. Items without a label or with an unknown kind are dropped.
  - `fill_columns` builds the skeleton per kind: values as today, text as `{"text": null, "source_text": "", "end_text": "", "page": null}`, tables as `{"rows": [], "source_text": "", "end_text": "", "page": null}`. It returns the same normalized item dict, taking a table's `columns` from `Column.table_columns`.
  - Reuse `_chat` and `_json_object`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Live prompt check** (only if `curl -s localhost:11434/api/tags` answers):
  - Run `discover_items` on `_pdf_text("samples/digital.pdf")` with `qwen2.5:7b`. Expected: at least the roles `invoice_no`, `invoice_date`, `net`, `vat`, `gross` and `iban`, and no `ModelError`.
  - If the model fails, improve the prompt (an answer skeleton with one example item of each kind helped in v1) before committing.
  - Record the result in the ledger.
- [ ] **Step 6: Commit:** `feat: ask the model for all items and for label merges`

---

### Task 3: One item to one cell, with boxes for blocks and tables

**Files:**
- Create: `src/invoice_extract/cells.py`
- Modify: `src/invoice_extract/pdf.py`, `src/invoice_extract/core.py` (`_llm_cells` delegates to `cell_from_item`)
- Test: `tests/test_cells.py`, `tests/test_pdf.py`

**Interfaces:**
- Consumes: the item dict (Task 2), `normalize`, `display_variants`, `locate`.
- Produces:
  - `pdf.union_box(a: Box, b: Box) -> Box`: same page, smallest box covering both.
  - `pdf.locate_span(path, start: str, end: str, page: Optional[int] = None) -> Optional[Box]`: the box of `start`. If `end` is found on the same page at or after it, the union of both boxes.
  - `cells.cell_from_item(path, item: dict, type: str, source: str, locate_boxes: bool) -> Cell`. `type` is the column type for values. The cell's `kind`, `label` (`item["label"]`, may be `""`), `role`, `type`, `columns` and `raw` (`source_text`) are set.
    - **Values:** v1 behaviour, with the source-text box and the value fallback that `_llm_cells` has today.
    - **Text and tables:** `value` is the text or rows, the box comes from `locate_span(source_text, end_text)`, and there is no normalization.
    - **No box when `locate_boxes` is true:** the cell gets the `source_not_located` warning, as today.
    - **Empty item** (value `None`/`""`, empty text, no rows): `Cell(kind=..., source=source)` with no checks.

- [ ] **Step 1: Write the failing tests** on generated PDFs:

```python
def test_text_block_box_spans_lines(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(make_pdf([[(50, 700, "We debit 119.00 EUR"), (50, 686, "on 2026-11-20 from your account.")]]))
    item = item_of(kind="text", text="We debit 119.00 EUR on 2026-11-20 from your account.",
                   source_text="We debit", end_text="your account.", page=1)
    cell = cell_from_item(pdf, item, "text", "text", locate_boxes=True)
    first, last = locate(pdf, "We debit"), locate(pdf, "your account.")
    assert cell.kind == "text" and cell.value.startswith("We debit")
    assert cell.box.y0 <= first.y0 and cell.box.y1 >= last.y1 and cell.checks == []

def test_table_box_covers_rows(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(make_pdf([[(50, 700, "Paper 100,00"), (50, 686, "Toner 19,00")]]))
    item = item_of(kind="table", columns=["Description", "Amount"],
                   rows=[{"Description": "Paper", "Amount": "100,00"}, {"Description": "Toner", "Amount": "19,00"}],
                   source_text="Paper 100,00", end_text="Toner 19,00", page=1)
    cell = cell_from_item(pdf, item, "text", "text", locate_boxes=True)
    assert cell.columns == ["Description", "Amount"] and len(cell.value) == 2
    assert cell.box.y1 >= locate(pdf, "Toner 19,00").y1

def test_block_not_located_warns(tmp_path):
    pdf = tmp_path / "t.pdf"
    pdf.write_bytes(make_pdf([[(50, 700, "Hello")]]))
    cell = cell_from_item(pdf, item_of(kind="text", text="Nope", source_text="Nope", page=1), "text", "text", True)
    assert cell.box is None and [c.name for c in cell.checks] == ["source_not_located"]
```

  - `item_of(**kw)` is a test helper that fills the missing item keys with their empty defaults.
  - Add `test_union_box` to `tests/test_pdf.py`: two boxes give the min/max corners.

- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_cells.py tests/test_pdf.py`. Expected: FAIL (`No module named 'invoice_extract.cells'`).
- [ ] **Step 3: Implement** `union_box`, `locate_span` (decorate with `@serialized` if it opens the PDF itself) and `cell_from_item`. Then reduce `core._llm_cells` to a loop over `fill_columns` that calls `cell_from_item(path, item, col.type, source, locate_boxes)`. Empty items still become `Cell(source=source)`.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass, including the v1 core tests.
- [ ] **Step 5: Commit:** `feat: build cells for values, text blocks and tables`

---

### Task 4: Discovery per file (PDF, scan, e-invoice)

**Files:**
- Create: `src/invoice_extract/discover.py`
- Modify: `src/invoice_extract/einvoice.py`, `tests/pdfgen.py` (one line item in `cii_xml`/`ubl_xml`)
- Test: `tests/test_discover.py`, `tests/test_einvoice.py`

**Interfaces:**
- Consumes:
  - `discover_items` (Task 2) and `cell_from_item` (Task 3);
  - from `core`: `detect`, `_pdf_text`, `_transcribe`, the scan box logic in `_scan_cells`, `ClientGetter`;
  - from `einvoice`: `parse_xml`, `xml_text`, `embedded_xml`;
  - from `columns`: `role_column`, `make_key`.
- Produces:
  - `einvoice.LINE_COLUMNS = ["Description", "Quantity", "Unit Price", "Amount"]`
  - `einvoice.parse_line_items(data: bytes) -> List[Dict[str, str]]`: CII `IncludedSupplyChainTradeLineItem` and UBL `InvoiceLine`/`CreditNoteLine`, matched by local name like the existing helpers.
  - `discover.discover_file(path, settings: Settings, client: ClientGetter) -> Row`.
  - Key rules inside one row:
    - an item with a role gets key = role and the standard label;
    - every other item gets `make_key(label, existing)`, so duplicate labels get `_2`, `_3`;
    - if two items on one invoice claim the same role, both lose it, get label keys, and the row gets `Check("role_conflict", "warning", "Several values look like <Label>; checks that need it were skipped")`.
  - `discover_file` ends with `row.checks = row.checks + run_checks(row, [])`, so the totals, IBAN and date checks run on the role keys.
  - **E-invoice:**
    - XML fields become value cells with roles and `source="xml"`, with v1's ZUGFeRD box lookup;
    - line items become a table cell under key `line_items`, label "Line Items", columns `LINE_COLUMNS`;
    - with `settings.base_url` and `settings.model` set, `discover_items` also runs on the XML text (plus the PDF text for ZUGFeRD), and its items are added only for keys the XML did not fill;
    - without a model, no request is made.
  - **Scan:** `_transcribe`, then `discover_items` on the Markdown, then the vision boxes for value cells as `_scan_cells` does (factor the box loop out of `_scan_cells` into `core._scan_boxes(path, cells, settings, client)` and call it from both).

- [ ] **Step 1: Write the failing tests:**

```python
def test_xrechnung_without_model_has_roles_and_lines(tmp_path, server):
    row = discover_file(write_samples(tmp_path)[2], resolve(), _lazy_client(resolve(), None))
    assert row.cells["gross"].value == Decimal("119.00") and row.cells["gross"].role == "gross"
    assert row.cells["line_items"].kind == "table" and row.cells["line_items"].value[0]["Description"]
    assert server.requests == []

def test_xml_wins_over_discovery(tmp_path, server):
    server.reply = lambda body: json.dumps({"items": [
        {"label": "Gross", "kind": "value", "type": "amount", "role": "gross", "value": "1.00", "page": 1},
        {"label": "Customer No.", "kind": "value", "value": "K-1", "source_text": "K-1", "page": 1}]})
    s = resolve(base_url=server.base_url, model="m")
    row = discover_file(write_samples(tmp_path)[2], s, _lazy_client(s, None))
    assert row.cells["gross"].value == Decimal("119.00") and row.cells["customer_no"].value == "K-1"

def test_pdf_duplicate_labels_and_roles(tmp_path, server):  # Review Focus 1
    server.reply = lambda body: json.dumps({"items": [
        {"label": "Date", "kind": "value", "type": "date", "value": "2026-10-21", "source_text": "Date 2026-10-21"},
        {"label": "Date", "kind": "value", "type": "date", "value": "2026-11-20", "source_text": "Due 2026-11-20"},
        {"label": "Total", "kind": "value", "type": "amount", "role": "gross", "value": "119.00",
         "source_text": "Total 119.00 EUR"}]})
    s = resolve(base_url=server.base_url, model="m")
    row = discover_file(write_samples(tmp_path)[0], s, _lazy_client(s, None))
    assert {"date", "date_2", "gross"} <= set(row.cells)
    assert row.cells["gross"].label == "Gross" and row.cells["date_2"].label == "Date"

def test_role_conflict(tmp_path, server):
    server.reply = lambda body: json.dumps({"items": [
        {"label": "VAT 7 %", "kind": "value", "type": "amount", "role": "vat", "value": "7.00"},
        {"label": "VAT 19 %", "kind": "value", "type": "amount", "role": "vat", "value": "19.00"}]})
    s = resolve(base_url=server.base_url, model="m")
    row = discover_file(write_samples(tmp_path)[0], s, _lazy_client(s, None))
    assert "vat" not in row.cells and {"vat_7", "vat_19"} <= set(row.cells)
    assert [c.name for c in row.checks if c.level != "ok"] == ["role_conflict"]

def test_parse_line_items_cii_and_ubl():
    expected = [{"Description": "Paper", "Quantity": "10", "Unit Price": "10.00", "Amount": "100.00"}]
    assert parse_line_items(cii_xml()) == expected and parse_line_items(ubl_xml()) == expected
```

  Add `test_scan_discovery_boxes` using `is_vision` from `conftest`, modelled on the existing scan test in `tests/test_core.py`: a value item on `samples/scan.png` gets a box from the vision reply.
- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_discover.py tests/test_einvoice.py`. Expected: FAIL (`No module named 'invoice_extract.discover'`).
- [ ] **Step 3: Implement** as specified in the Interfaces block. Use `make_key` for `vat_7` and `vat_19`; `make_key("VAT 7 %")` must give `vat_7` (check it; adjust the test's expected keys to `make_key`'s output if it differs, never the reverse).
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: discover all fields per invoice`

---

### Task 5: Merge across invoices and the field list

**Files:**
- Modify: `src/invoice_extract/discover.py`
- Test: `tests/test_discover.py`

**Interfaces:**
- Consumes: `merge_labels`, `merge_table_columns` (Task 2), rows from `discover_file`.
- Produces:
  - `merge_rows(rows: List[Row], settings: Settings, client: ClientGetter, known: Sequence[Column] = ()) -> None`. It renames cell keys and labels in place.
    - **Which cells:** only cells without a role. Error rows are skipped.
    - **No model, or no label-only cells:** no request is made.
    - **Request:** one `merge_labels` call over the distinct labels (kind, type, up to two example values each); `known` labels are passed as `known`.
    - **New key:** the key of a known field with that label, otherwise `make_key` of the mapped label. Equal mapped labels across rows give the same key.
    - **Hard rule:** within one row, if two cells would get the same key, both keep their pre-merge key.
    - **Tables:** for each table key, one `merge_table_columns` call over the union of its column names; rows and `Cell.columns` are renamed, without duplicating a column.
    - **Failure:** on `ModelError` keys stay as they are, and each involved row gets `Check("merge_failed", "warning", "Fields could not be merged across invoices")`.
  - `fields_of(rows: List[Row], known: Sequence[Column] = ()) -> List[Column]`:
    - one `Column` per cell key, excluding `file`/`status`;
    - label, kind, type and role come from the first cell with that key;
    - `table_columns` is the ordered union of the cells' columns;
    - order: known fields first, then by count (descending), then first appearance.
  - `field_counts(rows: List[Row]) -> Dict[str, int]`: in how many error-free rows each key has a non-empty value.
  - `with_hints(fields: List[Column], rows: List[Row]) -> List[Column]`: copies of the fields. A field without a hint gets `Example values: "<v1>", "<v2>".` from up to two non-empty values (text blocks cut to 60 characters, tables: `Columns: <names>.`). This is the hint a saved template carries.

- [ ] **Step 1: Write the failing tests** (build rows directly with `Row`/`Cell` and the fake server):
  - `test_merge_maps_labels_across_rows`: "Kd.-Nr." in row A and "Customer number" in row B, both mapped to "Customer No." Both rows end up with key `customer_no` and label "Customer No.".
  - `test_merge_keeps_same_invoice_items_apart` (Review Focus 2): row A has "VAT 7 %" and "VAT 19 %", both mapped to "VAT". Row A keeps keys `vat_7` and `vat_19`.
  - `test_merge_uses_known_keys`: with `known=[Column("cust", "Customer No.")]`, "Kunden-Nr." mapped to "Customer No." gets key `cust`.
  - `test_merge_failure_warns`: with `server.status = 500`, keys are unchanged and `"merge_failed"` is in each row's check names.
  - `test_merge_without_model_makes_no_request`: `resolve()` without a model gives `server.requests == []`.
  - `test_merge_table_columns_per_table`: tables with columns `["Artikel", "Betrag EUR"]` and `["Description", "Amount"]`, mapping `{"Artikel": "Description", "Betrag EUR": "Amount"}`. Both rows end up with `columns == ["Description", "Amount"]` and renamed row dicts.
  - `test_with_hints`: a value field gets `Example values: "K-1", "K-2".`, a table field gets `Columns: Description, Amount.`, and an existing hint is kept.
  - `test_fields_of_order_and_counts`: three rows; `field_counts` counts as expected; `fields_of(rows, known=[...])` lists the known field first and the rest by count.
- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_discover.py`. Expected: FAIL (`cannot import name 'merge_rows'`).
- [ ] **Step 3: Implement** as specified.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: merge fields across invoices`

---

### Task 6: Library API

**Files:**
- Modify: `src/invoice_extract/core.py`, `src/invoice_extract/__init__.py`
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes: `discover_file`, `merge_rows`, `fields_of`, `field_counts`.
- Produces:
  - `extract(path, columns=None, ...)`: with `columns=None`, `discover_file` only, without a merge.
  - `extract_many(paths, columns=None, ...)`: with `columns=None`, `discover_file` per path in the pool (errors become error rows as today), then one `merge_rows(rows, settings, get_client)`. With columns, the v1 path. The return type stays `List[Row]`.
  - `__init__` exports `fields_of` and `field_counts`.
  - `STANDARD_COLUMNS` stays exported but is no longer a default anywhere.
  - Existing tests that relied on the old default must now pass `STANDARD_COLUMNS` explicitly. Change only those call sites.

- [ ] **Step 1: Write the failing tests:**
  - `test_extract_many_discovers_and_merges`: two `make_pdf` invoices; the fake reply labels the customer number "Kd.-Nr." on one and "Customer number" on the other; the merge reply maps both. `fields_of(rows)` has exactly one customer field, and both rows hold its value.
  - `test_einvoices_only_template_mode_no_requests` (Review Focus 5): `extract_many([zugferd, xrechnung], Template.loads(v1_yaml).columns)` with only standard labels gives `server.requests == []`, and both rows are `ok`.
  - `test_template_mode_text_and_table`: `extract_many([pdf], [Column("note", "Note", kind="text"), Column("lines", "Line Items", kind="table", table_columns=["Description", "Amount"])], ...)` gives a text cell and a table cell with rows.
- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_core.py`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement**, and update the old call sites as stated above.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: run discovery when no columns are given`

---

### Task 7: Export with tables

**Files:**
- Modify: `src/invoice_extract/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- Consumes: `Column.kind`, `Column.table_columns`, `Cell.kind`, `make_key`.
- Produces:
  - `export(rows, path, columns=None, format=None) -> Path`. The signature is unchanged; `columns` is the selection, including `file`/`status`.
  - **"Invoices" and "Details" sheets:** only non-table columns. A text cell is its whole text in one cell.
  - **One sheet per table column:**
    - title from `_sheet_title(label, used: Set[str]) -> str`: remove `[]:*?/\`, cut to 31 characters, add ` (2)` etc. when used, and never take "Invoices" or "Details";
    - header `["File", "Invoice No."]` (the second only if any row has an `invoice_no` value), then `column.table_columns` or else the union of the cells' columns;
    - one row per table row; every value goes through `_put`.
  - **CSV:** the main file has the non-table columns. Each table column also writes `table_csv_path(path, column) -> Path` = `<stem>-<column.key>.csv` next to it.
  - **JSON:** `{"columns": [...], "rows": [...]}`, where each row's `cells` holds only the selected keys.

- [ ] **Step 1: Write the failing tests:**
  - `test_table_sheet`: rows with a table cell. The workbook has a sheet "Line Items" with header `["File", "Invoice No.", "Description", "Amount"]` and one data row per table row.
  - `test_sheet_titles_cleaned_and_unique` (Review Focus 4): two table columns labelled `"Lines [EUR]/*?"` and a 40-character label, plus a duplicate. All sheet titles are ≤31 characters, contain none of `[]:*?/\`, and are unique.
  - `test_ragged_table_rows_export` (Review Focus 3): table rows with missing keys and an int value. xlsx, csv and json all export, and missing cells are empty.
  - `test_csv_writes_table_files`: `out.csv` and `out-line_items.csv` exist; the second has the header and rows.
  - `test_json_has_selected_fields_only`: a cell whose key is not in `columns` is missing from the JSON.
  - `test_text_block_in_invoices_sheet`: the whole text is in one cell.
- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_export.py`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: export tables as their own sheets and files`

---

### Task 8: CLI

**Files:**
- Modify: `src/invoice_extract/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `extract_many(columns=None)`, `fields_of`, `export_columns`.
- Produces:
  - Without `--template` and `--column`: `rows = extract_many(files, None, ...)`, `columns = export_columns(fields_of(rows))`, then export, and stderr prints `Found <n> fields`.
  - With `--template` and/or `--column`: unchanged (`--column` alone still adds to `STANDARD_COLUMNS`).
  - The help text says that without `--template`/`--column` every field the model finds is exported.

- [ ] **Step 1: Write the failing test** `test_cli_discovery_default`: a PDF with a fake discovery reply (`invoice_no` role plus "Customer No."). The xlsx header is `["File", "Invoice No.", "Customer No.", "Status"]` in that order, and stderr contains "Found 2 fields". The existing CLI tests keep passing.
- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_cli.py`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: discover all fields by default in the CLI`

---

### Task 9: Web API v2

**Files:**
- Modify: `src/invoice_extract/web/app.py`
- Test: `tests/test_web.py` (rewrite the tests of removed endpoints; keep the upload, limit, guard, PNG, unknown-id, patch and export tests)

**Interfaces:**
- Consumes: `discover_file`, `merge_rows`, `fields_of`, `field_counts`, `with_hints`, `export_columns`, `table_csv_path`, `Template`.
- Produces:
  - **Workspace:**
    - `mode` (`"discover"` | `"template"`), `template: List[Column]`, `fields: List[Column]`, `selected: Set[str]`, `generation: int`;
    - entry states `pending`, `running`, `merging`, `done`.
  - **Discover mode:**
    - the extract job runs `discover_file` and sets the state to `merging`;
    - when no entry is `pending` or `running`, one merge job (never two at once) runs `merge_rows` over the `merging` rows with `known=ws.fields`, then sets `ws.fields = fields_of(all rows, known=ws.fields)`;
    - new keys are added to `selected`, and those entries become `done`;
    - if files arrived meanwhile, the merge runs again for them.
  - **Template mode:** the extract job runs the v1 path with `ws.template` and goes straight to `done`.
  - **A job result from an older `generation` is dropped.**
  - **Endpoints:**
    - `GET /api/state` returns:
      - `"mode"`;
      - `"fields": [{key, label, kind, type, role, selected, count}]`;
      - `"files"` as today;
      - `"summary": {"files", "fields": <selected count>, "to_check"}`.
    - `PUT /api/selection` with `{"keys": [...]}` sets the selection; an unknown key gives 400.
    - `GET /api/template` downloads `with_hints(selected fields in list order, rows)`.
    - `POST /api/template` (multipart `file`) switches to template mode: `fields = template`, all selected, `generation += 1`, all files are run again.
    - `DELETE /api/template` switches back to discover mode: fields and selection are cleared, `generation += 1`, all files are run again.
    - `GET /api/export?format=` exports `export_columns(selected fields in list order)`. CSV with a selected table field returns `application/zip` `invoices.zip` with all CSV files.
    - `PATCH /api/files/{id}/cells/{key}`: unchanged, but the key must be a field and must not be a table.
  - **Removed:** `POST /api/columns`, `DELETE /api/columns/{key}`, `GET /api/files/{id}/pages/{n}/segments`, `fill_job`, `refill_all`, `version`.

- [ ] **Step 1: Write the failing tests:**
  - `test_discovery_state_and_counts`: two PDFs with a gated fake reply; the merge reply maps the labels. While extracting, states are not `done`. Afterwards `fields` contains the merged field with `count == 2`, all fields are `selected`, and both files are `done`.
  - `test_selection_drives_export`: `PUT /api/selection` with one key. The xlsx header is `["File", <label>, "Status"]`. An unknown key gives 400.
  - `test_csv_export_with_table_is_zip`: a table field is selected; the export is a ZIP with `invoices.csv` and `invoices-line_items.csv`.
  - `test_template_mode_switch`: POST a template with "Order No."; the mode is `template` and the next reply is answered for `order_no` only. DELETE `/api/template` brings back `discover`.
  - `test_template_download_is_selection`: deselect a field; `GET /api/template` does not contain its label.
  - `test_stale_generation_dropped`: start an extraction gated on an event, switch to a template, release the gate. The row comes from the template run, not the discovery run.
  - Delete the tests for the removed endpoints.
- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/test_web.py`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement.** Keep the host/origin guard, the upload rules, the PNG cache and the lock discipline (never call the LLM while holding the workspace lock).
- [ ] **Step 4: Run** `.venv/bin/pytest -q`, then the new web tests 5× in a row. Expected: all pass every time.
- [ ] **Step 5: Commit:** `feat: web API for discovery, selection and template mode`

---

### Task 10: Web UI layout C

**Files:**
- Rewrite: `src/invoice_extract/web/static/index.html`, `app.js`, `style.css`
- Test: `tests/test_web.py::test_index_served` (unchanged: title, `/static/app.js`, `/static/style.css`)

**Interfaces:**
- Consumes: the Task 9 endpoints only.
- Produces (layout as in the approved mockup "C"):
  - **Top bar:** upload (button and drop anywhere), "Save as template", "Load template", "Use all fields" (shown only in template mode; it calls `DELETE /api/template`), "Export ▾" (xlsx/csv/json), "Clear".
  - **Left: field list.**
    - Groups "Values", "Text blocks" and "Tables", each field with a checkbox, a colour swatch, its label and a count `n/N`.
    - "Select all" / "None" per group.
    - Each change sends `PUT /api/selection`.
    - Clicking a label sets the focus field: the viewer jumps to its page and highlights its box.
  - **Right: viewer.**
    - The current file's page image with an SVG overlay: one box per cell with a box, coloured by field, tagged with the label.
    - Deselected fields are drawn faded and dashed.
    - Previous/next file, page arrows, file name, and the checks list of the current file below.
  - **Bottom: preview table** of the selected non-table fields (files × fields), cells editable as in v1 (Enter = PATCH, Esc = cancel), plus one tab per selected table field showing its rows. A summary line reads `"<files> files · <fields> fields · <to_check> to check"`.
  - **Behaviour:**
    - poll `/api/state` every second while any file is not `done`;
    - all text goes in via `textContent`;
    - colours come from a fixed palette, assigned by field order;
    - no framework.
  - **Style:** the v1 tokens (accent `#2f6fed`, dark mode, radius 8px), and the layout stacks under 900px.

- [ ] **Step 1: Run** `test_index_served` after removing the old JS references from the test, if any. Expected: PASS before and after the rewrite (the test pins the files, not the layout).
- [ ] **Step 2: Implement** the three files. Check with `node --check src/invoice_extract/web/static/app.js`.
- [ ] **Step 3: Smoke test**, if Ollama is available:
  1. Run `invoice-extract serve --port 8001 --base-url http://localhost:11434/v1 --model qwen2.5:7b --vision-model gemma4:12b` (port 8000 may be taken on this machine).
  2. Upload the four files in `samples/`.
  3. Check: fields appear with counts, unticking fades the boxes and removes the column from the preview, the line-items tab shows rows, export xlsx/csv(zip)/json works, and save/load template works.
  4. Take headless Chrome screenshots in light, dark and 700px.
  5. Record the result in the ledger.
- [ ] **Step 4: Run** `.venv/bin/pytest -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: review UI with field selection (layout C)`

---

### Task 11: Docs

**Files:**
- Modify: `README.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: the finished behaviour of Tasks 1–10.
- Produces:
  - **README:**
    - the intro and usage say the model finds all fields and you pick;
    - the CLI default is discovery;
    - Web UI: upload, untick, export, templates;
    - the template example shows `kind: text` and `kind: table`;
    - the export section names the table sheets and the CSV side files;
    - limitations: discovery quality depends on the model, and small models may miss fields;
    - the section structure stays as in `doc-extract/README.md`.
  - **CHANGELOG:** the `0.1.0` entry describes v2. Nothing has been released yet, so the version stays `0.1.0`.

- [ ] **Step 1: Write** both files. Check every claim against the code: flags, defaults, sheet names, file names.
- [ ] **Step 2: Run** `.venv/bin/pytest -q` and the pre-push grep for the internal lab server URL (the pattern is kept outside the repo). Expected: all pass, and no grep hit.
- [ ] **Step 3: Commit:** `docs: describe discovery, selection and table export`
