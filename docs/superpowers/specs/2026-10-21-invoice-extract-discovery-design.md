# invoice-extract v2 design: the model finds everything, the user only picks

Date: 2026-10-21
Status: approved
Builds on: `2026-10-21-invoice-extract-design.md` (v1, implemented). Everything in v1 that this document does not change stays as it is: the three reading paths (e-invoice, digital PDF, scan), the provider rules, packaging, Docker, limits, checks, formula-safe export, the web server's security rules, and the library/CLI/UI split.

## Intent

v1 asks the user to build the table: fixed standard columns, plus columns added by name or by clicking a value. Trying it on a real invoice, the owner saw no benefit in that and found adding boxes by hand awkward.

v2 turns this around. The user should do almost nothing except upload, choose what goes into the export, and export:

1. The user uploads invoices.
2. The model finds and names **everything** on each invoice by itself: single values (amounts, dates, numbers), whole text blocks (for example a payment note or contract terms), and tables (the line items). Each item gets a label and a box on the page.
3. The model also decides when items from different invoices mean the same thing and merges them under one name ("Nettorechnungs-Betrag" on one invoice and "Summe netto" on another both become "Net").
4. In the UI, everything starts selected. The user unticks what they don't want. The selection applies to all invoices at once.
5. The export contains exactly the selected fields.
6. The selection can be saved as a template. With a template loaded, the model sorts new invoices into exactly that schema: same fields, same names.

Audience: first the owner, for their own invoices; later possibly other open-source users with their own models. So it must work well for one person and stay generic: any model, any language, any company layout.

## Goals

- Zero setup on first use: upload, untick, export.
- Every found item has a label, a kind (value, text block or table), a box on the page where possible, and its source text.
- One selection for all invoices, with a count showing in how many invoices each field was found (for example "6/10").
- Line items done properly from the start: tables are read row by row and exported as their own sheet, one row per line item, linked to the invoice. The owner explicitly asked for this instead of a "block now, rows later" step.
- Templates capture the selection and make later runs consistent.
- Checks (net + VAT = gross, IBAN, due date after invoice date) keep working wherever the matching fields are found.
- Manual correction of a value in the table stays possible and is marked.

## Non-goals

- Drawing or adding boxes by hand, and creating columns by clicking a value (removed from v1).
- Adding columns by hand in the UI. The model finds them; the user only selects.
- Choosing fields per invoice. The selection is one list for all invoices.
- The user renaming or merging fields. Naming and merging are the model's job (owner's decision). Renaming may come later.
- Splitting one file into several invoices (unchanged from v1).
- Accounting integrations, logins, databases (unchanged from v1).

## Anti-goals (a failure even if it works)

- The user has to do manual work before getting a useful export.
- Values without a traceable source, or invented values (unchanged from v1).
- Merging fields that clearly mean different things (for example the invoice date and the due date under one name), so that values end up in the wrong column.
- Being tied to one provider or one invoice layout (unchanged from v1).

## Decided by the owner

- Discovery first: the model marks everything, including text blocks and tables. Everything starts selected; the user deselects.
- The model decides labels and whether fields from different invoices are merged.
- Templates: a saved selection makes the model sort new invoices into that schema.
- Line items as their own export sheet, one row per item, from the start.
- UI layout C: on the left, the list of fields across all invoices with checkboxes and counts; on the right, the current invoice page with all boxes; the user flips through invoices; at the bottom, the export preview.
- First for the owner, but open for other users (generic, any model).

## Decided by the author (open to change)

These were not discussed with the owner; they are the author's calls.

### Two modes

- **Discovery mode** (no template): the model lists everything it finds.
- **Template mode** (template loaded, or `--column` given in the CLI): the model fills only the template's fields, as v1 does today. No discovery, no merge step.
- Switching: loading a template in the UI re-runs the open invoices in template mode. Clearing the template returns to discovery.

### Discovery per invoice

- One LLM request per invoice (same text input as v1: XML text, PDF text layer, or doc-extract Markdown for scans).
- The answer is a list of items. Each item has:
  - `label`: a short English name chosen by the model (for example "Invoice No.", "Billing Period", "Payment Note").
  - `kind`: `value`, `text` or `table`.
  - `type` for values: `text`, `date`, `amount` or `iban` (used for normalization, checks and Excel formatting).
  - `value` (for a value), `text` (for a text block), or `columns` plus `rows` (for a table).
  - `source_text` (for a table: its first row as printed) and `page`.
  - `role`, optional, from a fixed list the checks need: `invoice_no`, `invoice_date`, `due_date`, `net`, `vat`, `gross`, `currency`, `iban`, `supplier`. This lets the checks work no matter what the label is.
- Labels are in English, whatever the invoice language, so that exports from mixed-language stacks line up. The source text stays as printed.

### Merging across invoices

- After the invoices of a batch are read, one extra LLM request receives only labels, kinds, types, roles and one or two example values per label (no full invoice text). It returns a mapping from each label to a common field name. The same mapping also unifies table columns (for example "Betrag EUR" and "Amount" become "Amount").
- Items with the same `role` are always merged into one field, without asking the model.
- Hard rule: two items from the same invoice never end up in the same field. If the mapping would do that (for example two VAT amounts for 7 % and 19 %, or "Date" meaning the invoice date on one invoice and the delivery date on another), the items stay separate fields ("VAT 7 %", "VAT 19 %"). If two items on one invoice claim the same role, only the one the model marks as the total keeps the role; if that is unclear, neither keeps it, and the checks that need it are skipped with a warning. A cell therefore always holds one value, never a list.
- When files are added later, the merge request runs again for the new labels against the existing field names, so existing fields keep their names.
- If the merge request fails, every label stays its own field, and a warning says that merging did not work.

### E-invoices

- XRechnung and ZUGFeRD are still read from the XML without a model. All fields the XML mapping knows become items with fixed English labels and their roles, and the XML line items become a table.
- With a model configured, the XML text also goes through discovery, so fields outside the fixed mapping are found too. Without a model, only the fixed mapping is used, and nothing fails.

### Boxes

- Values: the source text is searched in the PDF text layer; if it is not found, the value as printed is searched (as fixed in v1 today).
- Text blocks: the box covers the found text. A block that cannot be located exactly gets no box and a warning, as values do.
- Tables: one box around all located rows.
- Scans: best-effort boxes from the vision model, as in v1.
- Each field has its own colour. Fields that are deselected stay visible on the page but faded, so the user can still see what the model found.

### Export

- Sheet "Invoices": one row per invoice. Columns: File, then the selected value and text fields in list order, then Status. A text block is one cell with the whole text.
- One sheet per selected table field, named after the field (usually "Line Items"; other tables such as a VAT breakdown get their own sheet). One row per table row. Columns: File, Invoice No. (if found), then that table's unified columns. Table columns are merged per table field, never across different tables.
- Sheet "Details": as in v1, one row per value.
- CSV: the "Invoices" sheet in the named file. Each selected table goes into its own file next to it, `<name>-<table-field>.csv`. In the UI, a CSV export with tables downloads one ZIP.
- JSON: the selected fields only, including the selected tables, like every other format. What is not selected is not exported.

### Templates

- A template stores the selected fields: label, kind, type, role, a hint the author generates from the examples, and the table columns for tables.
- The v1 template format stays readable: a v1 template is a template whose fields are all values.

### UI (layout C)

- Left: the field list for all invoices, grouped into Values, Text blocks and Tables. Each field has a checkbox, its colour, and a count such as "6/10". Clicking a field shows its box on the current invoice and scrolls to it.
- Right: the current invoice with all boxes and labels, previous/next buttons and the file name.
- Bottom: a preview of the export with the selected fields, with one tab per selected table. Cells stay editable as in v1.
- Top: upload, "Save as template", "Load template", "Export ▾", "Clear".
- Removed from v1: column chips, "+ Add column", the click-to-create popover, the per-invoice "Values" card. The checks list stays, for the current invoice.

### CLI and library

- `invoice-extract FILES` without `--template` and `--column` runs discovery and exports every field found.
- `--template` and `--column` run template mode as today.
- Library: `extract_many(paths)` without columns runs discovery and returns rows plus the merged field list; with columns it behaves as today. The exact return type is left to the builder, as long as v1 callers that pass columns keep working.
- `STANDARD_COLUMNS` stays in the library as a ready-made template ("standard"), but it is no longer the default.

## Left to the builder

- Prompt wording, and how to keep discovery answers usable with small local models (for example per-page requests for long invoices).
- How text blocks are bounded, and how long a block may be before it is cut in the preview (never in the export).
- Field colours, and the order of fields in the list (suggested: by count, then by first appearance).
- How the merge step is batched for large stacks.
- Internal data structures, module layout, test fixtures.
