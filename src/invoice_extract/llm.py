"""Calls to an OpenAI-compatible chat endpoint."""

import base64
import json
import re
from typing import Any, Dict, List, Optional

import openai

from .columns import KINDS, RESERVED, ROLES, Column
from .config import PLACEHOLDER_API_KEY, Settings
from .errors import ModelError
from .results import Box

FILL_PROMPT = """You extract values from an invoice.
You get a JSON list of columns (key, label, hint, type, kind) and the invoice text.
Pages in the text start with a marker like <!-- page N -->.

Answer with one JSON object and nothing else. For every column key give
{"value": ..., "source_text": "...", "page": N}, or null if the value is not printed.
A column of kind "text" gets {"text": "...", "source_text": "<first words>", "end_text": "<last words>", "page": N};
a column of kind "table" gets {"rows": [[...], ...], "source_text": "<first row>", "end_text": "<last row>", "page": N}
with the cells in the order of the column's "columns".
Rules:
- Use only values printed in the text. Never guess or calculate.
- Dates as ISO "YYYY-MM-DD". Amounts as a decimal number with a dot, e.g. 1234.56.
- source_text is the exact text as printed, copied character by character.
- page is the number from the nearest <!-- page N --> marker before the value."""

DISCOVER_PROMPT = """You read an invoice and list everything printed on it.
Pages in the text start with a marker like <!-- page N -->.

Answer with one JSON object and nothing else: {"items": [...]}. Each item is one of
- a single value: {"label": "...", "kind": "value", "type": "text|date|amount|iban", "role": "...",
  "value": ..., "source_text": "...", "page": N}
- a block of text: {"label": "...", "kind": "text", "text": "...", "source_text": "<first words>",
  "end_text": "<last words>", "page": N}
- a table: {"label": "...", "kind": "table", "columns": ["...", ...], "rows": [["...", ...], ...],
  "source_text": "<first row as printed>", "end_text": "<last row as printed>", "page": N}
Rules:
- List every value, every paragraph of text and every table. Do not leave anything out.
- label is a short English name for what the item is, whatever language the invoice is in,
  e.g. "Invoice No.", "Billing Period", "Payment Note", "Line Items".
- role says what a value means for bookkeeping. Set it whenever a value is the supplier name ("supplier"),
  the invoice number ("invoice_no"), the invoice date ("invoice_date"), the due date ("due_date"),
  the total net amount ("net"), the total VAT amount ("vat"), the total gross amount ("gross"),
  the currency ("currency") or the supplier's IBAN ("iban"). Each role at most once; otherwise "".
- Use only what is printed. Never guess or calculate.
- Dates as ISO "YYYY-MM-DD". Amounts as a decimal number with a dot, e.g. 1234.56.
- source_text and end_text are exact text as printed, copied character by character.
- page is the number from the nearest <!-- page N --> marker before the item."""

DISCOVER_EXAMPLE = {"items": [
    {"label": "Invoice No.", "kind": "value", "type": "text", "role": "invoice_no", "value": "A-1",
     "source_text": "Invoice A-1", "page": 1},
    {"label": "Payment Note", "kind": "text", "text": "Please pay within 14 days.", "source_text": "Please pay",
     "end_text": "14 days.", "page": 1},
    {"label": "Line Items", "kind": "table", "columns": ["Description", "Amount"], "rows": [["Item", "10.00"]],
     "source_text": "Item 10.00", "end_text": "Item 10.00", "page": 1},
]}

MERGE_PROMPT = """You get labels of fields found on several invoices, each with its kind, type and example values,
and the field names already in use. Map every label to a field name.
Give two labels the same name only when they mean exactly the same thing on an invoice.
Prefer a name already in use when it fits. Names are short English names.
Answer with one JSON object and nothing else: {"mapping": {"<label>": "<field name>"}}."""

COLUMNS_PROMPT = """You get the column names of the same kind of table from several invoices.
Map every column name to a common English name. Give two names the same name only when they mean the same thing.
Answer with one JSON object and nothing else: {"mapping": {"<column name>": "<common name>"}}."""

VALUE_TYPES = ("text", "date", "amount", "iban")

# Labels that mean a role, for values the model named but did not mark.
ROLE_LABELS = {
    "supplier": ("supplier", "supplier name", "seller", "vendor"),
    "invoice_no": ("invoice no.", "invoice no", "invoice number", "invoice #"),
    "invoice_date": ("invoice date", "date of invoice"),
    "due_date": ("due date", "payment due date", "payment due"),
    "net": ("net", "net amount", "net total", "total net"),
    "vat": ("vat", "vat amount", "total vat", "tax", "tax amount"),
    "gross": ("gross", "gross amount", "total", "total amount", "grand total", "amount due", "total due"),
    "currency": ("currency",),
    "iban": ("iban",),
}

BOX_PROMPT = """You locate printed text on an invoice page image.
You get a JSON object that maps keys to the text to find.
Answer with one JSON object and nothing else: {"key": [x0, y0, x1, y1]} for each text you find.
Coordinates are integers from 0 to 1000, relative to the image width and height,
with 0,0 at the top left. Leave out texts you cannot find."""


def make_client(settings: Settings) -> openai.OpenAI:
    settings.require_llm()
    return openai.OpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key or PLACEHOLDER_API_KEY,
        timeout=settings.timeout,
        max_retries=settings.max_retries,
    )


def _chat(client: openai.OpenAI, model: str, system: str, user: Any) -> str:
    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
    except openai.OpenAIError as e:
        raise ModelError(f"Model request failed: {e}") from e
    return response.choices[0].message.content or ""


_THINK = re.compile(r"<think>.*?</think>", re.S)


def _json_object(text: str) -> dict:
    """The first top-level JSON object in a model answer; reasoning blocks, fences and chatter around it are skipped."""
    text = _THINK.sub("", text)
    decoder = json.JSONDecoder()
    error: Optional[json.JSONDecodeError] = None
    for match in re.finditer(r"\{", text):
        before = text[:match.start()].rstrip()
        if before.endswith((":", ",", "[")):
            continue  # a nested value, not the start of an answer
        try:
            data, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError as e:
            error = error or e
            continue
        if isinstance(data, dict):
            return data
    if error is None:
        raise ModelError("Model answer contains no JSON object.")
    raise ModelError(f"Model answer is not valid JSON: {error}")


def _text(value: Any) -> str:
    return "" if value is None else value if isinstance(value, str) else str(value)


def _rows(rows: Any, columns: List[str]) -> List[Dict[str, str]]:
    """Rows as dicts over `columns`: short rows are padded, long rows cut, cells turned into strings."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            cells = [row.get(c) for c in columns]
        elif isinstance(row, list):
            cells = (row + [None] * len(columns))[:len(columns)]
        else:
            continue
        out.append({c: _text(v) for c, v in zip(columns, cells)})
    return out


def _item(data: Any, kind: Optional[str] = None, columns: Optional[List[str]] = None,
          label: str = "", type: str = "", role: str = "") -> Optional[dict]:
    """One item of a model answer in a fixed shape, or None if it is unusable."""
    if not isinstance(data, dict):
        return None
    kind = kind or data.get("kind")
    if kind not in KINDS:
        return None
    if not columns:
        raw = data.get("columns")
        columns = [_text(c) for c in raw if c is not None] if isinstance(raw, list) else []
    page = data.get("page")
    if isinstance(page, bool) or not isinstance(page, int):
        page = None
    type_ = type or (data.get("type") if data.get("type") in VALUE_TYPES else "text")
    role = role or (data.get("role") if data.get("role") in ROLES else "")
    return {
        "label": label or _text(data.get("label")).strip(),
        "kind": kind,
        "type": type_,
        "role": role,
        "value": data.get("value") if kind == "value" else None,
        "text": _text(data.get("text")) if kind == "text" else "",
        "columns": columns if kind == "table" else [],
        "rows": _rows(data.get("rows"), columns) if kind == "table" else [],
        "source_text": _text(data.get("source_text")),
        "end_text": _text(data.get("end_text")),
        "page": page,
    }


def _skeleton(col: Column) -> dict:
    if col.kind == "text":
        return {"text": None, "source_text": "", "end_text": "", "page": None}
    if col.kind == "table":
        return {"rows": [], "source_text": "", "end_text": "", "page": None}
    return {"value": None, "source_text": "", "page": None}


def fill_columns(client: openai.OpenAI, model: str, columns: List[Column], text: str) -> Dict[str, Optional[dict]]:
    """Ask the model for every non-reserved column. Missing keys become None."""
    wanted = [c for c in columns if c.key not in RESERVED]
    spec = [{"key": c.key, "label": c.label, "hint": c.hint, "type": c.type, "kind": c.kind}
            for c in wanted]
    for entry, col in zip(spec, wanted):
        if col.kind == "table":
            entry["columns"] = col.table_columns
    skeleton = {c.key: _skeleton(c) for c in wanted}
    user = ("Columns:\n" + json.dumps(spec, ensure_ascii=False)
            + "\n\nFill in this object and answer with it. Keep every key; a value that is not printed stays null.\n"
            + "Answer skeleton:\n" + json.dumps(skeleton, ensure_ascii=False)
            + "\n\nInvoice text:\n" + text)
    data = _json_object(_chat(client, model, FILL_PROMPT, user))
    return {c.key: _item(data.get(c.key), c.kind, c.table_columns, c.label, c.type if c.kind == "value" else "",
                         c.role) for c in wanted}


def discover_items(client: openai.OpenAI, model: str, text: str) -> List[dict]:
    """Ask the model for every value, text block and table on the invoice."""
    user = ("Answer in this shape (an example, not this invoice):\n" + json.dumps(DISCOVER_EXAMPLE, ensure_ascii=False)
            + "\n\nInvoice text:\n" + text)
    data = _json_object(_chat(client, model, DISCOVER_PROMPT, user))
    items = data.get("items")
    if not isinstance(items, list):
        raise ModelError("Model answer has no list of items.")
    found = [item for item in (_item(raw) for raw in items) if item is not None and item["label"]]
    _infer_roles(found)
    return found


def _infer_roles(items: List[dict]) -> None:
    """Give unmarked values the role their label names, unless another item already has that role."""
    taken = {item["role"] for item in items if item["role"]}
    for item in items:
        if item["role"] or item["kind"] != "value":
            continue
        label = item["label"].strip().lower()
        for role, labels in ROLE_LABELS.items():
            if role not in taken and label in labels:
                item["role"] = role
                taken.add(role)
                break


def _mapping(client: openai.OpenAI, model: str, prompt: str, payload: Any, names: List[str]) -> Dict[str, str]:
    data = _json_object(_chat(client, model, prompt, json.dumps(payload, ensure_ascii=False)))
    mapping = data.get("mapping")
    mapping = mapping if isinstance(mapping, dict) else {}
    out = {}
    for name in names:
        target = mapping.get(name)
        out[name] = target.strip() if isinstance(target, str) and target.strip() else name
    return out


def merge_labels(client: openai.OpenAI, model: str, labels: List[dict], known: List[str]) -> Dict[str, str]:
    """Map labels from several invoices to common field names. Unanswered labels map to themselves."""
    return _mapping(client, model, MERGE_PROMPT, {"labels": labels, "known": list(known)},
                    [entry["label"] for entry in labels])


def merge_table_columns(client: openai.OpenAI, model: str, names: List[str]) -> Dict[str, str]:
    """Map table column names from several invoices to common names."""
    return _mapping(client, model, COLUMNS_PROMPT, {"columns": list(names)}, list(names))


def _valid_box(coords: Any) -> bool:
    if not isinstance(coords, list) or len(coords) != 4:
        return False
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in coords):
        return False
    x0, y0, x1, y1 = coords
    return 0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000


def vision_boxes(client: openai.OpenAI, model: str, png: bytes, items: Dict[str, str], page: int) -> Dict[str, Box]:
    """Ask a vision model where each text is on the page. Any failure gives {}."""
    url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    user = [
        {"type": "text", "text": json.dumps(items, ensure_ascii=False)},
        {"type": "image_url", "image_url": {"url": url}},
    ]
    try:
        data = _json_object(_chat(client, model, BOX_PROMPT, user))
    except ModelError:
        return {}
    boxes = {}
    for key in items:
        coords = data.get(key)
        if _valid_box(coords):
            x0, y0, x1, y1 = (v / 1000 for v in coords)
            boxes[key] = Box(page, x0, y0, x1, y1)
    return boxes
