# invoice-extract: incoming invoices to one checked table

Date: 2026-10-22. Status: approved by the owner ("make a sensible tool out of it; as simple as possible and useful").

## Intent

invoice-extract becomes one product: **the invoices you receive go in, one checked table for your bookkeeping comes out.**

Every business in Germany must be able to receive e-invoices since 2025, and most small ones cannot even open an XRechnung. At the same time, every month the pile of PDFs, scans and photos has to be typed into a list for the tax advisor. invoice-extract does both: e-invoices are read exactly from their XML and shown like a paper invoice; PDFs and scans are read by a model of the user's choice; everything lands in one table with fixed, bookkeeping-ready columns, with a status per invoice and a box on the page for every value.

Who it is for: freelancers, small companies, clubs and anyone who receives invoices and hands them to bookkeeping. It runs locally with any OpenAI-compatible model, so invoices never leave the machine.

## Goals

- One page: drop files in, see the table, fix a value, export. Nothing to configure in the UI.
- Fixed columns that a bookkeeper expects, the same for every invoice.
- E-invoices (XRechnung UBL/CII, ZUGFeRD/Factur-X) are read without a model and shown readable, printable.
- Checks that catch what goes wrong in practice: totals, VAT rate, IBAN, dates, missing mandatory fields, duplicates.
- Excel, CSV and JSON export; CLI and Python API for the same thing.

## Non-goals

- The model does not decide which fields exist (v2 "discovery" is removed).
- No account assignment, no DATEV export. The Excel/CSV table is what the tax advisor gets.
- No validation of incoming e-invoices against EN 16931 rules; a full validator is a separate (Java) tool, and partial rules would wrongly reject valid invoices.
- No UI for writing e-invoices. The converter stays as a library and CLI feature (`invoice-extract einvoice`), it is not part of the page.

## Anti-goals

- Wrong confidence: never silently fix a number; every correction is a visible check.
- A table whose columns change from run to run.
- A UI that needs explanation.

## The columns

`STANDARD_COLUMNS`, in this order. Keys are stable API.

| key | label | type | required | from XML | note |
|---|---|---|---|---|---|
| file | File | file | | | file name |
| doc_type | Type | doctype | | TypeCode / root | `invoice` or `credit note` |
| supplier | Supplier | text | yes | seller name | |
| supplier_vat_id | Supplier VAT ID | text | | seller VA registration | as printed |
| invoice_no | Invoice No. | text | yes | document ID | |
| invoice_date | Invoice Date | date | yes | issue date | |
| due_date | Due Date | date | | due date | |
| description | Description | text | | line names joined | the subject as printed: heading or first line item |
| net | Net | amount | | tax basis total | |
| vat_rate | VAT Rate | percent | | tax breakdown rates | text such as `19`, `19, 7`, `0` |
| vat | VAT | amount | | tax total | |
| gross | Gross | amount | yes | grand total | |
| currency | Currency | text | | currency code | |
| iban | IBAN | iban | | payee IBAN | |
| status | Status | status | | | ok / warning / error |

New column types:

- `doctype`: normalized to `credit note` when the value contains "credit", "gutschrift", "storno" or "cancel" (case-insensitive), else `invoice`; empty stays empty.
- `percent`: every number in the value is kept, as a plain percentage without trailing zeros (`19`, `7`, `5.5`); a number with a decimal point that is at most 1 and not 0 is a fraction and becomes a percentage (`0.19` → `19`); duplicates dropped; joined with `, `. Empty stays empty.

`ROLES` grows by `doc_type`, `supplier_vat_id`, `description`, `vat_rate`, so templates can map their own labels to these meanings.

All standard columns are read from an e-invoice's XML; e-invoices never need a model.

## Checks

Per row (`run_checks`), in addition to the existing totals, IBAN, due-before-invoice and required checks:

- `vat_rate`: when `vat_rate` holds exactly one rate and net and vat are present: warning if `net × rate / 100` differs from `vat` by more than 0.02. Mixed rates are not checked.
- `vat_id_missing`: warning when gross is 250.00 or more and `supplier_vat_id` is empty: "No supplier VAT ID; needed on invoices from 250.00 upwards for input VAT deduction". Not raised for credit notes.

Across rows (`duplicate_checks(rows)`): rows with the same supplier (casefolded, whitespace collapsed) and the same invoice number (casefolded, stripped) get a warning `duplicate`: "Same invoice number as <other file name>". Rows with no invoice number are skipped. The function removes earlier `duplicate` checks before adding new ones, so it can run after every change. `extract_many` runs it; the web UI runs it after each finished file and after each edit.

## E-invoice document view

`einvoice.parse_document(data) -> dict` reads a CII or UBL invoice into a readable structure: kind, number, dates (issue, due, delivery, period), currency, buyer reference, order reference, seller and buyer (name, street, postcode, city, country, VAT ID, tax number, e-mail, phone, contact), lines (description, quantity, unit, unit price, VAT rate, amount), tax groups (category, rate, basis, tax), totals (lines, allowances, charges, net, vat, gross, prepaid, due), payment (means as text, IBAN, BIC, account name, terms, mandate reference) and notes. Missing values are empty strings; nothing is computed.

The web UI shows this as an invoice sheet for XML files (which have no page image) and, for ZUGFeRD PDFs, behind a "Show XML data" toggle next to the page image. A "Print" button prints the sheet alone, so the browser's print dialog can save it as PDF.

## Web UI

One page, `index.html` + `app.js`, plain DOM, all data through `textContent`.

- Top bar: title, "Add invoices" (PDF, XML, images; drop anywhere), Export menu (Excel, CSV, JSON), Clear.
- Table: one row per file, all columns, status text per row ("reading…", "OK", "Check: <first problem>"). Click a cell to edit (Enter saves, Escape cancels); edited cells are marked. Click a row to select it.
- Below the table: the page viewer (image with a labelled box per value, page controls) or the e-invoice sheet; next to it the values of the selected invoice (click one to jump to its box) and the list of checks.
- Summary line: files, to check, reading.

API (`web/app.py`): `GET /`, `POST/DELETE /api/files`, `GET /api/state` (columns, files, summary), `GET /api/files/{id}/pages`, `GET /api/files/{id}/pages/{n}.png`, `GET /api/files/{id}/document`, `PATCH /api/files/{id}/cells/{key}`, `GET /api/export?format=`. The host/origin guard stays. `create_app(settings, workdir, allowed_hosts, columns=None)`; `invoice-extract serve --template FILE` passes a template's columns, otherwise the standard columns.

## CLI and Python API

- `invoice-extract FILES -o invoices.xlsx [--template FILE] [--column LABEL]`: standard columns by default; duplicates are checked across the files.
- `invoice-extract serve [--template FILE]`.
- `invoice-extract einvoice ...` stays as it is.
- `extract(path, columns=None)` and `extract_many(paths, columns=None)` use the standard columns when `columns` is None. `add_columns` stays. `duplicate_checks` is exported.

## Removed

- Discovery: `discover.py`, the discovery prompts and helpers in `llm.py`, `fields_of`, `field_counts`, the discovery branch in `core`, the v2 page (`table.html`, the old `app.js`), its routes (`/table`, `/api/selection`, `/api/template`) and tests.
- The e-invoice converter page (`einvoice.html`, `einvoice.js`, `web/einvoice_api.py`) and its tests. The `einvoicing` package, `invoice-extract einvoice` and their tests stay.

## Constraints

- Everything outward (code, UI, README, commits) in English.
- No model needed for e-invoices; a text model for digital PDFs; a vision model for scans.
- Existing exports, templates, checks and boxes keep working; tests stay green.
- README follows the doc-extract README template; it describes the one product first and the e-invoice CLI briefly at the end.

## Left to the builder

- Exact CSS; the sheet layout for the e-invoice view.
- The exact prompt hints for the new columns.
