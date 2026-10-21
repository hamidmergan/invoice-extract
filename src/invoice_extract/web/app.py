"""Web API for the review UI: upload invoices, extract in the background, review, edit and export."""

import copy
import re
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..checks import run_checks
from ..columns import RESERVED, STANDARD_COLUMNS, Column, Template, column
from ..config import Settings
from ..core import _extract, _fill, _lazy_client
from ..errors import InvoiceError
from ..export import FORMATS, export
from ..normalize import normalize
from ..pdf import click_hint, page_count, render_page, segments
from ..results import Cell, Check, Row

STATIC = Path(__file__).parent / "static"
MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "json": "application/json",
}
_SUFFIX = re.compile(r"\.[a-z0-9]{1,10}")


@dataclass
class FileEntry:
    id: str
    name: str
    path: Path
    kind: str = ""
    state: str = "pending"  # pending, running, queued (waits to fill new columns) or done
    row: Optional[Row] = None


@dataclass
class Workspace:
    columns: List[Column] = field(default_factory=lambda: list(STANDARD_COLUMNS))
    files: Dict[str, FileEntry] = field(default_factory=dict)
    pngs: Dict[tuple, bytes] = field(default_factory=dict)
    version: int = 0  # counts column changes, so a job knows it started with an outdated column list


class Source(BaseModel):
    file_id: str
    page: int
    index: int


class NewColumn(BaseModel):
    label: str
    hint: str = ""
    type: str = "text"
    source: Optional[Source] = Field(None, alias="from")


class CellEdit(BaseModel):
    value: Any = None


def _row_dict(entry: FileEntry) -> Optional[Dict[str, Any]]:
    if entry.row is None:
        return None
    data = entry.row.to_dict()
    data["path"] = entry.name  # never show the server-side path
    return data


def _entry_dict(entry: FileEntry) -> Dict[str, Any]:
    status = entry.row.status if entry.row is not None else None
    return {"id": entry.id, "name": entry.name, "kind": entry.kind, "state": entry.state, "status": status,
            "row": _row_dict(entry)}


def _hostname(host: str) -> str:
    if host.startswith("["):
        return host[1:host.find("]")].lower()
    return host.rsplit(":", 1)[0].lower() if host.count(":") == 1 else host.lower()


def _suffix(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return suffix if _SUFFIX.fullmatch(suffix) else ""


def create_app(settings: Settings, workdir: Optional[Path] = None,
               allowed_hosts: Optional[Sequence[str]] = None) -> FastAPI:
    """The review UI and its JSON API, with one in-memory workspace.

    `allowed_hosts` limits the Host header (against DNS rebinding); requests from another origin are always refused.
    """
    tmp = None if workdir is not None else tempfile.TemporaryDirectory(prefix="invoice-extract-")
    uploads = Path(workdir) if workdir is not None else Path(tmp.name)
    uploads.mkdir(parents=True, exist_ok=True)
    ws = Workspace()
    lock = threading.Lock()
    pool = ThreadPoolExecutor(settings.concurrency)
    get_client = _lazy_client(settings, None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        pool.shutdown(wait=False, cancel_futures=True)
        if tmp is not None:
            tmp.cleanup()

    app = FastAPI(title="invoice-extract", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    hosts = {h.lower() for h in allowed_hosts} if allowed_hosts is not None else None

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if hosts is not None and _hostname(host) not in hosts:
            return PlainTextResponse("Unknown host", status_code=400)
        origin = request.headers.get("origin")
        own = {host.lower(), request.headers.get("x-forwarded-host", "").lower()}  # also behind a reverse proxy
        if origin is not None and urlsplit(origin).netloc.lower() not in own:
            return PlainTextResponse("Cross-origin requests are not allowed", status_code=403)
        return await call_next(request)

    def entry_or_404(file_id: str) -> FileEntry:
        with lock:
            entry = ws.files.get(file_id)
        if entry is None:
            raise HTTPException(404, f"Unknown file id {file_id!r}")
        return entry

    def extract_job(entry: FileEntry) -> None:
        with lock:
            entry.state = "running"
            columns = list(ws.columns)
            version = ws.version
        try:
            row = _extract(entry.path, columns, settings, get_client)
        except Exception as e:  # one broken file must not stop the others
            row = Row(path=str(entry.path), error=f"{type(e).__name__}: {e}")
        if "file" in row.cells:
            row.cells["file"].value = entry.name
        with lock:
            entry.kind = row.kind
            entry.row = row
            finish(entry, version)

    def finish(entry: FileEntry, version: int) -> None:
        """Mark a job done, or queue a fill when the columns changed while it ran. Call under the lock."""
        if ws.version != version and entry.row is not None and not entry.row.error:
            entry.state = "queued"
            pool.submit(fill_job, entry)
        else:
            entry.state = "done"

    def fill_job(entry: FileEntry) -> None:
        with lock:
            row = entry.row
            if row is None or row.error:
                entry.state = "done"
                return
            entry.state = "running"
            version = ws.version
            missing = [c for c in ws.columns if c.key not in row.cells and c.key not in RESERVED]
        cells: Dict[str, Cell] = {}
        failure: Optional[Check] = None
        if missing:
            try:
                cells = _fill(row.path, row.kind, missing, settings, get_client)
            except Exception as e:
                failure = Check("fill", "error", f"Could not fill new columns: {type(e).__name__}: {e}")
        with lock:
            for key, cell in cells.items():
                row.cells.setdefault(key, cell)  # a manual edit made meanwhile wins
            row.checks = run_checks(row, ws.columns) + ([failure] if failure else [])
            finish(entry, version)

    def refill_all() -> None:
        """Columns changed: queue a fill for finished rows; running jobs see the new version when they end."""
        with lock:
            ws.version += 1
            for entry in ws.files.values():
                if entry.state == "done" and entry.row is not None and not entry.row.error:
                    entry.state = "queued"
                    pool.submit(fill_job, entry)

    def column_dict(col: Column) -> Dict[str, Any]:
        return asdict(col)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.post("/api/files")
    async def upload(files: List[UploadFile] = File(...)) -> Dict[str, Any]:
        with lock:
            total = len(ws.files) + len(files)
        if total > settings.max_files:
            raise HTTPException(413, f"{total} files in total, the limit is {settings.max_files} "
                                     "(raise it with --max-files)")
        entries = []
        for upload_file in files:
            name = Path(upload_file.filename or "file").name or "file"
            file_id = uuid.uuid4().hex
            path = uploads / f"{file_id}{_suffix(name)}"
            path.write_bytes(await upload_file.read())
            entries.append(FileEntry(id=file_id, name=name, path=path))
        with lock:
            for entry in entries:
                ws.files[entry.id] = entry
        for entry in entries:
            pool.submit(extract_job, entry)
        with lock:
            return {"files": [_entry_dict(e) for e in entries]}

    @app.delete("/api/files")
    def clear() -> Dict[str, Any]:
        with lock:
            entries = list(ws.files.values())
            ws.files.clear()
            ws.pngs.clear()
        for entry in entries:
            entry.path.unlink(missing_ok=True)
        return {"files": []}

    @app.get("/api/state")
    def state() -> Dict[str, Any]:
        with lock:
            files = [_entry_dict(e) for e in ws.files.values()]
            columns = [column_dict(c) for c in ws.columns]
        to_check = sum(1 for f in files if f["state"] == "done" and f["status"] in ("warning", "error"))
        return {"columns": columns, "files": files,
                "summary": {"files": len(files), "columns": len(columns), "to_check": to_check}}

    @app.get("/api/files/{file_id}/pages")
    def pages(file_id: str) -> Dict[str, int]:
        entry = entry_or_404(file_id)
        if entry.kind == "xml":
            return {"count": 0}
        try:
            return {"count": page_count(entry.path)}
        except Exception:
            return {"count": 0}

    @app.get("/api/files/{file_id}/pages/{n}.png")
    def page_png(file_id: str, n: int) -> Response:
        entry = entry_or_404(file_id)
        with lock:
            png = ws.pngs.get((file_id, n))
        if png is None:
            try:
                png = render_page(entry.path, n)
            except (ValueError, InvoiceError, OSError) as e:
                raise HTTPException(404, str(e))
            with lock:
                ws.pngs[(file_id, n)] = png
        return Response(png, media_type="image/png")

    @app.get("/api/files/{file_id}/pages/{n}/segments")
    def page_segments(file_id: str, n: int) -> List[Dict[str, Any]]:
        entry = entry_or_404(file_id)
        try:
            segs = segments(entry.path, n)
        except (ValueError, InvoiceError, OSError) as e:
            raise HTTPException(404, str(e))
        return [{"index": i, "text": s.text, "box": asdict(s.box)} for i, s in enumerate(segs)]

    @app.post("/api/columns")
    def add_column(body: NewColumn) -> Dict[str, Any]:
        hint = body.hint
        if body.source is not None:
            entry = entry_or_404(body.source.file_id)
            try:
                segs = segments(entry.path, body.source.page)
                clicked = click_hint(segs, body.source.index)
            except (ValueError, IndexError, InvoiceError, OSError) as e:
                raise HTTPException(404, f"Unknown text segment: {e}")
            hint = f"{hint} {clicked}".strip()
        with lock:
            try:
                col = column(body.label, hint=hint, type=body.type, existing=[c.key for c in ws.columns])
            except InvoiceError as e:
                raise HTTPException(400, str(e))
            # new columns go before a trailing Status column, as in the table people expect
            at = len(ws.columns) - 1 if ws.columns and ws.columns[-1].key == "status" else len(ws.columns)
            ws.columns.insert(at, col)
        refill_all()
        return column_dict(col)

    @app.delete("/api/columns/{key}")
    def delete_column(key: str) -> Dict[str, Any]:
        with lock:
            if not any(c.key == key for c in ws.columns):
                raise HTTPException(404, f"Unknown column {key!r}")
            ws.columns = [c for c in ws.columns if c.key != key]
            for entry in ws.files.values():
                if entry.state == "done" and entry.row is not None and not entry.row.error:
                    entry.row.checks = run_checks(entry.row, ws.columns)
        return {"deleted": key}

    @app.patch("/api/files/{file_id}/cells/{key}")
    def edit_cell(file_id: str, key: str, body: CellEdit) -> Dict[str, Any]:
        entry = entry_or_404(file_id)
        if key in RESERVED:
            raise HTTPException(400, f"Column {key!r} cannot be edited")
        with lock:
            row = entry.row
            if row is None or row.error:
                raise HTTPException(409, "This file has no extracted values to edit")
            col = next((c for c in ws.columns if c.key == key), None)
            if col is None and key not in row.cells:
                raise HTTPException(404, f"Unknown column {key!r}")
            old = row.cells.get(key, Cell())
            value, checks = normalize(col.type if col else "text", body.value, "")
            row.cells[key] = Cell(value=value, raw=old.raw, page=old.page, box=old.box, source="manual",
                                  edited=True, checks=checks)
            row.checks = run_checks(row, ws.columns)
            return _entry_dict(entry)

    @app.get("/api/export")
    def download(format: str = "xlsx") -> Response:
        if format not in FORMATS:
            raise HTTPException(400, f"Unknown export format {format!r}; use one of: {', '.join(FORMATS)}")
        with lock:
            rows = []
            for entry in ws.files.values():
                if entry.state == "done" and entry.row is not None:
                    row = copy.copy(entry.row)
                    row.path = entry.name  # export names files by path
                    rows.append(row)
            columns = list(ws.columns)
        with tempfile.TemporaryDirectory(prefix="invoice-extract-export-") as out:
            path = export(rows, Path(out) / f"invoices.{format}", columns, format)
            data = path.read_bytes()
        return Response(data, media_type=MEDIA_TYPES[format],
                        headers={"Content-Disposition": f'attachment; filename="invoices.{format}"'})

    @app.get("/api/template")
    def get_template() -> Response:
        with lock:
            text = Template("invoices", list(ws.columns)).dumps()
        return Response(text, media_type="application/x-yaml",
                        headers={"Content-Disposition": 'attachment; filename="invoices.yaml"'})

    @app.post("/api/template")
    async def put_template(file: UploadFile = File(...)) -> Dict[str, Any]:
        try:
            template = Template.loads((await file.read()).decode("utf-8"))
        except (InvoiceError, UnicodeDecodeError) as e:
            raise HTTPException(400, f"Invalid template: {e}")
        with lock:
            ws.columns = list(template.columns)
            columns = [column_dict(c) for c in ws.columns]
        refill_all()
        return {"columns": columns}

    return app
