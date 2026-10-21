"""Web API for the review UI: upload invoices, let the model find the fields, pick fields, edit and export."""

import copy
import io
import re
import tempfile
import threading
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set
from urllib.parse import urlsplit

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..checks import run_checks
from ..columns import RESERVED, Column, Template, export_columns
from ..config import Settings
from ..core import _extract, _lazy_client
from ..discover import discover_file, field_counts, fields_of, merge_rows, with_hints
from ..errors import InvoiceError
from ..export import FORMATS, export, table_csv_path
from ..normalize import normalize
from ..pdf import page_count, render_page
from ..results import Cell, Check, Row

STATIC = Path(__file__).parent / "static"
MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv",
    "json": "application/json",
}
_SUFFIX = re.compile(r"\.[a-z0-9]{1,10}")
# Row checks that come from reading the file, not from the values; they survive a manual edit.
_READ_CHECKS = ("role_conflict", "merge_failed", "discovery_failed")


@dataclass
class FileEntry:
    id: str
    name: str
    path: Path
    kind: str = ""
    state: str = "pending"  # pending, running, merging (waits for the merge across invoices) or done
    row: Optional[Row] = None


@dataclass
class Workspace:
    files: Dict[str, FileEntry] = field(default_factory=dict)
    pngs: Dict[tuple, bytes] = field(default_factory=dict)
    mode: str = "discover"  # "discover": the model finds the fields; "template": fill the template's fields
    template: List[Column] = field(default_factory=list)
    fields: List[Column] = field(default_factory=list)
    selected: Set[str] = field(default_factory=set)
    generation: int = 0  # bumped when the mode changes or files are cleared; older job results are dropped
    merging: bool = False


class Selection(BaseModel):
    keys: List[str]


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

    def finished_rows() -> List[Row]:
        """Rows of finished files. Call under the lock."""
        return [e.row for e in ws.files.values() if e.state == "done" and e.row is not None]

    def extract_job(entry: FileEntry, generation: int) -> None:
        with lock:
            if generation != ws.generation:
                return
            entry.state = "running"
            mode, template = ws.mode, list(ws.template)
        try:
            if mode == "discover":
                row = discover_file(entry.path, settings, get_client)
            else:
                row = _extract(entry.path, template, settings, get_client)
        except Exception as e:  # one broken file must not stop the others
            row = Row(path=str(entry.path), error=f"{type(e).__name__}: {e}")
        if "file" in row.cells:
            row.cells["file"].value = entry.name
        with lock:
            if generation != ws.generation:
                return
            entry.kind = row.kind
            entry.row = row
            entry.state = "done" if mode == "template" else "merging"
            start_merge()

    def start_merge() -> None:
        """Merge the waiting files once no file is being read any more. Call under the lock."""
        if ws.mode != "discover" or ws.merging:
            return
        if any(e.state in ("pending", "running") for e in ws.files.values()):
            return
        batch = [e for e in ws.files.values() if e.state == "merging"]
        if batch:
            ws.merging = True
            pool.submit(merge_job, batch, ws.generation)

    def merge_job(batch: List[FileEntry], generation: int) -> None:
        with lock:
            known = list(ws.fields)
        rows = [e.row for e in batch if e.row is not None]
        try:
            merge_rows(rows, settings, get_client, known)
        except Exception:  # merging is best effort; the fields then stay as each invoice named them
            for row in rows:
                row.checks.append(Check("merge_failed", "warning", "Fields could not be merged across invoices"))
        with lock:
            if generation != ws.generation:
                return  # the files were cleared or re-read meanwhile; a newer merge owns the workspace
            ws.merging = False
            before = {c.key for c in ws.fields}
            ws.fields = fields_of(finished_rows() + rows, known=ws.fields)
            ws.selected |= {c.key for c in ws.fields if c.key not in before}
            for entry in batch:
                entry.state = "done"
            start_merge()

    def rerun_all() -> None:
        """Read every file again, e.g. after the mode changed. Call under the lock."""
        ws.generation += 1
        ws.merging = False
        for entry in ws.files.values():
            entry.state, entry.row = "pending", None
            pool.submit(extract_job, entry, ws.generation)

    def selected_fields() -> List[Column]:
        """The selected fields in list order. Call under the lock."""
        return [c for c in ws.fields if c.key in ws.selected]

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
                pool.submit(extract_job, entry, ws.generation)
            return {"files": [_entry_dict(e) for e in entries]}

    @app.delete("/api/files")
    def clear() -> Dict[str, Any]:
        with lock:
            entries = list(ws.files.values())
            ws.files.clear()
            ws.pngs.clear()
            ws.generation += 1
            ws.merging = False
            if ws.mode == "discover":
                ws.fields, ws.selected = [], set()
        for entry in entries:
            entry.path.unlink(missing_ok=True)
        return {"files": []}

    @app.get("/api/state")
    def state() -> Dict[str, Any]:
        with lock:
            files = [_entry_dict(e) for e in ws.files.values()]
            counts = field_counts(finished_rows())
            fields = [{**asdict(c), "selected": c.key in ws.selected, "count": counts.get(c.key, 0)}
                      for c in ws.fields]
            mode = ws.mode
        to_check = sum(1 for f in files if f["state"] == "done" and f["status"] in ("warning", "error"))
        summary = {"files": len(files), "fields": sum(1 for f in fields if f["selected"]), "to_check": to_check}
        return {"mode": mode, "fields": fields, "files": files, "summary": summary}

    @app.put("/api/selection")
    def select(body: Selection) -> Dict[str, Any]:
        with lock:
            unknown = [k for k in body.keys if k not in {c.key for c in ws.fields}]
            if unknown:
                raise HTTPException(400, f"Unknown fields: {', '.join(unknown)}")
            ws.selected = set(body.keys)
            return {"selected": [c.key for c in selected_fields()]}

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

    @app.patch("/api/files/{file_id}/cells/{key}")
    def edit_cell(file_id: str, key: str, body: CellEdit) -> Dict[str, Any]:
        entry = entry_or_404(file_id)
        if key in RESERVED:
            raise HTTPException(400, f"Field {key!r} cannot be edited")
        with lock:
            col = next((c for c in ws.fields if c.key == key), None)
            if col is None:
                raise HTTPException(404, f"Unknown field {key!r}")
            if col.kind == "table":
                raise HTTPException(400, "Tables cannot be edited")
            row = entry.row
            if entry.state != "done" or row is None or row.error:
                raise HTTPException(409, "This file has no finished values to edit")
            old = row.cells.get(key, Cell())
            value, checks = normalize(col.type, body.value, "")
            row.cells[key] = Cell(value=value, raw=old.raw, page=old.page, box=old.box, source="manual",
                                  edited=True, checks=checks, kind=col.kind, label=col.label, role=col.role,
                                  type=col.type)
            kept = [c for c in row.checks if c.name in _READ_CHECKS]
            row.checks = kept + run_checks(row, ws.template if ws.mode == "template" else [])
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
            columns = export_columns(selected_fields())
        tables = [c for c in columns if c.kind == "table"]
        with tempfile.TemporaryDirectory(prefix="invoice-extract-export-") as out:
            path = export(rows, Path(out) / f"invoices.{format}", columns, format)
            if format == "csv" and tables:
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                    for file in [path] + [table_csv_path(path, c) for c in tables]:
                        archive.write(file, file.name)
                return Response(buffer.getvalue(), media_type="application/zip",
                                headers={"Content-Disposition": 'attachment; filename="invoices.zip"'})
            data = path.read_bytes()
        return Response(data, media_type=MEDIA_TYPES[format],
                        headers={"Content-Disposition": f'attachment; filename="invoices.{format}"'})

    @app.get("/api/template")
    def get_template() -> Response:
        with lock:
            text = Template("invoices", with_hints(selected_fields(), finished_rows())).dumps()
        return Response(text, media_type="application/x-yaml",
                        headers={"Content-Disposition": 'attachment; filename="invoices.yaml"'})

    @app.post("/api/template")
    async def put_template(file: UploadFile = File(...)) -> Dict[str, Any]:
        try:
            template = Template.loads((await file.read()).decode("utf-8"))
        except (InvoiceError, UnicodeDecodeError) as e:
            raise HTTPException(400, f"Invalid template: {e}")
        with lock:
            ws.mode = "template"
            ws.template = list(template.columns)
            ws.fields = [c for c in template.columns if c.key not in RESERVED]  # File and Status frame every export
            ws.selected = {c.key for c in ws.fields}
            rerun_all()
            return {"mode": ws.mode, "fields": [asdict(c) for c in ws.fields]}

    @app.delete("/api/template")
    def drop_template() -> Dict[str, Any]:
        with lock:
            ws.mode = "discover"
            ws.template, ws.fields, ws.selected = [], [], set()
            rerun_all()
            return {"mode": ws.mode}

    return app
