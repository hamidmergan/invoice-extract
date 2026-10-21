"""The pipeline: detect the file kind, fill the columns, locate the sources, run the checks."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Union

import doc_extract
import openai

from .checks import run_checks
from .columns import RESERVED, STANDARD_COLUMNS, Column
from .config import Settings, resolve
from .detect import detect, embedded_xml
from .einvoice import CII_PATHS, UBL_PATHS, parse_xml, xml_text
from .errors import LimitError, ModelError
from .llm import fill_columns, make_client, vision_boxes
from .normalize import display_variants, normalize
from .pdf import locate, page_count, render_page, text_pages
from .results import Cell, Check, Row

PathLike = Union[str, Path]
ClientGetter = Callable[[], openai.OpenAI]

XML_KEYS = set(CII_PATHS) | set(UBL_PATHS)
SOURCE = {"xml": "xml", "zugferd": "xml", "pdf": "text", "scan": "vision"}
VISION_HINT = "; set a vision-capable model with --vision-model or INVOICE_EXTRACT_VISION_MODEL"
_VISION_WORDS = ("image", "vision", "multimodal")


def _settings(base_url, model, vision_model, api_key, limits) -> Settings:
    return resolve(base_url=base_url, model=model, vision_model=vision_model, api_key=api_key, **limits)


def _lazy_client(settings: Settings, client: Optional[openai.OpenAI]) -> ClientGetter:
    """Build the client on first use, so e-invoices need no model settings."""
    made: List[openai.OpenAI] = [client] if client is not None else []

    def get() -> openai.OpenAI:
        if not made:
            made.append(make_client(settings))
        return made[0]

    return get


def _wanted(columns: Sequence[Column]) -> List[Column]:
    return [c for c in columns if c.key not in RESERVED]


def _pdf_text(path: PathLike) -> str:
    return "\n".join(f"<!-- page {i} -->\n{t}" for i, t in enumerate(text_pages(path), start=1))


def _llm_cells(path: PathLike, columns: List[Column], text: str, source: str, client: openai.OpenAI,
               model: str, locate_boxes: bool) -> Dict[str, Cell]:
    cells = {}
    for key, item in fill_columns(client, model, columns, text).items():
        col = next(c for c in columns if c.key == key)
        if item is None or item["value"] in (None, ""):
            cells[key] = Cell(source=source)
            continue
        source_text = item["source_text"]
        value, checks = normalize(col.type, item["value"], source_text)
        cell = Cell(value=value, raw=source_text, page=item["page"], source=source, checks=checks)
        if locate_boxes and source_text:
            cell.box = locate(path, source_text, item["page"])
            if cell.box is None:
                cell.checks.append(Check("source_not_located", "warning", "Source text not found in the PDF"))
            else:
                cell.page = cell.box.page
        cells[key] = cell
    return cells


def _xml_cells(path: PathLike, kind: str, columns: List[Column], settings: Settings,
               client: ClientGetter) -> Dict[str, Cell]:
    data = Path(path).read_bytes() if kind == "xml" else embedded_xml(path)
    parsed = parse_xml(data)
    cells = {}
    for col in columns:
        if col.key not in XML_KEYS:
            continue
        raw = parsed.get(col.key, "")
        if not raw:
            cells[col.key] = Cell(source="xml")
            continue
        value, checks = normalize(col.type, raw, raw)
        cell = Cell(value=value, raw=raw, source="xml", checks=checks)
        if kind == "zugferd":
            for variant in display_variants(col.type, value):
                cell.box = locate(path, variant)
                if cell.box is not None:
                    cell.page = cell.box.page
                    break
        cells[col.key] = cell

    rest = [c for c in columns if c.key not in XML_KEYS]
    if rest:
        text = xml_text(data)
        if kind == "zugferd":
            text += "\n\n" + _pdf_text(path)
        cells.update(_llm_cells(path, rest, text, "xml", client(), settings.model, kind == "zugferd"))
    return cells


def _transcribe(path: PathLike, settings: Settings) -> str:
    try:
        return doc_extract.extract(
            path,
            base_url=settings.base_url,
            model=settings.vision(),
            api_key=settings.api_key,
            timeout=settings.timeout,
            max_retries=settings.max_retries,
            max_pages=settings.max_pages,
            max_file_mb=settings.max_file_mb,
        )
    except doc_extract.ExtractError as e:
        message = str(e)
        if any(word in message.lower() for word in _VISION_WORDS):
            message += VISION_HINT
        raise ModelError(message) from e


def _scan_cells(path: PathLike, columns: List[Column], settings: Settings, client: ClientGetter) -> Dict[str, Cell]:
    settings.require_llm()
    text = _transcribe(path, settings)
    cells = _llm_cells(path, columns, text, "vision", client(), settings.model, locate_boxes=False)

    pages = page_count(path)
    by_page: Dict[int, Dict[str, str]] = {}
    for key, cell in cells.items():
        if cell.value in (None, ""):
            continue
        page = cell.page if cell.page is not None else (1 if pages == 1 else None)
        if page is None or not 1 <= page <= pages:
            continue
        by_page.setdefault(page, {})[key] = cell.raw or str(cell.value)
    for page, items in sorted(by_page.items()):
        boxes = vision_boxes(client(), settings.vision(), render_page(path, page), items, page)
        for key, box in boxes.items():
            if key in cells:
                cells[key].box = box
                cells[key].page = page
    return cells


def _fill(path: PathLike, kind: str, columns: List[Column], settings: Settings, client: ClientGetter) -> Dict[str, Cell]:
    wanted = _wanted(columns)
    if kind in ("xml", "zugferd"):
        return _xml_cells(path, kind, wanted, settings, client)
    if kind == "pdf":
        return _llm_cells(path, wanted, _pdf_text(path), "text", client(), settings.model, locate_boxes=True)
    return _scan_cells(path, wanted, settings, client)


def _extract(path: PathLike, columns: List[Column], settings: Settings, client: ClientGetter) -> Row:
    kind = detect(path, settings)
    row = Row(path=str(path), kind=kind)
    if any(c.key == "file" for c in columns):
        row.cells["file"] = Cell(value=Path(path).name, source=SOURCE[kind])
    row.cells.update(_fill(path, kind, columns, settings, client))
    row.checks = run_checks(row, columns)
    return row


def extract(path: PathLike, columns: Optional[List[Column]] = None, *, base_url: Optional[str] = None,
            model: Optional[str] = None, api_key: Optional[str] = None, vision_model: Optional[str] = None,
            client: Optional[openai.OpenAI] = None, **limits) -> Row:
    """One invoice as a Row. Raises InvoiceError subclasses."""
    settings = _settings(base_url, model, vision_model, api_key, limits)
    return _extract(path, list(columns or STANDARD_COLUMNS), settings, _lazy_client(settings, client))


def extract_many(paths: Sequence[PathLike], columns: Optional[List[Column]] = None, *,
                 base_url: Optional[str] = None, model: Optional[str] = None, api_key: Optional[str] = None,
                 vision_model: Optional[str] = None, client: Optional[openai.OpenAI] = None,
                 **limits) -> List[Row]:
    """One Row per path, in input order. A failing file becomes a Row with `error` set."""
    settings = _settings(base_url, model, vision_model, api_key, limits)
    paths = list(paths)
    if len(paths) > settings.max_files:
        raise LimitError(f"{len(paths)} files given, the limit is {settings.max_files} (raise it with --max-files)")
    columns = list(columns or STANDARD_COLUMNS)
    get_client = _lazy_client(settings, client)

    def one(path: PathLike) -> Row:
        try:
            return _extract(path, columns, settings, get_client)
        except Exception as e:  # one broken file must not stop the batch
            return Row(path=str(path), error=f"{type(e).__name__}: {e}")

    with ThreadPoolExecutor(settings.concurrency) as pool:
        return list(pool.map(one, paths))


def add_columns(row: Row, columns: List[Column], *, base_url: Optional[str] = None, model: Optional[str] = None,
                api_key: Optional[str] = None, vision_model: Optional[str] = None,
                client: Optional[openai.OpenAI] = None, **limits) -> Row:
    """Fill the columns that `row` does not have yet; existing and edited cells stay as they are."""
    settings = _settings(base_url, model, vision_model, api_key, limits)
    missing = [c for c in columns if c.key not in row.cells and c.key not in RESERVED]
    if missing:
        kind = row.kind or detect(row.path, settings)
        row.kind = kind
        row.cells.update(_fill(row.path, kind, missing, settings, _lazy_client(settings, client)))
    kept = [c for c in row.checks if c.name == "required"]
    row.checks = kept + run_checks(row, columns)
    return row
