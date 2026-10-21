# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-22

### Added

- The invoices you receive become one checked table: XRechnung (UBL and CII), ZUGFeRD/Factur-X, digital PDFs, scans and photos
- Bookkeeping columns: Type, Supplier, Supplier VAT ID, Invoice No., Invoice Date, Due Date, Description, Net, VAT Rate, VAT, Gross, Currency, IBAN, Status
- E-invoices are read straight from their XML, without a model, and shown as a printable invoice sheet on the web page
- Checks: totals, VAT amount against the VAT rate, IBAN checksum, dates, required values, supplier VAT ID from 250.00 upwards, duplicates across files
- A box on the page for every value; cells can be corrected on the web page
- Export to Excel, CSV and JSON with formula injection protection; own columns via templates (`--template`, `--column`)
- Command-line tool, Python API, web page (`invoice-extract serve`) and Dockerfile
- Writing e-invoices from the command line: `invoice-extract einvoice` turns a PDF, scan or photo into a valid XRechnung (CII XML) or ZUGFeRD (PDF with embedded XML), checked against EN 16931 and the XRechnung rules
