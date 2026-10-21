"""Calls to an OpenAI-compatible chat endpoint."""

import base64
import json
import re
from typing import Any, Dict, List, Optional

import openai

from .columns import RESERVED, Column
from .config import PLACEHOLDER_API_KEY, Settings
from .errors import ModelError
from .results import Box

FILL_PROMPT = """You extract values from an invoice.
You get a JSON list of columns (key, label, hint, type) and the invoice text.
Pages in the text start with a marker like <!-- page N -->.

Answer with one JSON object and nothing else. For every column key give
{"value": ..., "source_text": "...", "page": N}, or null if the value is not printed.
Rules:
- Use only values printed in the text. Never guess or calculate.
- Dates as ISO "YYYY-MM-DD". Amounts as a decimal number with a dot, e.g. 1234.56.
- source_text is the exact text as printed, copied character by character.
- page is the number from the nearest <!-- page N --> marker before the value."""

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


def _field(item: Any) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    page = item.get("page")
    if isinstance(page, bool) or not isinstance(page, int):
        page = None
    source = item.get("source_text")
    return {
        "value": item.get("value"),
        "source_text": source if isinstance(source, str) else "",
        "page": page,
    }


def fill_columns(client: openai.OpenAI, model: str, columns: List[Column], text: str) -> Dict[str, Optional[dict]]:
    """Ask the model for every non-reserved column. Missing keys become None."""
    wanted = [c for c in columns if c.key not in RESERVED]
    spec = [{"key": c.key, "label": c.label, "hint": c.hint, "type": c.type} for c in wanted]
    skeleton = {c.key: {"value": None, "source_text": "", "page": None} for c in wanted}
    user = ("Columns:\n" + json.dumps(spec, ensure_ascii=False)
            + "\n\nFill in this object and answer with it. Keep every key; a value that is not printed stays null.\n"
            + "Answer skeleton:\n" + json.dumps(skeleton, ensure_ascii=False)
            + "\n\nInvoice text:\n" + text)
    data = _json_object(_chat(client, model, FILL_PROMPT, user))
    return {c.key: _field(data.get(c.key)) for c in wanted}


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
