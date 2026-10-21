# Incoming Invoices Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make invoice-extract one product: received invoices (PDF, scan, photo, XRechnung, ZUGFeRD) become one checked table with fixed bookkeeping columns, e-invoices readable in the UI; discovery and the converter page are removed.

**Architecture:** The v1 pipeline (`detect` → `_fill` → `run_checks`) stays and gets four more standard columns, two column types and three checks. `einvoice.py` reads all standard columns and a readable document from CII/UBL. The web app shrinks to the v1 API without column management plus a document route; the page is rewritten on the v1 page.

**Tech Stack:** Python 3.9+, FastAPI, pypdfium2, defusedxml, openpyxl; plain JS.

**Spec:** docs/superpowers/specs/2026-10-22-incoming-invoices-design.md

## Global Constraints

- All outward text in English; keys of `STANDARD_COLUMNS` are stable API.
- E-invoices never call the model (`test_zugferd_no_model_no_requests` guards it).
- Every test in the suite stays green after each task; run `.venv/bin/python -m pytest -q`.
- Commit after every task with a conventional message.

## Review Focus

1. A ZUGFeRD PDF whose XML lacks tax breakdown, VAT ID and type code: all standard cells exist with source `xml`, empty where missing, no model call (Task 4).
2. `vat_rate` of `"19 %"`, `"0.19"`, `"19,00"`, `"19 % / 7 %"` all normalize to `19` / `19, 7` (Task 2).
3. Duplicate detection ignores case and whitespace and never pairs rows without an invoice number; re-running it does not stack warnings (Task 3).
4. The document route answers 404 for PDFs and scans and a readable dict for XML and ZUGFeRD (Task 5).
5. Editing a cell in the UI re-runs row checks and the duplicate check (Task 5).

---

### Task 1: Remove discovery and the converter page

**Files:**
- Delete: `src/invoice_extract/discover.py`, `tests/test_discover.py`, `src/invoice_extract/web/einvoice_api.py`, `src/invoice_extract/web/static/einvoice.html`, `src/invoice_extract/web/static/einvoice.js`, `src/invoice_extract/web/static/table.html`, `src/invoice_extract/web/static/app.js`, `tests/test_web_einvoice.py`
- Modify: `src/invoice_extract/llm.py` (drop DISCOVER_*, MERGE_PROMPT, COLUMNS_PROMPT, ROLE_LABELS, discover_items, _salvage_items, _infer_roles, _mapping, merge_labels, merge_table_columns), `src/invoice_extract/core.py` (columns None → STANDARD_COLUMNS), `src/invoice_extract/cli.py`, `src/invoice_extract/__init__.py`, `tests/test_llm.py`, `tests/test_core.py`, `tests/test_cli.py`
- Replace: `src/invoice_extract/web/app.py` with the v1 app (git show 2facba9) minus columns/segments/template routes; `tests/test_web.py` with v1-style tests (upload xml, missing model, png, export xlsx, patch cell, 404, 413, index, guard)
- Static: temporary `index.html` from 2facba9 and `app.js` from 2facba9 so `/` serves; both are rewritten in Task 6.

**Interfaces:**
- Produces: `extract(path, columns=None)` and `extract_many(paths, columns=None)` default to `STANDARD_COLUMNS`; `create_app(settings, workdir=None, allowed_hosts=None, columns=None)`.

- [ ] Step 1: Delete the files, strip llm.py/core.py/cli.py/__init__.py, replace app.py and test_web.py.
- [ ] Step 2: Update tests: drop discovery tests in test_llm.py, test_core.py (`test_extract_many_discovers_and_merges` → `test_extract_many_defaults_to_standard_columns`), test_cli.py (`test_cli_discovery_default` → `test_cli_default_columns_standard`).
- [ ] Step 3: Run `.venv/bin/python -m pytest -q`. Expected: all pass.
- [ ] Step 4: Commit `refactor: one product, remove discovery and the converter page`.

### Task 2: New columns and column types

**Files:**
- Modify: `src/invoice_extract/columns.py` (TYPES += doctype, percent; ROLES += doc_type, supplier_vat_id, description, vat_rate; STANDARD_COLUMNS as the spec table with hints), `src/invoice_extract/normalize.py` (`normalize` handles doctype and percent; `display_variants` for percent), `tests/test_columns.py`, `tests/test_normalize.py`

**Interfaces:**
- Produces: `normalize("percent", "19 % / 7 %", "")[0] == "19, 7"`, `normalize("doctype", "Gutschrift", "")[0] == "credit note"`.

- [ ] Step 1: Tests `test_standard_columns_order_and_keys` (new order), `test_percent_forms` (parametrized: "19 %"→"19", "0.19"→"19", "19,00"→"19", "19 % / 7 %"→"19, 7", 19→"19", "0"→"0", "5.5"→"5.5", ""→None), `test_doctype_forms` ("Gutschrift"→"credit note", "Credit Note"→"credit note", "Rechnung"→"invoice", ""→None).
- [ ] Step 2: Run, expect FAIL. Implement. Run, expect PASS. Full suite green.
- [ ] Step 3: Commit `feat: bookkeeping columns (type, VAT ID, description, VAT rate)`.

### Task 3: Checks: VAT rate, VAT ID over 250, duplicates

**Files:**
- Modify: `src/invoice_extract/checks.py` (+ `vat_rate`, `vat_id_missing` in `run_checks`; new `duplicate_checks(rows: List[Row]) -> None`), `src/invoice_extract/core.py` (`extract_many` runs `duplicate_checks`), `src/invoice_extract/__init__.py` (export), `tests/test_checks.py`, `tests/test_core.py`

- [ ] Step 1: Tests: `test_vat_rate_matches_and_mismatches`, `test_vat_rate_mixed_not_checked`, `test_vat_id_missing_over_250` (119 no warn, 250 warn, credit note no warn), `test_duplicates_same_supplier_and_number` (case/space insensitive, both rows warned, message names the other file), `test_duplicates_skip_empty_number`, `test_duplicates_rerun_does_not_stack`, in test_core `test_extract_many_marks_duplicates`.
- [ ] Step 2: RED → implement → GREEN → suite green.
- [ ] Step 3: Commit `feat: VAT rate, VAT ID and duplicate checks`.

### Task 4: Read all standard columns from e-invoice XML

**Files:**
- Modify: `src/invoice_extract/einvoice.py` (`parse_xml` returns doc_type, supplier_vat_id, vat_rate, description too; `XML_KEYS`), `src/invoice_extract/core.py` (uses `XML_KEYS` from einvoice; percent/doctype normalization), `tests/pdfgen.py` (samples with TypeCode 380, seller VAT ID, tax breakdown 19 %, SAMPLE updated), `tests/test_einvoice.py`

- [ ] Step 1: Tests: `test_parse_cii`/`test_parse_ubl` with the extended SAMPLE (`doc_type: "invoice"`, `supplier_vat_id: "DE123456789"`, `vat_rate: "19.00"`, `description: "Paper"`), `test_parse_ubl_credit_note` asserts `doc_type == "credit note"`, `test_parse_description_joins_lines_and_cuts` (three lines → "A; B; C", 100-char names cut to 80 with "…"), `test_parse_vat_rates_mixed` (two tax groups → "19.00, 7.00"), `test_parse_minimal_xml_has_no_new_keys` (a sample without the nodes: keys absent).
- [ ] Step 2: core test `test_zugferd_no_model_no_requests` extended: `row.cells["vat_rate"].value == "19"`, `row.cells["doc_type"].value == "invoice"`.
- [ ] Step 3: RED → implement → GREEN → suite green. Commit `feat: read type, VAT ID, VAT rate and description from e-invoice XML`.

### Task 5: Readable e-invoice document and the web API

**Files:**
- Modify: `src/invoice_extract/einvoice.py` (`parse_document(data) -> Dict[str, Any]`, `MEANS` code→text), `src/invoice_extract/web/app.py` (`GET /api/files/{id}/document`; duplicate_checks after each job and edit; `columns` parameter), `src/invoice_extract/cli.py` (`serve --template`), `tests/test_einvoice.py`, `tests/test_web.py`, `tests/test_cli.py`

- [ ] Step 1: Tests: `test_parse_document_cii` and `test_parse_document_ubl` (seller name, lines[0], taxes[0].rate, totals.gross, payment.iban, kind), `test_parse_document_missing_parts_are_empty`; web: `test_document_route` (xml → 200 with `number`, zugferd → 200, pdf → 404, unknown → 404), `test_duplicates_across_uploads` (upload the same XML twice → both warnings; patch invoice_no of one → warnings gone), `test_serve_template_columns` (create_app with two columns → state columns keys), cli: `test_cli_serve_passes_template` (monkeypatched uvicorn.run captures app; `/api/state` columns).
- [ ] Step 2: RED → implement → GREEN → suite green. Commit `feat: readable e-invoice view and duplicate checks in the web API`.

### Task 6: The page

**Files:**
- Replace: `src/invoice_extract/web/static/index.html`, `src/invoice_extract/web/static/app.js`, `src/invoice_extract/web/static/style.css`

Content per spec: top bar, table (editable cells), viewer with boxes or the e-invoice sheet ("Show XML data" toggle for ZUGFeRD, "Print" button), values list, checks list, summary. Print CSS shows the sheet alone.

- [ ] Step 1: Write the three files. `node --check app.js` passes.
- [ ] Step 2: Smoke test with the running server on 127.0.0.1:8001 via curl (`/`, `/static/app.js`, upload an XML sample, `/api/files/{id}/document`). Expected: 200 each, document has `number`.
- [ ] Step 3: Commit `feat: one-page review UI for received invoices`.

### Task 7: README, CHANGELOG, package description

**Files:**
- Modify: `README.md`, `CHANGELOG.md`, `pyproject.toml` (description), `Dockerfile` if it mentions removed things.

- [ ] Step 1: Rewrite README: intro (the product), supported inputs, prerequisites, installation, Web UI, Command line, Docker, Python API, Templates, Export, Settings, Checks and statuses, Writing e-invoices (CLI), Limitations, Tests, License.
- [ ] Step 2: CHANGELOG 0.1.0 entry describes the product. Commit `docs: describe the product`.

### Task 8: Final review

- [ ] Fresh reviewer on the whole branch; fix Critical/Important with tests; ledger minors.
