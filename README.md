# invoice-extract

A lightweight Python tool for invoices, with a language model of your choice:

- **Normal invoices to e-invoices:** a PDF, scan or photo goes in, a valid **XRechnung** or **ZUGFeRD** comes out. The model reads every field an EN 16931 e-invoice needs, you check and complete them next to the page, and nothing invalid is written.
- **Invoices to one table:** many invoices become one Excel, CSV or JSON file.

For the table, you upload invoices, a language model of your choice finds everything on them (values, text blocks and tables such as the line items), and you only pick what goes into the export. Fields that mean the same thing on different invoices are merged into one column, and each value comes with the place on the page where it was found. E-invoices are read straight from their XML, without a model. The result is an Excel, CSV or JSON file.

invoice-extract currently supports:

- ZUGFeRD / Factur-X (PDF with embedded XML)
- XRechnung (UBL and CII)
- Digital PDF
- Scans and photos (PDF without text, PNG, JPG, WEBP, GIF, BMP, TIFF)

## Prerequisites

- Python 3.9 or newer
- A server with an OpenAI-compatible API (for example Ollama, vLLM, LM Studio, a university server or a cloud provider)
- A **vision-capable model** for scans and photos. A text-only model is enough for digital PDFs. E-invoices need no model at all.
- An API key is optional. Many self-hosted servers do not need one.

If you run your own model, your invoices stay on your own network. Nothing leaves your machine or data center.

## Installation

```bash
pip install invoice-extract
```

With the web UI:

```bash
pip install "invoice-extract[ui]"
```

Alternatively, you can install it from the source:

```bash
git clone https://github.com/hamidmergan/invoice-extract.git
cd invoice-extract
pip install ".[ui]"
```

## Usage

### E-invoices (XRechnung / ZUGFeRD)

German companies must receive e-invoices since 2025 and send them from 2027/2028. invoice-extract turns a normal invoice into one:

```bash
invoice-extract serve --base-url http://localhost:11434/v1 --model your-model
```

Open http://127.0.0.1:8000 and drop your invoices onto the page.

- **Read:** the model reads seller, buyer, number, dates, service period, payment (transfer or direct debit with mandate and creditor ID), the lines and the totals. Each read value has a box on the page.
- **Check:** the form on the right shows every field. Problems are listed on top and marked at the field, with the rule they come from (EN 16931 or the German XRechnung rules BR-DE). Lines and totals are recomputed from what you type, and the printed totals are compared with them.
- **Your company:** "My company" stores your own seller data (address, VAT ID, contact person, phone, e-mail, IBAN). It replaces the seller fields read from your invoices and fills what is rarely printed. Save it as YAML and load it next time.
- **Write:** choose **XRechnung** (CII XML) or **ZUGFeRD** (the original PDF with the same XML embedded; a scan or photo is turned into a PDF first). "Download" stays disabled until the invoice is valid for the chosen format.

From the command line:

```bash
invoice-extract einvoice invoice.pdf scan.jpg --format xrechnung --profile company.yaml -o out/ \
  --base-url http://localhost:11434/v1 --model your-model
```

Valid invoices are written as `<name>.xml` (XRechnung) or `<name>-zugferd.pdf` (ZUGFeRD); for the others the problems are listed. The exit code is 1 if any file could not be written. A company profile looks like this:

```yaml
seller:
  name: My Shop GmbH
  street: Main Street 1
  postcode: "12345"
  city: Berlin
  country: DE
  vat_id: DE123456789
  contact_name: Jane Doe
  phone: "+49 30 123456"
  email: billing@example.com
iban: DE89370400440532013000
payment_terms: Payable within 14 days.
```

From Python:

```python
from invoice_extract.einvoicing import convert_many, write

for conversion in convert_many(["invoice.pdf"], base_url=..., model=...):
    problems = [i for i in conversion.issues("xrechnung") if i.level == "error"]
    if not problems:
        write(conversion, "xrechnung", "out/")
```

Good to know:

- XRechnung asks for more than most printed invoices show: a buyer reference (Leitweg-ID, or any reference agreed with the buyer), a seller contact person, phone and e-mail, and the buyer's e-mail address. ZUGFeRD (EN 16931) shows these as warnings only.
- The XML follows EN 16931 in the CII syntax and was checked with the official KoSIT validator (XRechnung 3.0.2 configuration). For your own invoices, you can check the files the same way: [KoSIT validator](https://github.com/itplr-kosit/validator).
- A ZUGFeRD PDF contains a valid XML, but the PDF itself is only as PDF/A-3 conform as your source PDF.
- Not supported yet: allowances and charges, prepayments, other currencies than the one on the invoice, UBL output.
- An invoice that already is an e-invoice is reported as such and not converted.

### Web UI (table)

```bash
invoice-extract serve --base-url http://localhost:11434/v1 --model your-model
```

Then open http://127.0.0.1:8000/table and drop your invoices onto the page.

- On the left you see every field the model found in all invoices, grouped into values, text blocks and tables, with a count such as "6/10" (found in 6 of 10 invoices). Everything starts ticked. Untick what you do not need.
- On the right you see the current invoice with a coloured box around every field. Unticked fields stay visible but faded. Flip through the invoices with ‹ › or the arrow keys, and click a field name to jump to its box.
- At the bottom you see the export preview: one row per invoice, plus one tab per selected table (for example the line items). Click a cell to correct it; edited cells are marked and the checks run again.
- "Save as template" stores your selection. Next time, "Load template" makes the model sort new invoices into exactly these fields. "Use all fields" goes back to finding everything.
- "Export" writes Excel, CSV or JSON with the selected fields only.

The UI has no login. It listens on 127.0.0.1 by default and only answers requests for localhost, so other websites cannot reach it. Only use `--host 0.0.0.0` on a trusted network.

### Command-Line

Put several invoices into one Excel file with every field the model finds:

```bash
invoice-extract *.pdf invoices/*.xml --base-url http://localhost:11434/v1 --model your-model
```

This writes `invoices.xlsx`. If it already exists, a number is added instead (`invoices (1).xlsx`). A file named with `-o` is overwritten. Choose the format with the suffix (`.xlsx`, `.csv` or `.json`):

```bash
invoice-extract *.pdf -o october.csv --base-url http://localhost:11434/v1 --model your-model
```

Use exactly the fields of a template (for example one saved in the web UI):

```bash
invoice-extract *.pdf --template suppliers.yaml --base-url http://localhost:11434/v1 --model your-model
```

Or the standard columns (File, Supplier, Invoice No., Invoice Date, Due Date, Net, VAT, Gross, Currency, IBAN, Status) plus your own:

```bash
invoice-extract *.pdf --column "Order No." --column "Customer No." --base-url http://localhost:11434/v1 --model your-model
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

The container starts the web UI on port 8000 and reads its settings from the `INVOICE_EXTRACT_*` environment variables. The UI has no login, so do not expose the port to the internet.

### Python API

Find every field and merge them across the invoices:

```python
from invoice_extract import export, export_columns, extract_many, fields_of

rows = extract_many(["a.pdf", "b.xml", "scan.jpg"], base_url="http://localhost:11434/v1", model="your-model")
fields = fields_of(rows)  # one Column per field found, most frequent first
for row in rows:
    print(row.path, row.status, row.error or {key: cell.value for key, cell in row.cells.items()})

wanted = [f for f in fields if f.kind != "text"]  # your selection
export(rows, "invoices.xlsx", export_columns(wanted))
```

Or fill fixed columns:

```python
from invoice_extract import STANDARD_COLUMNS, column, extract_many

columns = STANDARD_COLUMNS + [column("Order No.", hint="starts with PO-")]
rows = extract_many(["a.pdf", "b.xml"], columns, base_url=..., model=...)
```

`extract_many` keeps the input order. If one file fails, the others continue and the error is stored in that row. Each cell has `value`, `raw` (the text as printed), `page`, `box` (the position on the page, from 0 to 1), `source` (`xml`, `text` or `vision`), `label`, and `kind`: `value`, `text` (the value is the whole text) or `table` (the value is a list of rows, `columns` names them).

Add columns later without reading the whole invoice again:

```python
from invoice_extract import add_columns, column

rows = [add_columns(row, [column("Customer No.")], base_url=..., model=...) for row in rows]
```

### Templates

A template is a YAML file with a list of fields. `label` is required. `hint` helps the model, `type` (`text`, `date`, `amount` or `iban`) controls normalization and checks, and `kind` is `value` (default), `text` or `table`. A table lists its `table_columns`.

```yaml
name: suppliers
columns:
  - label: Supplier
    required: true
  - label: Gross
    type: amount
  - label: Order No.
    hint: PO number, starts with PO-
  - label: Payment Note
    kind: text
  - label: Line Items
    kind: table
    table_columns: [Description, Quantity, Unit Price, Amount]
```

### Export

- **Excel:** sheet "Invoices" has one row per invoice with the selected values and text blocks, plus Status. Sheet "Details" has one row per value with the raw text, source, page and checks. Every selected table gets its own sheet (for example "Line Items") with one row per table row, linked to its invoice by file name and invoice number.
- **CSV:** the invoices table in the named file. Each selected table goes into its own file next to it, `<name>-<field>.csv`. In the web UI, a CSV export with tables is downloaded as one ZIP.
- **JSON:** the selected fields of every invoice, including tables.

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

Every invoice gets a status. The checks do not use the model; they work on the fields the model marks as supplier, invoice number, dates, net, VAT, gross, currency and IBAN, whatever the field is called.

- **ok**: all checks passed.
- **warning**: something needs a look, for example a value does not match the text printed on the page, the due date is before the invoice date, a field has no box on the page, or two values on one invoice look like the same total.
- **error**: net + VAT does not equal gross, the IBAN checksum is wrong, or the file could not be read.

One file is one invoice. A PDF with several invoices in it becomes one row.

## Limitations

Quality depends on the chosen model. Finding every field asks a lot of a model: on a real two-page invoice, a 7B model (qwen2.5:7b) found about 15 fields, a 30B mixture-of-experts model (qwen3:30b-a3b) found 24 in about the same time. Use a model of 20B or more if you can. A small model may miss fields, misread poor scans or merge fields that only look alike. Check the rows marked warning or error, and spot-check the rest. A saved template makes later runs more consistent.

Boxes on scans come from the vision model and are best effort. Boxes on digital PDFs come from the text layer and are exact.

## Running Tests

```bash
pip install -e ".[test]"
pytest
```

The tests run against a local fake server and need no real model.

## License

MIT
