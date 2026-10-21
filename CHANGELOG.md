# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-21

### Added

- One table from many invoices: ZUGFeRD/Factur-X, XRechnung (UBL and CII), digital PDFs, scans and photos
- E-invoices are read straight from their XML, without a model
- Standard columns plus your own columns by name, from a YAML template or by clicking a value in the web UI
- Source boxes for each value, checks (totals, IBAN, dates, required fields) and a status per invoice
- Export to Excel, CSV and JSON with formula injection protection
- Command-line tool, Python API, web UI (`invoice-extract serve`) and Dockerfile
