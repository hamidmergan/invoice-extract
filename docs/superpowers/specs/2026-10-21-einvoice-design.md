# invoice-extract: normal invoices to e-invoices (XRechnung / ZUGFeRD)

Date: 2026-10-21
Status: approved (owner: "make a plan for A and build it"; questions skipped by the owner, so the calls below are the author's)

## Intent

German companies must receive e-invoices since 2025 and must send them from 2027/2028. Many small businesses still write invoices in Word or Excel, or receive paper and PDF invoices they need in machine-readable form. Open-source tools only build or check the XML from data typed in by hand; reading a normal invoice with AI and turning it into a valid e-invoice is only offered by paid services.

invoice-extract gets a converter: a normal invoice (digital PDF, scan or photo) goes in, the model reads every field an EN 16931 e-invoice needs, the user checks and completes the fields next to the page, and the tool writes a valid **XRechnung** (CII XML) or **ZUGFeRD** (the PDF with the same CII XML embedded). Everything runs with any OpenAI-compatible model, locally if wanted.

## Goals

- Read the EN 16931 fields from a normal invoice: seller and buyer (name, address, country, VAT ID, tax number, contact, e-mail), invoice number, type, issue date, service period or delivery date, due date, currency, payment means (transfer or direct debit, IBAN, BIC, mandate reference, creditor ID), payment terms, buyer reference, notes, and every line (description, quantity, unit, unit price, VAT rate, line amount).
- A **company profile** for the seller (your own company), saved once as YAML. When a profile is set, its values replace the seller fields read from the invoice. This covers fields that are rarely printed (contact person, e-mail, electronic address).
- Compute the VAT breakdown and totals from the lines, and compare them with the printed totals. A mismatch is an error the user must resolve.
- Validate before writing: the EN 16931 rules and, for XRechnung, the German rules (BR-DE). Missing or inconsistent data is shown field by field; nothing invalid is written.
- Output: `XRechnung` = CII XML with the XRechnung 3.0 guideline ID; `ZUGFeRD` = the original PDF (a scan is turned into a PDF first) with the CII XML embedded as `factur-x.xml`, profile EN 16931.
- Ways in: library, CLI (`invoice-extract einvoice FILES ...`), web UI (the new start page).

## Non-goals

- UBL output (XRechnung allows CII, so one syntax is enough).
- Allowances and charges on document or line level, prepayments, multiple currencies.
- Sending (Peppol, e-mail) and archiving.
- Guaranteeing PDF/A-3 conformance of the ZUGFeRD PDF; the XML is valid, but the PDF is only as PDF/A-conform as the source PDF. The README says so.
- Converting existing e-invoices (an XML or ZUGFeRD input is reported as "already an e-invoice").

## Anti-goals

- Writing an e-invoice that fails the official rules.
- Silently guessing legally relevant data (VAT IDs, IBAN, amounts). Every value is traceable to the page or to the profile, or typed by the user.
- Dropping the existing table export; it stays available (`/table` in the UI, the existing CLI).

## Constraints

As in the v1 and v2 specs: any OpenAI-compatible model, no default model, Python 3.9+, English for everything outward-facing, MIT, no real invoices in the repo, the internal lab server URL never in the repo. New dependency: `drafthorse` (Apache 2.0, builds and XSD-checks CII XML, embeds it into PDFs; pulls in `lxml` and `pypdf`).

## Decided by the author

- **Extraction** reuses template mode: a fixed field list `EINVOICE_FIELDS` (values plus one `lines` table with fixed columns) is filled by `fill_columns`, with boxes as today. A row is then mapped to an `EInvoice` model with typed parts (`Party`, `Line`, `TaxGroup`).
- **Rules** are implemented in Python: the mandatory fields, the totals rules (BR-CO-10/13/14/15/16), the VAT rules for categories S, Z, E, AE, K, G, and the XRechnung rules BR-DE-1, 2, 5, 6, 7, 15, 16, 23, 24. The XML is also checked against the Factur-X EN 16931 XSD. During development, the official KoSIT validator (with the XRechnung 3.0.2 configuration) checks the generated files; it is not a runtime dependency.
- **Defaults**:
  - The type code is 380 (invoice), or 381 when the model reports a credit note.
  - The unit is C62 when unknown; common units map to UN/ECE codes (hour HUR, day DAY, month MON, kg KGM, litre LTR, metre MTR, piece C62).
  - The VAT category is S when the rate is above 0, otherwise E with a required exemption reason.
  - The buyer reference (BT-10) is the Leitweg-ID when printed, otherwise empty, which is an XRechnung error the user must fill (any reference is accepted).
- **Rounding**:
  - Line amount = quantity × unit price, rounded to 2 decimals.
  - Line amounts printed on the invoice win when they differ by at most 0.01.
  - Tax per VAT group = basis × rate, rounded to 2 decimals.
- **Web UI**:
  - `/` becomes the converter: file list, the page with boxes, a form grouped into Seller, Buyer, Invoice, Payment, Lines and Totals, and problems listed with the field they belong to.
  - Format choice: XRechnung or ZUGFeRD. Download stays disabled while there are errors.
  - "My company" opens the profile form (load/save YAML).
  - The v2 field-selection page moves to `/table`.
- **CLI**: `invoice-extract einvoice FILES [--format xrechnung|zugferd] [--profile company.yaml] [-o DIR]`. It writes valid files, prints the problems of the others, and exits with 1 if any file failed.

## Left to the builder

Module layout, prompt wording, the exact form layout, the list of mapped unit names.
