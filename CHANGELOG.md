# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-21

### Added

- Normal invoices to e-invoices: PDF, scan or photo in, valid XRechnung (CII XML) or ZUGFeRD (PDF with embedded XML) out; EN 16931 and XRechnung rule checks, company profile, review form in the web UI, `invoice-extract einvoice` command
- One table from many invoices: ZUGFeRD/Factur-X, XRechnung (UBL and CII), digital PDFs, scans and photos
- The model finds every value, text block and table on each invoice and names it; fields that mean the same thing on different invoices are merged into one column
- Pick the fields to export; save the selection as a YAML template so later invoices are sorted into the same fields
- Line items and other tables as their own Excel sheet or CSV file, one row per table row
- E-invoices are read straight from their XML, including their line items, without a model
- Source boxes for each value, checks (totals, IBAN, dates, required fields) and a status per invoice
- Export to Excel, CSV and JSON with formula injection protection
- Command-line tool, Python API, web review UI (`invoice-extract serve`) and Dockerfile
