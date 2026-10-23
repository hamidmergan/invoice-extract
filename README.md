# invoice-extract

The invoices you receive go in, one checked table for your bookkeeping comes out.

Drop in everything that lands in your inbox: PDFs, scans, photos, **XRechnung** and **ZUGFeRD** e-invoices. invoice-extract puts them into one table with the columns a bookkeeper expects, checks every invoice (totals, VAT rate, IBAN, dates, mandatory fields, duplicates), shows where on the page each value was found, and exports Excel, CSV or JSON. E-invoices are read exactly from their XML, without a model, and shown like a paper invoice, ready to print. PDFs and scans are read by a language model of your choice.

invoice-extract currently supports:

- XRechnung (UBL and CII)
- ZUGFeRD / Factur-X (PDF with embedded XML)
- Digital PDF
- Scans and photos (PDF without text, PNG, JPG, WEBP, GIF, BMP, TIFF)

## Prerequisites

- Python 3.9 or newer
- For PDFs and scans: a server with an OpenAI-compatible API (for example Ollama, vLLM, LM Studio, a university server or a cloud provider). E-invoices need no model at all.
- A **vision-capable model** for scans and photos. A text-only model is enough for digital PDFs.
- An API key is optional. Many self-hosted servers do not need one.

If you run your own model, your invoices stay on your own network. Nothing leaves your machine or data center.

## Installation

invoice-extract is installed from GitHub:

```bash
pip install git+https://github.com/hamidmergan/invoice-extract.git
```

With the web page:

```bash
pip install "invoice-extract[ui] @ git+https://github.com/hamidmergan/invoice-extract.git"
```

Alternatively, you can install it from a local copy:

```bash
git clone https://github.com/hamidmergan/invoice-extract.git
cd invoice-extract
pip install ".[ui]"
```

## Usage

### Web page

```bash
invoice-extract serve --base-url http://localhost:11434/v1 --model your-model
```

Open http://127.0.0.1:8000 and drop your invoices onto the page.

- **The table** has one row per invoice: Type, Supplier, Supplier VAT ID, Invoice No., Invoice Date, Due Date, Description, Net, VAT Rate, VAT, Gross, Currency, IBAN and Status. Click a cell to correct it; edited cells are marked and the checks run again.
- **The page** below shows the selected invoice with a box around every value the model read. Click a value on the right to jump to it. An XRechnung has no page, so it is shown as an invoice sheet instead; a ZUGFeRD PDF shows its page and, behind "Show e-invoice data", the same sheet. "Print" prints the sheet alone, so your browser can save it as a PDF.
- **Checks** lists what to look at: a total that does not add up, a VAT amount that does not match the rate, an invalid IBAN, a missing invoice number, a supplier VAT ID missing on an invoice above 250.00, or the same invoice number as another file.
- **Export** writes the table as Excel, CSV or JSON.

Want other columns? Start the page with a template (see [Templates](#templates)):

```bash
invoice-extract serve --template my-columns.yaml --base-url ... --model ...
```

The page has no login. It listens on 127.0.0.1 by default and only answers requests for localhost, so other websites cannot reach it. Only use `--host 0.0.0.0` on a trusted network.

### Command-Line

Put many invoices into one Excel file:

```bash
invoice-extract *.pdf invoices/*.xml --base-url http://localhost:11434/v1 --model your-model
```

This writes `invoices.xlsx`. If it already exists, a number is added instead (`invoices (1).xlsx`). A file named with `-o` is overwritten. Choose the format with the suffix (`.xlsx`, `.csv` or `.json`):

```bash
invoice-extract *.pdf -o october.csv --base-url http://localhost:11434/v1 --model your-model
```

Add your own columns to the standard ones, or use exactly the columns of a template:

```bash
invoice-extract *.pdf --column "Cost Center" --base-url ... --model ...
invoice-extract *.pdf --template my-columns.yaml --base-url ... --model ...
```

Use a different model for scans:

```bash
invoice-extract scan.jpg invoice.pdf --base-url http://localhost:11434/v1 \
  --model your-model --vision-model your-vision-model
```

E-invoices work without any model settings:

```bash
invoice-extract xrechnung.xml zugferd.pdf
```

Each file gets one line on stderr with its status. If a file cannot be read, the others continue and the error is shown in that file's row. The exit code is 1 if at least one file failed, and 2 for a setup error (missing template, unknown format, missing output folder, too many files).

### Docker

```bash
docker build -t invoice-extract .
docker run -p 8000:8000 \
  -e INVOICE_EXTRACT_BASE_URL=http://host.docker.internal:11434/v1 \
  -e INVOICE_EXTRACT_MODEL=your-model \
  invoice-extract
```

The container starts the web page on port 8000 and reads its settings from the `INVOICE_EXTRACT_*` environment variables. The page has no login, so do not expose the port to the internet.

### Python API

```python
from invoice_extract import export, extract_many

rows = extract_many(["a.pdf", "b.xml", "scan.jpg"], base_url="http://localhost:11434/v1", model="your-model")
for row in rows:
    print(row.path, row.status, row.error or {key: cell.value for key, cell in row.cells.items()})
export(rows, "invoices.xlsx")
```

`extract_many` fills the standard columns, keeps the input order and checks for duplicates across the files. If one file fails, the others continue and the error is stored in that row. Each cell has `value`, `raw` (the text as printed), `page`, `box` (the position on the page, from 0 to 1), `source` (`xml`, `text`, `vision` or `manual`) and `checks`.

Your own columns:

```python
from invoice_extract import STANDARD_COLUMNS, column, extract_many

columns = STANDARD_COLUMNS + [column("Order No.", hint="starts with PO-")]
rows = extract_many(["a.pdf", "b.xml"], columns, base_url=..., model=...)
```

Add columns later without reading the whole invoice again:

```python
from invoice_extract import add_columns, column

rows = [add_columns(row, [column("Customer No.")], base_url=..., model=...) for row in rows]
```

Read an e-invoice as a document, with parties, lines, tax groups, totals and payment details:

```python
from invoice_extract.einvoice import parse_document

doc = parse_document(open("xrechnung.xml", "rb").read())
print(doc["seller"]["name"], doc["totals"]["gross"], doc["payment"]["iban"])
```

### Templates

A template is a YAML file with a list of columns. `label` is required. `hint` helps the model, `type` (`text`, `date`, `amount`, `iban`, `percent` or `doctype`) controls normalization and checks, `role` gives a column the meaning of a standard column so the checks apply to it, and `kind` is `value` (default), `text` or `table`. A table lists its `table_columns`.

```yaml
name: my-columns
columns:
  - label: Lieferant
    role: supplier
    required: true
  - label: Brutto
    type: amount
    role: gross
  - label: Cost Center
    hint: the cost center printed next to the order number
  - label: Line Items
    kind: table
    table_columns: [Description, Quantity, Unit Price, Amount]
```

Own columns are always framed by File and Status. The standard columns and their roles are `doc_type`, `supplier`, `supplier_vat_id`, `invoice_no`, `invoice_date`, `due_date`, `description`, `net`, `vat_rate`, `vat`, `gross`, `currency` and `iban`. The standard columns of an e-invoice, including the line items table, come straight from the XML.

### Export

- **Excel:** sheet "Invoices" has one row per invoice, plus Status. Sheet "Details" has one row per value with the raw text, source, page and checks. A table column (for example line items) gets its own sheet with one row per table row.
- **CSV:** the invoices table in the named file. A table column goes into its own file next to it, `<name>-<column>.csv`; on the web page, such an export is downloaded as one ZIP.
- **JSON:** every column of every invoice.

Values that start with `=`, `+`, `-` or `@` are written as text in Excel and CSV, so a hostile invoice cannot smuggle a formula into your spreadsheet.

### Settings

Order: argument, then environment variable, then default.

| Argument / flag | Environment variable | Default |
| --- | --- | --- |
| `base_url` / `--base-url` | `INVOICE_EXTRACT_BASE_URL` | none, required for PDFs and scans |
| `model` / `--model` | `INVOICE_EXTRACT_MODEL` | none, required for PDFs and scans |
| `vision_model` / `--vision-model` | `INVOICE_EXTRACT_VISION_MODEL` | same as `model` |
| `api_key` / `--api-key` | `INVOICE_EXTRACT_API_KEY` (then `OPENAI_API_KEY`) | optional |
| `max_files` / `--max-files` | `INVOICE_EXTRACT_MAX_FILES` | 100 |
| `max_file_mb` / `--max-file-mb` | `INVOICE_EXTRACT_MAX_FILE_MB` | 20 |
| `max_pages` / `--max-pages` | `INVOICE_EXTRACT_MAX_PAGES` | 50 |
| `concurrency` / `--concurrency` | `INVOICE_EXTRACT_CONCURRENCY` | 4 |
| `timeout` / `--timeout` | `INVOICE_EXTRACT_TIMEOUT` | 120 seconds |

If a limit is exceeded, the file is rejected with a message naming the limit and the setting. Nothing is silently truncated.

## Checks and statuses

Every invoice gets a status. The checks do not use the model.

- **ok**: all checks passed.
- **warning**: something needs a look. The VAT amount does not match the VAT rate, the due date is before the invoice date, a required value is missing, a value does not match the text printed on the page, the supplier VAT ID is missing on an invoice above 250.00 (small-amount invoices up to 250.00 need none), or another file has the same supplier and invoice number.
- **error**: net + VAT does not equal gross, the IBAN checksum is wrong, or the file could not be read.

One file is one invoice. A PDF with several invoices in it becomes one row.

## Writing e-invoices

For the other direction, when a customer wants an XRechnung or ZUGFeRD from you, the command line turns your own PDF invoice into one:

```bash
invoice-extract einvoice invoice.pdf --format xrechnung --profile company.yaml -o out/ \
  --base-url http://localhost:11434/v1 --model your-model
```

The model reads every field an EN 16931 e-invoice needs, the rules of EN 16931 and XRechnung (BR-DE) are checked, and only valid invoices are written: `<name>.xml` (XRechnung, CII syntax) or `<name>-zugferd.pdf` (the PDF with the XML embedded). The problems of the others are listed. A company profile fills your own seller data that is rarely printed:

```yaml
seller:
  name: My Shop GmbH
  street: Main Street 1
  postcode: "12345"
  city: Berlin
  country: DE
  vat_id: DE123456789          # or tax_number plus legal_id (e.g. "HRB 12345") if you have no VAT ID
  contact_name: Jane Doe
  phone: "+49 30 123456"
  email: billing@example.com
iban: DE89370400440532013000
payment_terms: Payable within 14 days.
```

Good to know: the XML was checked with the official [KoSIT validator](https://github.com/itplr-kosit/validator) (XRechnung 3.0.2). XRechnung asks for a buyer reference (Leitweg-ID), a seller contact with phone and e-mail, and the buyer's e-mail. A ZUGFeRD PDF is only as PDF/A-3 conform as your source PDF. Supported VAT categories: standard (S), zero rated (Z), exempt (E) and reverse charge (AE). From Python, use `invoice_extract.einvoicing.convert_many` and `write`.

## Limitations

Quality depends on the chosen model. A 7B model misses fields on longer invoices; a model of 20B or more (for example qwen3:30b-a3b) reads a two-page invoice well. A small model may misread poor scans. Check the rows marked warning or error, and spot-check the rest. E-invoices are always exact.

Boxes on scans come from the vision model and are best effort. Boxes on digital PDFs come from the text layer and are exact.

Incoming e-invoices are read and checked arithmetically, not validated against the full EN 16931 rule set; use the KoSIT validator for that.

## Running Tests

```bash
pip install -e ".[test]"
pytest
```

The tests run against a local fake server and need no real model.

## License

MIT
