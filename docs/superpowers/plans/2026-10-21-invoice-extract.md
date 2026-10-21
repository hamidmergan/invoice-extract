# invoice-extract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pip package that turns invoices (e-invoices, digital PDFs, scans) into one table with user-chosen columns, with a library, a CLI, a local web UI and a Dockerfile.

**Architecture:**
- Per file, the cheapest path wins:
  - Embedded or pure XML is parsed without AI.
  - A PDF text layer comes from pypdfium2.
  - Scans are transcribed by doc-extract.
- One JSON LLM call maps the text to the columns. `source_text` is located in the PDF for boxes, then plain-Python normalization and checks run.
- The web UI is a FastAPI app with in-memory state and plain HTML, JS and CSS served from the package.

**Tech Stack:** Python 3.9+, openai, pypdfium2, Pillow, doc-extract-llm, openpyxl, PyYAML, defusedxml; `[ui]` adds fastapi, uvicorn and python-multipart; pytest with a local fake OpenAI server.

**Spec:** `docs/superpowers/specs/2026-10-21-invoice-extract-design.md`

## Global Constraints

- Repo root: `/Users/hamidmergan/Projects/oss-tools/invoice-extract`, src layout `src/invoice_extract`. PyPI name `invoice-extract` (checked 2026-10-21, free).
- `requires-python = ">=3.9"`. Use `typing.Optional/List/Dict`, no `X | Y`, no `match`.
- Runtime dependencies:
  - `openai>=1.30`
  - `pypdfium2>=4.30`
  - `Pillow>=10.0`
  - `doc-extract-llm>=0.1.0`
  - `openpyxl>=3.1`
  - `PyYAML>=6.0`
  - `defusedxml>=0.7`
- Optional dependency groups:
  - `ui`: `fastapi>=0.100`, `uvicorn>=0.23`, `python-multipart>=0.0.6`
  - `test`: `pytest>=7`, `httpx>=0.24`, plus the `ui` packages
  - `dev`: `test` plus `build` and `twine`
- AI access is OpenAI-compatible only: `base_url`, `model`, optional `api_key` (placeholder `"not-needed"`), optional `vision_model`.
  - There is no default for `base_url` or `model`.
  - Missing values raise `ConfigError` naming the flag and the env var, but only when an LLM call is actually needed. E-invoices must work without any model.
- Settings order: argument, then env `INVOICE_EXTRACT_*` (api key also `OPENAI_API_KEY`), then default.
- Everything public is in English (code, comments, messages, CLI help, README, tests, commits).
- No Co-Authored-By lines in commits.
- No KIS code or design. No real invoices in the repo.
- `.gitignore` contains `.DS_Store`.
- One file is exactly one invoice. One row per file.
- Before any push, run `grep -rn` for the lab server URL (kept only locally). The push goes to `hamidmergan/invoice-extract`, never `merganh`.
- The mockup (claude.ai artifact) is a visual reference only. Never copy it into the repo.

## Review Focus

1. **Formula injection in exports.** An invoice text starting with `=`, `+`, `-` or `@` must be written to xlsx and CSV as plain text, never as a formula. Tested in Task 8.
2. **Malformed or hostile files.** An encrypted or broken PDF, an XML bomb or a 0-byte file gives an error for that file only, and the others still finish. Tested in Tasks 4 and 7.
3. **Sloppy LLM output.** JSON wrapped in ```` ```json ```` fences, extra keys, unknown column keys or a non-numeric `page` must still parse. Text that is not JSON at all becomes a `ModelError` for that file. Tested in Task 6.
4. **Duplicate or non-ASCII column labels.** Labels like "Kd.-Nr." twice, or "Größe", give unique stable keys. Tested in Task 2.
5. **E-invoices only, no model set.** A stack of only ZUGFeRD/XRechnung files runs with no `base_url`/`model` and makes no HTTP request. Tested in Task 7.

---

## File Structure

```
invoice-extract/
  pyproject.toml  LICENSE  README.md  .gitignore  .dockerignore  Dockerfile
  src/invoice_extract/
    __init__.py      public exports, __version__ = "0.1.0"
    errors.py        InvoiceError, ConfigError, ModelError, UnsupportedFileError, LimitError
    config.py        Settings, resolve()
    columns.py       Column, STANDARD_COLUMNS, make_key(), Template (YAML)
    results.py       Box, Check, Cell, Row
    pdf.py           page_count(), render_page(), text_pages(), segments(), locate(), click_hint()
    detect.py        detect() -> Kind, embedded_xml()
    einvoice.py      parse_xml() for CII and UBL
    normalize.py     normalize(), display_variants()
    checks.py        run_checks()
    llm.py           make_client(), fill_columns(), vision_boxes()
    core.py          extract(), extract_many(), add_columns()
    export.py        export()
    cli.py           main(), the serve subcommand
    web/app.py       create_app()
    web/static/      index.html  app.js  style.css
    py.typed
  tests/
    conftest.py      FakeServer (programmable OpenAI fake), fixtures
    pdfgen.py        make_pdf(), add_attachment(), CII/UBL sample builders
    test_*.py        one per module
```

---

### Task 1: Scaffold, errors, config

**Files:**
- Create: `pyproject.toml`, `LICENSE` (MIT, Hamid Mergan, 2026), `.gitignore` (`.DS_Store`, `__pycache__/`, `*.egg-info/`, `dist/`, `build/`, `.venv/`, `.pytest_cache/`, `samples/`)
- Create: `src/invoice_extract/__init__.py`, `errors.py`, `config.py`, `py.typed`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - **errors.py:** `class InvoiceError(Exception)`. `ConfigError`, `ModelError`, `UnsupportedFileError` and `LimitError` subclass it.
  - **Settings fields:**
    - `base_url`, `model`, `vision_model`, `api_key`: `Optional[str]`
    - `max_files=100`, `max_file_mb=20.0`, `max_pages=50`, `concurrency=4`
    - `timeout=120.0`, `max_retries=2`
  - **Settings methods:**
    - `Settings.require_llm() -> None` raises ConfigError if base_url or model is missing.
    - `Settings.vision() -> str` returns vision_model or model.
  - `resolve(**kwargs) -> Settings`, with the env prefix `INVOICE_EXTRACT_`.
  - `PLACEHOLDER_API_KEY = "not-needed"`.

- [ ] **Step 1: `git init` in the repo root, write pyproject.**
  - Mirror `doc-extract/pyproject.toml`: setuptools>=77, license MIT, `package-data` for `py.typed` and `web/static/*`.
  - Script `invoice-extract = "invoice_extract.cli:main"`.
  - `description = "Turn invoices (PDF, scans, e-invoices) into one table with columns you choose, using any OpenAI-compatible model"`.
- [ ] **Step 2: Write failing tests in `tests/test_config.py`:**
  - `test_resolve_order`: an argument beats an env var, and an env var beats the default.
  - `test_require_llm_message`:
    - `resolve().require_llm()` raises ConfigError.
    - The message contains `--base-url`, `INVOICE_EXTRACT_BASE_URL`, `--model` and `INVOICE_EXTRACT_MODEL`.
  - `test_no_error_without_llm_need`: `resolve()` with nothing set does not raise.
  - `test_api_key_fallback`:
    - With no key set, the key is `"not-needed"`.
    - With `OPENAI_API_KEY=k`, the key is `"k"`.
  - `test_vision_falls_back_to_model`: `resolve(model="m").vision() == "m"`, and with `vision_model="v"` it is `"v"`.
  - `test_numeric_env`: `INVOICE_EXTRACT_MAX_FILES=5` resolves to int 5. A non-numeric value raises ConfigError naming the variable.
- [ ] **Step 3: Run `pip install -e ".[test]" && pytest tests/test_config.py -q`.** Expect FAIL.
- [ ] **Step 4: Implement `errors.py` and `config.py`,** following doc-extract's `config.py` pattern.
- [ ] **Step 5: Run `pytest -q | tail -n 20`.** Expect PASS.
- [ ] **Step 6: Commit:** `chore: scaffold package with settings and errors`

### Task 2: Columns, templates, result types

**Files:**
- Create: `src/invoice_extract/columns.py`, `src/invoice_extract/results.py`
- Test: `tests/test_columns.py`, `tests/test_results.py`

**Interfaces:**
- Produces:
  - **Column:**
    - `@dataclass Column(key: str, label: str, hint: str = "", required: bool = False, type: str = "text")`
    - `type` is one of `text`, `date`, `amount`, `iban`, `file`, `status`.
  - **STANDARD_COLUMNS: List[Column]** in this order. `type` in brackets, otherwise text:

    | key | label | type |
    | --- | --- | --- |
    | `file` | File | file |
    | `supplier` | Supplier | text |
    | `invoice_no` | Invoice No. | text |
    | `invoice_date` | Invoice Date | date |
    | `due_date` | Due Date | date |
    | `net` | Net | amount |
    | `vat` | VAT | amount |
    | `gross` | Gross | amount |
    | `currency` | Currency | text |
    | `iban` | IBAN | iban |
    | `status` | Status | status |

    `supplier`, `invoice_no`, `invoice_date` and `gross` are `required=True`.
  - `RESERVED = {"file", "status"}`: never sent to the LLM.
  - **make_key and column:**
    - `make_key(label: str, existing: Iterable[str]) -> str` lowercases the label, applies NFKD and drops non-ASCII, joins with `_`, and adds `_2`, `_3` and so on if the key is taken. An empty result becomes `column`.
    - `column(label: str, hint: str = "", type: str = "text", existing=()) -> Column`
  - **Template:**
    - `@dataclass Template(name: str, columns: List[Column])`
    - `Template.load(path) -> Template` and `Template.loads(text)`
    - `Template.save(path)` and `Template.dumps() -> str`
    - YAML shape: `{name: str, columns: [{key, label, hint, required, type}]}`, loaded with `yaml.safe_load`.
    - An unknown `type` or a missing `label` raises ConfigError.
  - **Result types (results.py):**
    - `@dataclass Box(page: int, x0: float, y0: float, x1: float, y1: float)`. `page` is 1-based. The coordinates are normalized 0..1 from the page's top-left corner.
    - `@dataclass Check(name: str, level: str, message: str)`. `level` is one of `ok`, `warning`, `error`.
    - `@dataclass Cell(value: Any = None, raw: str = "", page: Optional[int] = None, box: Optional[Box] = None, source: str = "", edited: bool = False, checks: List[Check] = [])`
      - `value` is `str`, `datetime.date` or `decimal.Decimal`.
      - `source` is one of `xml`, `text`, `vision`, `manual`.
    - `@dataclass Row(path: str, kind: str = "", cells: Dict[str, Cell] = {}, checks: List[Check] = [], error: Optional[str] = None)`
      - `Row.status -> str`: `"error"` if `error` is set, otherwise the worst level over the row checks and all cell checks. No checks gives `"ok"`.
      - `Row.to_dict() -> dict` is JSON-safe: dates become ISO strings, Decimals become strings.

- [ ] **Step 1: Write the failing tests:**
  - `test_standard_columns_order_and_keys`: checks the key order exactly as in the table.
  - `test_make_key_unique`: `make_key("Kd.-Nr.", [])=="kd_nr"`, and `make_key("Kd.-Nr.", ["kd_nr"])=="kd_nr_2"`.
  - `test_make_key_non_ascii`: `make_key("Größe", [])=="grosse"` (replace `ß` with `ss` before NFKD) and `make_key("請求", [])=="column"`.
  - `test_template_roundtrip`: `Template.loads(t.dumps()) == t`.
  - `test_template_rejects_unknown_type`
  - `test_row_status_worst_level`
  - `test_row_status_error_wins`
  - `test_row_to_dict_json_safe`: `json.dumps(row.to_dict())` works and contains `"2026-10-21"` and `"1234.50"`.
- [ ] **Step 2: Run `pytest tests/test_columns.py tests/test_results.py -q`.** Expect FAIL.
- [ ] **Step 3: Implement both modules.**
- [ ] **Step 4: Run them again.** Expect PASS.
- [ ] **Step 5: Commit:** `feat: add columns, templates and result types`

### Task 3: Test PDF generator and PDF text, boxes and rendering

**Files:**
- Create: `tests/pdfgen.py`, `src/invoice_extract/pdf.py`
- Test: `tests/test_pdf.py`

**Interfaces:**
- **Produces (test helper):**
  - `make_pdf(pages: List[List[Tuple[float, float, str]]], size=(595, 842)) -> bytes` writes a hand-written minimal PDF:
    - Helvetica Type1, one `BT /F1 10 Tf x y Td (text) Tj ET` per item, a correct xref.
    - The `(x, y)` coordinates are PDF points from the bottom-left. No dependency.
  - `add_attachment(pdf_bytes, name: str, data: bytes) -> bytes` uses `pypdfium2.PdfDocument.new_attachment(name).set_data(data)`, then saves to bytes.
- **Produces (pdf.py, takes a file path, works for PDFs and for images):**
  - `@dataclass Segment(text: str, box: Box)`
  - `page_count(path) -> int`. For images this is the number of frames (TIFF), otherwise 1.
  - `render_page(path, page: int, scale: float = 1.5) -> bytes` returns a PNG. `page` is 1-based.
  - `text_pages(path) -> List[str]` is the text layer per page. For images it is `[]`.
  - `segments(path, page: int) -> List[Segment]` lists the text runs from `textpage.count_rects()` / `get_rect(i)` / `get_text_bounded(*rect)`, with boxes normalized.
  - `locate(path, source_text: str, page: Optional[int] = None) -> Optional[Box]`:
    - Whitespace-insensitive and case-sensitive.
    - Searches `page` first, then all pages.
    - Returns the union of the `get_charbox` boxes of the match on one page, or None.
  - `click_hint(segs: List[Segment], index: int) -> str`:
    - Finds the label: the nearest segment to the left on the same line (vertical centres within half a line height), else the nearest one directly above (horizontal overlap).
    - Returns `'Label near the value: "<label>". Example value: "<value>".'`, or only the example part if no label is found.
- Coordinate conversion: pypdfium2 gives `(left, bottom, right, top)` in points from the bottom-left. Box = `(left/w, 1-top/h, right/w, 1-bottom/h)`.

- [ ] **Step 1: Write the failing tests.** The fixture is a one-page PDF with `(50, 780, "ACME GmbH")`, `(50, 700, "Kd.-Nr.")`, `(120, 700, "40711")`, `(50, 650, "Total 1.234,50 EUR")`.
  - `test_text_pages_contains_text`: `"40711" in text_pages(p)[0]`.
  - `test_locate_box_position`: `locate(p, "40711")` has `page == 1` and `0.19 < box.x0 < 0.22`. The box y-centre is near `1 - 700/842`, within ±0.02.
  - `test_locate_whitespace_insensitive`: `locate(p, "Total  1.234,50\nEUR")` is not None.
  - `test_locate_missing_returns_none`
  - `test_click_hint_finds_left_label`: the segment index of `"40711"` gives a hint containing `"Kd.-Nr."` and `"40711"`.
  - `test_render_page_png`: the output starts with `b"\x89PNG"`.
  - `test_image_has_no_text`: a Pillow PNG gives `text_pages == []` and `page_count == 1`.
- [ ] **Step 2: Run `pytest tests/test_pdf.py -q`.** Expect FAIL.
- [ ] **Step 3: Implement `tests/pdfgen.py` and `pdf.py`.** Close the pypdfium2 documents in `finally`.
- [ ] **Step 4: Run it again.** Expect PASS.
- [ ] **Step 5: Commit:** `feat: read PDF text, locate values and render pages`

### Task 4: Type detection and e-invoice XML

**Files:**
- Create: `src/invoice_extract/detect.py`, `src/invoice_extract/einvoice.py`
- Modify: `tests/pdfgen.py` (add `cii_xml(**values) -> bytes`, `ubl_xml(**values) -> bytes`, minimal valid-shaped documents)
- Test: `tests/test_detect.py`, `tests/test_einvoice.py`

**Interfaces:**
- **detect.py:**
  - `detect(path, settings) -> str` returns one of `"xml"`, `"zugferd"`, `"pdf"`, `"scan"`:
    1. It checks the file: the file exists, is under `max_file_mb`, and has a suffix in `.pdf .xml .png .jpg .jpeg .webp .gif .bmp .tif .tiff`. It raises `UnsupportedFileError` or `LimitError`, and a 0-byte file raises `UnsupportedFileError`.
    2. `.xml` gives `"xml"`.
    3. A PDF whose `embedded_xml()` is not None gives `"zugferd"`.
    4. A PDF with at least 30 non-whitespace text characters per page on average gives `"pdf"`.
    5. Anything else gives `"scan"`.
  - `embedded_xml(path) -> Optional[bytes]` returns the first attachment whose lowercased name is in `{"factur-x.xml", "zugferd-invoice.xml", "xrechnung.xml", "zugferd.xml"}`.
  - pypdfium2 errors (broken or encrypted PDF) become `UnsupportedFileError("Cannot open PDF: ...")`.
- **einvoice.py:**
  - `parse_xml(data: bytes) -> Dict[str, str]` returns the raw strings for the standard keys it finds. It uses `defusedxml.ElementTree.fromstring`, and any defusedxml or parse error becomes `UnsupportedFileError`.
  - The root local-name `CrossIndustryInvoice` means CII. `Invoice` or `CreditNote` means UBL. Anything else raises `UnsupportedFileError`.
  - Dates are returned as ISO `YYYY-MM-DD`. CII format 102 is `YYYYMMDD`.
  - **CII paths.** `S` stands for `rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeSettlement`:
    - `invoice_no`: `rsm:ExchangedDocument/ram:ID`
    - `invoice_date`: `rsm:ExchangedDocument/ram:IssueDateTime/udt:DateTimeString`
    - `supplier`: `rsm:SupplyChainTradeTransaction/ram:ApplicableHeaderTradeAgreement/ram:SellerTradeParty/ram:Name`
    - `currency`: `S/ram:InvoiceCurrencyCode`
    - `net`: `S/ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:TaxBasisTotalAmount`
    - `vat`: `S/ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:TaxTotalAmount`
    - `gross`: `S/ram:SpecifiedTradeSettlementHeaderMonetarySummation/ram:GrandTotalAmount`
    - `due_date`: `S/ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString`
    - `iban`: `S/ram:SpecifiedTradeSettlementPaymentMeans/ram:PayeePartyCreditorFinancialAccount/ram:IBANID`
  - **UBL paths:**
    - `invoice_no`: `cbc:ID`
    - `invoice_date`: `cbc:IssueDate`
    - `due_date`: `cbc:DueDate`
    - `supplier`: `cac:AccountingSupplierParty/cac:Party/cac:PartyLegalEntity/cbc:RegistrationName`, falling back to `.../cac:PartyName/cbc:Name`
    - `currency`: `cbc:DocumentCurrencyCode`
    - `net`: `cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount`
    - `vat`: `cac:TaxTotal/cbc:TaxAmount`
    - `gross`: `cac:LegalMonetaryTotal/cbc:TaxInclusiveAmount`
    - `iban`: `cac:PaymentMeans/cac:PayeeFinancialAccount/cbc:ID`
  - `xml_text(data: bytes) -> str` is the pretty XML text that is given to the LLM for custom columns.
- **pdfgen.py (test helper):** `write_samples(folder) -> List[Path]` writes one digital PDF, one ZUGFeRD PDF (CII attached as `factur-x.xml`), one XRechnung UBL file and one PNG "scan". It is used by tests and by the manual smoke test in Task 11.

- [ ] **Step 1: Write the failing tests:**
  - `test_detect_kinds`: one fixture of each kind returns the expected kind. The scan fixture is a PNG, plus an empty-page PDF.
  - `test_detect_zero_byte_and_wrong_suffix`: `UnsupportedFileError`.
  - `test_detect_too_large`: `LimitError` with `max_file_mb=0.000001`.
  - `test_detect_broken_pdf`: `b"%PDF-1.4 garbage"` raises `UnsupportedFileError`.
  - `test_parse_cii`: all 9 keys equal the sample values, and `invoice_date=="2026-10-21"`.
  - `test_parse_ubl`: the same for UBL, including the `PartyName` fallback.
  - `test_parse_xml_bomb_rejected`: a billion-laughs document raises `UnsupportedFileError` quickly.
  - `test_parse_unknown_root`
- [ ] **Step 2: Run `pytest tests/test_detect.py tests/test_einvoice.py -q`.** Expect FAIL.
- [ ] **Step 3: Implement `detect.py`, `einvoice.py` and the pdfgen XML builders.**
- [ ] **Step 4: Run them again.** Expect PASS.
- [ ] **Step 5: Commit:** `feat: detect invoice type and parse CII/UBL e-invoices`

### Task 5: Normalization and checks

**Files:**
- Create: `src/invoice_extract/normalize.py`, `src/invoice_extract/checks.py`
- Test: `tests/test_normalize.py`, `tests/test_checks.py`

**Interfaces:**
- **normalize(type: str, value: Any, raw: str) -> Tuple[Any, List[Check]]:**
  - `value` is what the LLM returned and `raw` is the source text.
  - **text:** the value is stripped.
  - **iban:** spaces are removed and the value is uppercased.
  - **date:**
    - The LLM value is parsed as ISO.
    - `raw` is parsed with these candidates: `%d.%m.%Y`, `%d.%m.%y`, `%Y-%m-%d`, `%d/%m/%Y`, `%m/%d/%Y`, `%d-%m-%Y`, `%Y/%m/%d` and `%d %B %Y` (English months).
    - Ambiguous (the candidates give more than one distinct date): `Check("date_ambiguous", "warning", ...)`.
    - The LLM date is not among the candidates while the candidates are not empty: `Check("value_mismatch", "warning", ...)`.
    - The LLM value is not ISO: try `raw`. If both fail, keep the original string and add `Check("unparsed", "warning", ...)`.
  - **amount:**
    - The LLM value becomes a `Decimal`.
    - `raw` is parsed like this:
      - Strip the currency symbols and letters.
      - If both `,` and `.` appear, the last one is the decimal separator.
      - A single separator followed by exactly 3 digits is ambiguous: try both readings.
      - Otherwise the single separator is the decimal separator.
    - The same ambiguity, mismatch and unparsed rules apply as for dates. The value is never dropped.
  - Raw `None` or empty means no raw-based checks.
- `display_variants(type: str, value: Any) -> List[str]`:
  - date: `["21.10.2026", "21/10/2026", "2026-10-21", "10/21/2026"]`
  - amount `Decimal("1234.50")`: `["1.234,50", "1,234.50", "1234,50", "1234.50", "1 234,50"]`
  - anything else: `[str(value)]`
- **run_checks(row: Row, columns: List[Column]) -> List[Check]** (row-level only):
  - `totals`:
    - The check runs only when net, vat and gross are all Decimals.
    - It is `ok` if `abs(net+vat-gross) <= Decimal("0.01")`, otherwise `error`, with the message `"Net + VAT (1234.50 + 234.56) does not equal gross (1500.00)"`.
  - `iban`:
    - mod-97 over the rearranged digits.
    - A failure gives `error` with the message `"IBAN checksum is invalid"`.
    - An empty IBAN is skipped.
  - `due_before_invoice`: if both dates are `date` and due < invoice, `warning`.
  - `required`: each `required` column whose cell value is None or `""` gives `warning` `"Missing: <label>"`.
  - Only checks that ran are returned.

- [ ] **Step 1: Write the failing tests:**
  - `test_amount_german`: `normalize("amount", "1234.50", "1.234,50 €")` gives `Decimal("1234.50")` and no checks.
  - `test_amount_english`: raw `"1,234.50"` gives no checks.
  - `test_amount_ambiguous`: raw `"1.234"` with LLM value `"1234"` gives `Decimal("1234")` and the check `amount_ambiguous` (warning).
  - `test_amount_mismatch`: LLM `"99.00"` with raw `"1.234,50"` keeps `Decimal("99.00")` and gives `value_mismatch`.
  - `test_date_iso_from_german`: `normalize("date", "2026-10-21", "21.10.2026")` gives `date(2026,10,21)` and no checks.
  - `test_date_ambiguous`: raw `"03/04/2026"` gives `date_ambiguous`.
  - `test_date_unparsed_kept`: LLM `"sometime"` with raw `"sometime"` keeps the string and gives `unparsed`.
  - `test_iban_cleanup`
  - `test_display_variants_amount`
  - `test_totals_ok_and_error`: `100.00+19.00 = 119.00` is ok, and `119.02` is an error. The message contains `"does not equal"`.
  - `test_iban_mod97`: `DE89370400440532013000` is ok, and `DE89370400440532013001` is an error.
  - `test_due_before_invoice`
  - `test_required_missing`
- [ ] **Step 2: Run `pytest tests/test_normalize.py tests/test_checks.py -q`.** Expect FAIL.
- [ ] **Step 3: Implement `normalize.py` and `checks.py`.** The check names are `date_ambiguous`, `amount_ambiguous`, `value_mismatch`, `unparsed`, `totals`, `iban`, `due_before_invoice`, `required`, and `source_not_located` (used in Task 7).
- [ ] **Step 4: Run them again.** Expect PASS.
- [ ] **Step 5: Commit:** `feat: normalize values and run plain checks`

### Task 6: LLM calls and fake server

**Files:**
- Create: `src/invoice_extract/llm.py`, `tests/conftest.py`
- Test: `tests/test_llm.py`

**Interfaces:**
- **FakeServer test helper (conftest.py),** modelled on `doc-extract/tests/conftest.py`:
  - `FakeServer.reply: Callable[[dict], str]` turns a request body into the assistant content. The default returns `"{}"`.
  - `FakeServer.requests: List[dict]`
  - `FakeServer.base_url`
  - Fixture `server`.
  - A request with an `image_url` part is a vision request. A doc-extract transcription request also has an image.
- **llm.py:**
  - `make_client(settings) -> openai.OpenAI` calls `settings.require_llm()` first.
  - `fill_columns(client, model: str, columns: List[Column], text: str) -> Dict[str, dict]`:
    - Makes one `chat.completions.create` call at temperature 0.
    - The system prompt says, in English:
      - Extract only values that are printed on the invoice.
      - Return `null` when a value is missing.
      - Give dates as ISO and amounts as plain decimals with a dot.
      - Copy `source_text` exactly from the text.
      - `page` is the number from the `<!-- page N -->` marker.
    - The user message holds the JSON list `[{key, label, hint, type}]` plus the text.
    - The model must reply `{"<key>": {"value": str|number|null, "source_text": str|null, "page": int|null} | null}`.
    - The result keeps only the requested keys. Missing keys become `None` entries. A non-int `page` becomes None.
    - Fences and leading or trailing prose are stripped: parse from the first `{` to the last `}`. Invalid JSON or an API error raises `ModelError` with the file context added by the caller.
    - `RESERVED` columns are never sent.
  - `vision_boxes(client, model: str, png: bytes, items: Dict[str, str], page: int) -> Dict[str, Box]`:
    - `items` maps each key to its source_text.
    - The model is asked for `{"<key>": [x0, y0, x1, y1]}` as integers 0–1000 from the top-left.
    - A box is kept only if `0 <= x0 < x1 <= 1000` and `0 <= y0 < y1 <= 1000`. It is divided by 1000.
    - Any error or unparsable reply returns `{}` and never raises.

- [ ] **Step 1: Write the failing tests:**
  - `test_fill_columns_basic`: the server replies with the JSON for `invoice_no`. The result has the value `"R-1"`, `source_text` `"R-1"` and page 1.
  - `test_fill_columns_strips_fences_and_extra_keys`: the reply is `` ```json\n{"invoice_no": {...}, "bogus": 1}\n``` ``. The result keys equal the requested keys.
  - `test_fill_columns_missing_key_is_none`
  - `test_fill_columns_bad_page`: `"page": "one"` gives a page of None.
  - `test_fill_columns_not_json_raises`: the reply `"sorry"` raises `ModelError`.
  - `test_reserved_not_sent`: the request body does not contain `"status"` or `"file"` as column keys.
  - `test_api_key_header`: without a key, the header is `Bearer not-needed`.
  - `test_vision_boxes_valid_and_invalid`: `[100,200,300,250]` gives `Box(page, .1, .2, .3, .25)`, and `[500,0,100,10]` is dropped.
  - `test_vision_boxes_error_returns_empty`: the server returns 500, and the result is `{}`.
- [ ] **Step 2: Run `pytest tests/test_llm.py -q`.** Expect FAIL.
- [ ] **Step 3: Implement `conftest.py` and `llm.py`.**
- [ ] **Step 4: Run it again.** Expect PASS.
- [ ] **Step 5: Commit:** `feat: add LLM column filling and vision boxes`

### Task 7: Pipeline

**Files:**
- Create: `src/invoice_extract/core.py`
- Modify: `src/invoice_extract/__init__.py` (exports below)
- Test: `tests/test_core.py`

**Interfaces:**
- Consumes everything above.
- Produces:
  - `extract(path, columns: Optional[List[Column]] = None, *, base_url=None, model=None, api_key=None, vision_model=None, client=None, **limits) -> Row`. It raises `InvoiceError` subclasses. `columns` defaults to `STANDARD_COLUMNS`.
  - `extract_many(paths, columns=None, **same) -> List[Row]`:
    - Rows come back in input order.
    - One file's error is stored in `row.error` as `"<ErrorClass>: message"`. A non-InvoiceError exception is also caught and stored.
    - `len(paths) > max_files` raises `LimitError` before any work.
    - Files run in a `ThreadPoolExecutor(settings.concurrency)`.
  - `add_columns(row: Row, columns: List[Column], **same) -> Row` fills only the keys not already in `row.cells`. Edited cells and existing cells are untouched, and the checks run again.
  - `__init__` exports:
    - `extract`, `extract_many`, `add_columns`
    - `Column`, `Template`, `STANDARD_COLUMNS`, `column`
    - `Row`, `Cell`, `Box`, `Check`
    - `export` (from Task 8; import it once Task 8 exists)
    - all the errors, and `__version__`
- **Flow inside `_fill(path, kind, columns, settings, client)`:**
  - `file` cell: `Cell(value=Path(path).name, source="xml"|"text"|"vision")`.
  - `xml` / `zugferd`:
    - `parse_xml` fills the standard keys with `source="xml"`. The values are normalized with `raw` set to the XML string.
    - The remaining non-reserved columns go to `fill_columns` only if any remain. Their text is `xml_text`, plus the PDF text for zugferd.
    - zugferd boxes: try `locate(path, v)` for each `v` in `display_variants`. If none matches, there is no box and no warning.
  - `pdf`:
    - The text is `"\n".join(f"<!-- page {i} -->\n{t}")` over `text_pages`.
    - `fill_columns` runs, and its output goes through `normalize`.
    - For each value with a `source_text`, call `locate`. If it is None, add `Check("source_not_located", "warning", "Source text not found in the PDF")` to the cell.
  - `scan`:
    - The text is `doc_extract.extract(path, base_url, model=settings.vision(), api_key, timeout, max_pages, max_file_mb)`.
    - `fill_columns` uses `settings.model`.
    - Then `vision_boxes` runs per page that has values, using `render_page` and `settings.vision()`.
    - `doc_extract` errors are re-raised as `ModelError`. If the message suggests that the image input was not supported, add `"; set a vision-capable model with --vision-model or INVOICE_EXTRACT_VISION_MODEL"`.
  - `make_client` is called lazily, only when the LLM is needed. This makes Review Focus #5 work.
  - Finally, `row.checks = run_checks(row, columns)`.

- [ ] **Step 1: Write the failing tests:**
  - `test_zugferd_no_model_no_requests`: ZUGFeRD and XRechnung fixtures with only the standard columns and no settings. Both rows have the status `ok`, `gross` is a Decimal, and `server.requests == []`.
  - `test_zugferd_custom_column_uses_llm`: a custom column triggers exactly one request whose content contains `CrossIndustryInvoice`.
  - `test_pdf_flow_values_and_boxes`: the fake reply is based on the Task 3 PDF. `invoice_no` gets a box, and `source="text"`.
  - `test_pdf_source_not_located_warning`: the fake `source_text` `"NOPE"` gives the cell check `source_not_located` and the row status `warning`.
  - `test_scan_flow`:
    - The PNG fixture and fake server answer transcription requests with `"Invoice R-9"`, fill requests with the JSON, and box requests with the boxes.
    - `source="vision"`, and `invoice_no` gets a box.
  - `test_extract_many_isolates_errors`: `[good.pdf, broken.pdf, empty.pdf(0 bytes)]` gives 3 rows in order. Only the first has `error is None`.
  - `test_extract_many_limit`: 3 paths with `max_files=2` raise `LimitError`.
  - `test_add_columns_keeps_edits`: a row with an edited `supplier` cell keeps its value after `add_columns(row, [column("Customer No.")])`, and the request contains only `customer_no`.
  - `test_missing_model_for_pdf_raises_config_error`
- [ ] **Step 2: Run `pytest tests/test_core.py -q`.** Expect FAIL.
- [ ] **Step 3: Implement `core.py` and the exports.**
- [ ] **Step 4: Run `pytest -q | tail -n 30`.** Expect PASS.
- [ ] **Step 5: Commit:** `feat: add extraction pipeline`

### Task 8: Export

**Files:**
- Create: `src/invoice_extract/export.py`
- Test: `tests/test_export.py`

**Interfaces:**
- `export(rows: List[Row], path, columns: Optional[List[Column]] = None, format: Optional[str] = None) -> Path`:
  - The format comes from the suffix (`.xlsx`, `.csv`, `.json`) unless `format` is given. An unknown format raises ConfigError.
  - Columns default to `STANDARD_COLUMNS`. The `file` and `status` cells come from `row.path` name and `row.status`. A row with an error gets `status="error"` and empty values.
  - **xlsx:**
    - Sheet `Invoices`: a header of labels, then one row per invoice. Dates become date cells (`yyyy-mm-dd`), Decimals become number cells (`#,##0.00`), the header is bold and frozen, and the widths are auto-sized up to 50.
    - Sheet `Details`: the header is `File, Column, Value, Raw text, Source, Page, Edited, Checks`. `Checks` is `"; ".join(f"{level}: {message}")`. The row-level checks get the column `(invoice)`, and errors get the column `(error)`.
    - Any string starting with `= + - @` (or a tab or CR) is written as an explicit string (`cell.data_type = "s"`), with a leading `'` quote prefix so Excel shows it as text.
  - **CSV:** UTF-8 with BOM (Excel-friendly), `,` delimiter, Invoices columns only. Strings starting with `= + - @ \t \r` get the prefix `'`.
  - **JSON:** `{"columns": [...asdict], "rows": [row.to_dict()]}`, `indent=2`, `ensure_ascii=False`.

- [ ] **Step 1: Write the failing tests:**
  - `test_xlsx_two_sheets_and_types`: loaded with openpyxl, `Invoices!` gross is numeric `1234.5` and invoice_date is a `datetime`. `Details` has a header and a row per cell.
  - `test_xlsx_formula_injection`: a supplier `"=HYPERLINK(\"x\")"` gives a cell `data_type == "s"`, and the value is not treated as a formula (`not str(v).startswith("=")` or the quote prefix is set).
  - `test_csv_bom_and_injection`: the bytes start with `\xef\xbb\xbf`, and the supplier field is `'=HYPERLINK...`.
  - `test_json_roundtrip`
  - `test_error_row_exported_as_error`
  - `test_unknown_format`
- [ ] **Step 2: Run `pytest tests/test_export.py -q`.** Expect FAIL.
- [ ] **Step 3: Implement `export.py` and export it from `__init__`.**
- [ ] **Step 4: Run it again.** Expect PASS.
- [ ] **Step 5: Commit:** `feat: export to Excel, CSV and JSON`

### Task 9: CLI

**Files:**
- Create: `src/invoice_extract/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- `main(argv: Optional[List[str]] = None) -> int`
- **Extract mode** (default): `invoice-extract FILES...` with these flags:
  - `-o/--output`. Default `invoices.xlsx` in the current folder. An existing default file gets ` (1)`. An explicit `-o` overwrites.
  - `--template PATH`
  - `--column LABEL` (repeatable, appended after the template or standard columns)
  - `--base-url`, `--model`, `--vision-model`, `--api-key`
  - `--max-files`, `--max-file-mb`, `--max-pages`, `--concurrency`, `--timeout`
  - `--version`
- **Output:**
  - One line per file on stderr: `<name>: ok|warning|error[: message]`.
  - Then `Wrote <path> (N files, M to check)`.
  - The exit code is 1 if any row has an error (including a per-file missing model), otherwise 0.
  - Errors before any file runs (too many files, bad template, unknown output format) print `Error: ...` and return 2.
- **Serve mode:** `invoice-extract serve [--host 127.0.0.1] [--port 8000]` plus the model flags.
  - It imports `uvicorn` and `invoice_extract.web.app` lazily. On ImportError it prints `The web UI needs extra packages: pip install "invoice-extract[ui]"` and returns 2.
  - It runs `uvicorn.run(create_app(settings), host, port)`.
  - On a non-loopback host it prints the warning `Warning: the UI has no login. Only expose it on a trusted network.`

- [ ] **Step 1: Write the failing tests:**
  - `test_cli_xml_only_no_model`: an XRechnung file with `-o out.xlsx` gives exit 0, and the file exists.
  - `test_cli_column_flag_and_template`: the fake server request contains the custom key.
  - `test_cli_exit_code_on_error`: a broken PDF gives exit 1, and stderr contains `broken.pdf: error`.
  - `test_cli_missing_model_for_pdf`: exit 1 for that file, and the message names `--model`.
  - `test_cli_serve_without_ui_extra`: monkeypatch the import to fail. Exit 2 and the pip hint.
  - `test_default_output_not_overwritten`
- [ ] **Step 2: Run `pytest tests/test_cli.py -q`.** Expect FAIL.
- [ ] **Step 3: Implement `cli.py`.**
- [ ] **Step 4: Run it again.** Expect PASS.
- [ ] **Step 5: Commit:** `feat: add command-line interface`

### Task 10: Web API

**Files:**
- Create: `src/invoice_extract/web/__init__.py`, `src/invoice_extract/web/app.py`
- Test: `tests/test_web.py` (FastAPI `TestClient`)

**Interfaces:**
- `create_app(settings: Settings, workdir: Optional[Path] = None) -> FastAPI`
  - The state is one in-memory `Workspace`:
    - `files: Dict[str, FileEntry(id, name, path, kind, state: "pending"|"running"|"done", row: Optional[Row])]`
    - `columns: List[Column]`, starting as a copy of `STANDARD_COLUMNS`
  - Uploads go to a `tempfile.TemporaryDirectory`, removed on shutdown.
  - Extraction runs in a background `ThreadPoolExecutor(settings.concurrency)` and is guarded by a `threading.Lock`.
- **Endpoints** (all JSON unless noted):
  - `GET /` serves `static/index.html`. `/static/*` is mounted.
  - `POST /api/files` (multipart `files[]`): saves the files, detects the kind (errors are kept on the row), and starts extraction. The response is `{"files": [...]}`. More than `max_files` in total gives 413.
  - `DELETE /api/files`: clears everything.
  - `GET /api/state` returns:
    - `{"columns": [...], "files": [{id, name, kind, state, status, row}], "summary": {"files": n, "columns": m, "to_check": k}}`
    - `to_check` counts the rows with status warning or error.
  - `GET /api/files/{id}/pages` returns `{"count": n}`. `GET /api/files/{id}/pages/{n}.png` returns `image/png`, cached in memory.
  - `GET /api/files/{id}/pages/{n}/segments` returns `[{index, text, box}]`, and `[]` for scans.
  - `POST /api/columns` with `{label, hint?, type?, from?: {file_id, page, index}}`:
    - `from` builds the hint with `click_hint`.
    - It creates the key with `make_key` and runs `add_columns` on every done row in the background.
    - It returns the column.
  - `DELETE /api/columns/{key}`: removes the column from the list, but the cells stay in the rows. The keys `file` and `status` are allowed too.
  - `PATCH /api/files/{id}/cells/{key}` with `{value}`: sets `Cell(value=..., raw=old raw, source="manual", edited=True)`. Then normalize (by column type) and run the row checks again.
  - `GET /api/export?format=xlsx|csv|json` returns the file download from `export()`, named `invoices.<ext>`.
  - `GET /api/template` returns the YAML download of the current columns. `POST /api/template` (multipart) replaces the columns and fills the missing keys.
  - A missing model on a PDF or scan shows up as that row's error text, not as an HTTP error.
  - Every path id is validated against the workspace. An unknown id gives 404. Filenames are never used as paths: the stored name is `<uuid><suffix>`.

- [ ] **Step 1: Write the failing tests:**
  - `test_upload_xml_and_state`: upload an XRechnung, poll `/api/state` until `done` (timeout 5 s). The status is `ok`, and the summary shows `files == 1`.
  - `test_page_png_and_segments`: the PDF fixture gives a PNG content type, and the segments contain `"40711"`.
  - `test_add_column_from_click`: the request body to the fake server contains `"Kd.-Nr."`.
  - `test_patch_cell_marks_edited_and_rechecks`: patch `gross` to a wrong total. The row status becomes `error`, and `edited` is true.
  - `test_export_xlsx_download`
  - `test_template_roundtrip`
  - `test_unknown_id_404`
  - `test_upload_limit_413`
- [ ] **Step 2: Run `pytest tests/test_web.py -q`.** Expect FAIL.
- [ ] **Step 3: Implement `web/app.py`.**
- [ ] **Step 4: Run it again.** Expect PASS.
- [ ] **Step 5: Commit:** `feat: add web API for the review UI`

### Task 11: Web frontend

**Files:**
- Create: `src/invoice_extract/web/static/index.html`, `app.js`, `style.css`
- Test: `tests/test_web.py::test_index_served` (GET `/` contains `<title>invoice-extract</title>`, and `/static/app.js` gives 200)

**Interfaces:**
- Consumes the Task 10 endpoints only.
- **Layout** (visual reference is the mockup at `/private/tmp/claude-501/-Users-hamidmergan-Projects-oss-tools/74bf5e2e-9bc8-4973-bcbc-1daa138aad95/scratchpad/canvas/project/Main.dc.html`, read-only, never copied into the repo):
  - **Top bar:** the app name, the drop zone or "Add files" button, "Export ▾" (xlsx, csv, json) and "Clear".
  - **File strip:** one chip per file with a status dot (ok green, warning amber, error red, pending grey). Clicking a chip selects that file.
  - **Left:** the page image (`<img>`) with an absolutely positioned `<svg>` overlay and page arrows.
    - Value boxes are drawn in the accent colour, with a label tag showing the column label.
    - For PDFs, clicking an empty spot fetches the segments and highlights them on hover. Clicking a segment opens a small popover "Column title" with a "Create column" button, which calls `POST /api/columns` with `from`.
  - **Right panel:**
    - **"Columns (N)" card:** chips with an × to remove, "+ Add column" (by name), "Save as template" (download) and "Load template" (upload).
    - **"Values" list** for the selected file: label, value, source badge, and an edited marker.
    - **"Checks" list:** level icon plus message.
  - **Bottom:** the result table, files × columns.
    - Clicking a cell edits it in place. Enter calls PATCH, and Esc cancels.
    - Edited cells show a dot.
    - A summary line above it reads `"6 files · 10 columns · 2 to check"`.
- **Style:**
  - The system font stack, neutral greys and one accent `#2f6fed`.
  - Colours are CSS custom properties on `:root`, with a dark set under `@media (prefers-color-scheme: dark)`.
  - Radius 8px, no shadows heavier than `0 1px 2px`.
  - The layout stacks into one column under 900px.
- **Behaviour:**
  - Poll `GET /api/state` every 1 s while any file is `pending` or `running`, otherwise stop.
  - All text is set via `textContent`, never `innerHTML` with data.
  - No framework or build step, about 500 lines of JS total.

- [ ] **Step 1: Write `test_index_served`, then run it.** Expect FAIL.
- [ ] **Step 2: Implement the three static files.**
- [ ] **Step 3: Run `pytest tests/test_web.py -q`.** Expect PASS.
- [ ] **Step 4: Manual smoke test.**
  - Generate samples: `python -c "import tests.pdfgen as g; g.write_samples('samples/')"`. This helper writes the PDF, ZUGFeRD and XRechnung fixtures to `samples/` (gitignored).
  - Run `invoice-extract serve --base-url <local Ollama> --model <model>` and open http://127.0.0.1:8000.
  - Upload the samples. Check the boxes, click a value to create a column, edit a cell, export the xlsx, and check light and dark mode.
- [ ] **Step 5: Commit:** `feat: add review UI`

### Task 12: Docker, README, release prep

**Files:**
- Create: `Dockerfile`, `.dockerignore` (`.git`, `tests`, `samples`, `docs`, `__pycache__`, `.DS_Store`), `README.md`
- Test: `docker build -t invoice-extract .` (if Docker is available), plus `pytest -q`

**Interfaces:**
- **Dockerfile:**
  - `FROM python:3.12-slim`, `WORKDIR /app`, `COPY . .`
  - `RUN pip install --no-cache-dir ".[ui]"`
  - `EXPOSE 8000`, a non-root `USER`
  - `CMD ["invoice-extract", "serve", "--host", "0.0.0.0", "--port", "8000"]`
  - The settings come only from `INVOICE_EXTRACT_*` env vars.
- **README.md** follows `doc-extract/README.md` section by section:
  1. Title and opening paragraph, then a "supports" list: ZUGFeRD/Factur-X, XRechnung (UBL, CII), digital PDF, scans and photos.
  2. **Prerequisites:**
     - Python 3.9+ and an OpenAI-compatible server.
     - A **vision-capable model** for scans and photos. Text-only models are enough for digital PDFs, and e-invoices need no model.
     - The API key is optional.
     - The privacy paragraph about self-hosting.
  3. **Installation:** `pip install invoice-extract`, `pip install "invoice-extract[ui]"`, and from source.
  4. **Usage:**
     - **Command-Line:** examples with `--base-url http://localhost:11434/v1 --model your-model`, `--column`, `--template`, `-o`.
     - **Web UI:** `invoice-extract serve`, plus what clicking a value does.
     - **Docker:** `docker build` / `docker run -p 8000:8000 -e INVOICE_EXTRACT_BASE_URL=http://host.docker.internal:11434/v1 -e INVOICE_EXTRACT_MODEL=...`, with the warning about no login.
     - **Python API:** `extract_many`, `export`, `add_columns`.
     - **Templates:** a YAML example.
     - **Settings:** a table like the one in doc-extract, including `vision_model` and `max_files` 100.
  5. **Checks and statuses:** what ok, warning and error mean. One file is one invoice.
  6. **Limitations:**
     - Quality depends on the model.
     - Line items are not extracted.
     - Boxes on scans are best effort.
  7. **Running Tests** and **License (MIT)**.

- [ ] **Step 1: Write the Dockerfile, .dockerignore and README.**
- [ ] **Step 2: Run `pytest -q | tail -n 20`.** Expect all PASS.
- [ ] **Step 3: Run `docker build -t invoice-extract . 2>&1 | tail -n 20`.** Expect success, or skip if Docker is not installed and report that.
- [ ] **Step 4: Run `python -m build 2>&1 | tail -n 5`.** Then check the wheel contains `web/static/index.html` (`unzip -l dist/*.whl | grep static`).
- [ ] **Step 5: Commit:** `docs: add README and Dockerfile`
- [ ] **Step 6: Push only when Hamid says "push".**
  - First grep for the lab URL.
  - Then run `gh repo create hamidmergan/invoice-extract --public --description "<pyproject description>" --source=. --remote=origin --push`.
