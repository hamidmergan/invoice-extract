# invoice-extract design

Date: 2026-10-21
Status: approved

## Intent

invoice-extract turns a stack of invoices (PDFs, scans, photos, e-invoices) into one table and exports it to Excel. The user decides which columns the table has. They can pick a value on one invoice ("this is the customer number") and the tool then finds the same field on all other invoices by meaning, not by position.

Audience: open-source users, small offices, freelancers, universities. Many run their own model (Ollama, vLLM, LM Studio, a university server). Their data must stay on their own network if they want that, and the tool must run on an ordinary laptop. Heavy local AI (Docling, PyTorch) is ruled out for that reason.

The tool builds on doc-extract (`doc-extract-llm` on PyPI), which already turns scans and images into Markdown with a vision model.

## Goals

- Read invoices in three ways, cheapest first:
  1. **E-invoice:** ZUGFeRD / Factur-X (PDF with embedded XML) and XRechnung (pure XML, UBL or CII). The values are read from the XML with no AI. They are exact and free.
  2. **Digital PDF:** the text and word positions come from pypdfium2, and an LLM maps the text to the columns.
  3. **Scan or photo:** doc-extract transcribes the page with the vision model, and an LLM maps the text to the columns.
- Let the user choose the columns. Fixed standard columns come first, and the user can add their own:
  - by name only ("Customer No."), or
  - by clicking a value on an invoice and naming the column.
- Show where each value comes from: a box on the page image for digital PDFs and ZUGFeRD, and best-effort boxes for scans.
- Run checks with plain Python and no AI, then flag the invoices that need a look.
- Export to Excel (.xlsx), CSV and JSON.
- Save column sets as reusable templates.
- Offer three ways in: a Python library (the core), a thin CLI, and a local web UI.
- Provide a Dockerfile for teams that host the UI on a server.

## Non-goals (v1)

- Local Docling or any other local layout model.
- docling-serve support (a possible later option: `--layout-url`).
- Line items as table rows. v1 has one row per invoice. Line items may come later.
- Splitting one file into several invoices. In v1 one file is exactly one invoice, and the README says so.
- Writing or sending e-invoices.
- Accounting integrations (DATEV, Lexoffice and similar).
- User accounts, logins, a database or job queues.
- A published image on ghcr.io (later).

## Anti-goals (a failure even if it works)

- Needing a GPU or a large download on the user's machine.
- Being tied to one AI provider, or having special code for Claude or OpenAI.
- Silently inventing values. An empty cell is better than a wrong one, and every value must be traceable to its source.
- Uploading files anywhere except the configured `base_url`.
- Working only for German invoices or one company's layout.
- Any code, design or branding from KIS.

## Constraints (from the oss-tools rules)

- AI access only through an OpenAI-compatible API: `base_url`, `model`, optional `api_key`.
  - There is no default for `base_url` or `model`. If either is missing, the error says what to set (as in doc-extract).
  - Without an `api_key`, a placeholder is used.
  - Settings can be given as a function argument, a CLI flag (`--base-url`, `--api-key`, `--model`) or an environment variable with the prefix `INVOICE_EXTRACT_`.
  - The model must be vision-capable if scans or photos are used. Digital PDFs and e-invoices work with a text-only model, and e-invoices need no model at all.
- Python 3.9+, pip package, library core with a thin CLI wrapper.
- Everything public is in English: code, comments, messages, CLI help, README and tests.
- The README follows the structure and tone of `doc-extract/README.md`. It mentions the vision model requirement and the privacy benefit of self-hosted models.
- MIT license, own public repo `hamidmergan/invoice-extract`.
- `.gitignore` contains `.DS_Store`.
- No real invoices in the repo. Test fixtures are generated.

## Decided

### Packaging

- Import name `invoice_extract`, CLI command `invoice-extract`.
- `pip install invoice-extract` installs the library and CLI only: doc-extract-llm, openai, pypdfium2, openpyxl, PyYAML, defusedxml.
- `pip install "invoice-extract[ui]"` also installs FastAPI and Uvicorn. `invoice-extract serve` then opens the UI at `http://127.0.0.1:8000`.
- The UI's HTML, JS and CSS ship inside the package in every install. They are small.
- Docker: one `Dockerfile` on `python:3.12-slim` that installs `.[ui]` and runs `invoice-extract serve --host 0.0.0.0`. Configuration is through environment variables. The README shows `host.docker.internal` for an Ollama instance on the host. There is no docker-compose.

### Pipeline per file

1. **Detect the type.**
   - A `.xml` file is an XRechnung (UBL or CII).
   - A PDF with an embedded `factur-x.xml`, `zugferd-invoice.xml` or `xrechnung.xml` is a ZUGFeRD invoice.
   - A PDF with a usable text layer is a digital PDF.
   - Anything else (an image, or a PDF without text) is a scan.
2. **Get the values.**
   - E-invoice: XML fields map to the standard columns (EN 16931 terms). Custom columns that the XML mapping cannot fill go to the LLM together with the XML text.
   - Digital PDF: the pypdfium2 text, page by page, goes to the LLM with the column list.
   - Scan: doc-extract's Markdown goes to the LLM with the column list.
3. **LLM call.**
   - One JSON request per invoice. The input is the column list (name plus optional hint) and the invoice text.
   - The answer has, per column: `value`, plus `source_text` (the exact text the value was taken from) and `page`.
   - The model must answer `null` when a field is not on the invoice.
4. **Locate.**
   - Digital PDF and ZUGFeRD: `source_text` is searched in the pypdfium2 text, and its character boxes become the value's box.
   - Scan: one extra vision request per page asks for boxes of the found values. If it fails or returns nonsense, there are no boxes, and the values stay.
5. **Normalize.** Dates become ISO 8601, amounts become decimals with a dot, and IBANs lose their spaces. The original text is kept as well.
6. **Check** (plain Python):
   - Net plus VAT equals gross, within 0.01.
   - The IBAN passes the mod-97 check.
   - Dates parse, and the due date is not before the invoice date.
   - Required columns are present.
   - Every check gives `ok`, `warning` or `error`, and the invoice status is the worst of them.

### Standard columns

These are on by default and can be removed:

File, Supplier, Invoice No., Invoice Date, Due Date, Net, VAT, Gross, Currency, IBAN, Status.

Each has a fixed key, an English label and an XML mapping (CII and UBL). Users can rename the labels.

### Custom columns by click

- In the UI, the page image of a digital PDF has an SVG overlay. Each overlay box is a pypdfium2 text segment (a word or line).
- The user clicks a box, enters a column title and confirms.
- The column stores its title plus a hint built from the clicked value and the label text next to it, for example: label "Kd.-Nr.", example value "40711".
- This hint goes into the LLM prompt for all invoices, so the field is found by meaning on other layouts too.
- On a scan, the user adds a column by name. There is no click, because a scan has no reliable boxes.

### Templates

A template is a YAML file with the columns (key, label, hint, required, type such as text, date, amount or iban). It is saved and loaded in the UI and the CLI (`--template`).

### UI

The current mockup is a review screen:

- A file list at the top with a status per file.
- The invoice page on the left, with boxes around the found values.
- On the right, a "Columns" card with a count, chips, "+ Add column" and "Save as template"; below it, the values for this invoice and the check results.
- The result table at the bottom (files × columns), with a summary such as "6 files · 10 columns · 2 to check".
- An export button for Excel, CSV and JSON.
- Values can be edited by hand in the table before export, and an edited value is marked.

The look is plain and modern, with no KIS design:

- Neutral colours with one accent.
- The system font stack.
- Light and dark mode.
- One small CSS file.
- No front-end framework or build step: plain HTML and JS.

The server is FastAPI:

- State lives in memory and a temporary directory for the life of the process. There is no database.
- It binds to 127.0.0.1 by default.
- The README warns that `--host 0.0.0.0` (Docker) has no login and should only be used on a trusted network.

### CLI

```
invoice-extract FILES... [-o out.xlsx|.csv|.json] [--template t.yaml]
                [--column "Customer No."]... [--base-url ...] [--model ...] [--api-key ...]
invoice-extract serve [--host 127.0.0.1] [--port 8000] [model flags]
```

### Library

```python
from invoice_extract import extract, extract_many, Column, Template, export

rows = extract_many(paths, columns=[...], base_url=..., model=...)
export(rows, "out.xlsx")
```

- `extract` raises errors.
- `extract_many` keeps going and stores the error per file, as in doc-extract.
- Each result row holds per column: value, original text, page, box (or None), source (`xml`, `text`, `vision`) and the check results.

### Settled after builder review (author's calls, open to change)

- **Source not found.** If `source_text` is not found in the PDF text, the value stays without a box. It gets a warning "source not located" and the status becomes `warning`.
- **XML values.** Source `xml` counts as traceable. A box is best effort: the formatted value (for example `21.10.2026` or `1.234,50`) is searched in the page text. If it is not found, there is no box and no warning.
- **doc-extract stays unchanged.** Only its public `extract()` is used, called once per file, so `max_files` does not apply. Page images for the UI and for the vision box request are rendered in invoice-extract with pypdfium2 and Pillow.
- **Adding a column after extraction.** Only the new columns are requested for each file. Existing values and manual edits are kept. The LLM call therefore takes any subset of columns. The rule of one request per invoice applies to the first run.
- **Normalization.**
  - The LLM returns a normalized value and the raw text. It sees the context: language, currency and the date style used elsewhere on the invoice.
  - Python parses the raw text as well. If the two disagree, or the raw text is ambiguous (`03/04/2026`, `1.234`), the normalized value is kept and the check is `warning`.
  - Values are never dropped silently.
- **Models.** `model` is used for everything. An optional `vision_model` (`--vision-model`, `INVOICE_EXTRACT_VISION_MODEL`) overrides it for scans and box requests. If a scan fails because the model has no vision, the error for that file says to set a vision-capable model.
- **Export.**
  - xlsx sheet 1 has one row per invoice: the values plus Status.
  - xlsx sheet 2 "Details" has one row per value: file, column, value, raw text, source, page, edited, and the check results.
  - CSV holds the values plus Status only.
  - JSON holds everything.

## Left to the builder

- The PyPI name. Use `invoice-extract` if it is free, otherwise `invoice-extract-llm` (as with doc-extract).
- The module layout, the names of internal functions, and the exact prompt wording.
- How the threshold for "has a text layer" is set.
- The exact XPaths for the CII and UBL mapping, and which XRechnung and ZUGFeRD profiles get fixtures.
- How to make test PDFs with an embedded XML, as long as it is a test-only dependency.
- The default limits. doc-extract allows 20 files; invoices need more, so start around 100.
- The UI details beyond the mockup, such as spacing, icons and keyboard use.
- Concurrency across files.
