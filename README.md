# invoice-extract

A lightweight Python tool that turns a pile of invoices into one table. You choose the columns, a language model of your choice fills them, and each value comes with the place on the page where it was found. E-invoices are read straight from their XML, without a model. The result is an Excel, CSV or JSON file.

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

### Command-Line

Put several invoices into one Excel file:

```bash
invoice-extract *.pdf invoices/*.xml --base-url http://localhost:11434/v1 --model your-model
```

This writes `invoices.xlsx` with the standard columns: File, Supplier, Invoice No., Invoice Date, Due Date, Net, VAT, Gross, Currency, IBAN and Status. If `invoices.xlsx` already exists, a number is added instead (`invoices (1).xlsx`). A file named with `-o` is overwritten.

Add your own columns by name, and choose the output with `-o` (`.xlsx`, `.csv` or `.json`):

```bash
invoice-extract *.pdf --column "Order No." --column "Customer No." -o october.csv \
  --base-url http://localhost:11434/v1 --model your-model
```

Use a different model for scans:

```bash
invoice-extract scan.jpg invoice.pdf --base-url http://localhost:11434/v1 \
  --model your-model --vision-model your-vision-model
```

Use the columns from a template:

```bash
invoice-extract *.pdf --template suppliers.yaml --base-url http://localhost:11434/v1 --model your-model
```

E-invoices work without any model settings:

```bash
invoice-extract xrechnung.xml zugferd.pdf
```

Each file gets one line on stderr with its status. If a file cannot be read, the others continue and the error is shown in that file's row. The exit code is 1 if at least one file failed, and 2 for a setup error (missing template, unknown format, too many files).

### Web UI

```bash
invoice-extract serve --base-url http://localhost:11434/v1 --model your-model
```

Then open http://127.0.0.1:8000 and drop your invoices onto the page.

- Each invoice shows its page with a box around every value found.
- Click any spot on a PDF page to see the text on it, then click a value (for example a customer number) and give it a title. A new column is created and filled for all invoices. The label next to the value is passed to the model as a hint.
- Click a cell in the table to correct it. Edited cells are marked, and the checks run again.
- Save your columns as a template and load them next time.
- Export the table as Excel, CSV or JSON.

The UI has no login. It listens on 127.0.0.1 by default. Only use `--host 0.0.0.0` on a trusted network.

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

```python
from invoice_extract import column, export, extract_many, STANDARD_COLUMNS

columns = STANDARD_COLUMNS + [column("Order No.", hint="starts with PO-")]
rows = extract_many(
    ["a.pdf", "b.xml", "scan.jpg"],
    columns,
    base_url="http://localhost:11434/v1",
    model="your-model",
)
for row in rows:
    print(row.path, row.status, row.error or row.cells["gross"].value)

export(rows, "invoices.xlsx", columns)
```

`extract_many` keeps the input order. If one file fails, the others continue and the error is stored in that row. Each cell has `value`, `raw` (the text as printed), `page`, `box` (the position on the page, from 0 to 1) and `source` (`xml`, `text` or `vision`).

Add columns later without reading the whole invoice again:

```python
from invoice_extract import add_columns, column

extra = [column("Customer No.")]
rows = [add_columns(row, extra, base_url=..., model=...) for row in rows]
```

### Templates

A template is a YAML file with a list of columns. `label` is required; `hint`, `type` (`text`, `date`, `amount` or `iban`) and `required` are optional.

```yaml
name: suppliers
columns:
  - label: Supplier
    required: true
  - label: Gross
    type: amount
    required: true
  - label: Order No.
    hint: PO number, starts with PO-
```

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
- **warning**: something needs a look, for example a required column is empty, the due date is before the invoice date, or a value does not match the text printed on the page.
- **error**: net + VAT does not equal gross, the IBAN checksum is wrong, or the file could not be read.

One file is one invoice. A PDF with several invoices in it becomes one row.

Values that start with `=`, `+`, `-` or `@` are written as text in Excel and CSV, so a hostile invoice cannot smuggle a formula into your spreadsheet.

## Limitations

Quality depends on the chosen model. A small model may misread poor scans or mix up similar fields. Check the rows marked warning or error, and spot-check the rest.

Line items are not extracted, only values that appear once per invoice.

Boxes on scans come from the vision model and are best effort. Boxes on digital PDFs come from the text layer and are exact.

## Running Tests

```bash
pip install -e ".[test]"
pytest
```

The tests run against a local fake server and need no real model.

## License

MIT
