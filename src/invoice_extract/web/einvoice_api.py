"""Web API of the e-invoice converter: upload, read, edit, check and download XRechnung / ZUGFeRD files."""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from ..config import Settings
from ..core import _extract
from ..detect import detect
from ..einvoicing import ALREADY, output_name
from ..einvoicing.cii import build_cii
from ..einvoicing.fields import EINVOICE_FIELDS, field_path, from_row
from ..errors import InvoiceError
from ..einvoicing.model import EInvoice, from_dict, to_dict, totals
from ..einvoicing.profile import Profile, apply_profile, profile_dumps, profile_loads
from ..einvoicing.rules import FORMATS, check, errors, hints
from ..einvoicing.zugferd import write_zugferd
from ..results import Row

MEDIA = {"xrechnung": "application/xml", "zugferd": "application/pdf"}


@dataclass
class EEntry:
    id: str
    name: str
    path: Path
    kind: str = ""
    state: str = "pending"  # pending, running or done
    error: Optional[str] = None
    row: Optional[Row] = None
    invoice: Optional[EInvoice] = None


@dataclass
class EWorkspace:
    files: Dict[str, EEntry] = field(default_factory=dict)
    profile: Profile = field(default_factory=Profile)


class InvoiceBody(BaseModel):
    invoice: Dict[str, Any]


def _boxes(row: Optional[Row]) -> Dict[str, Any]:
    out = {}
    for key, cell in (row.cells.items() if row is not None else []):
        if cell.box is not None:
            out[field_path(key)] = asdict(cell.box)
    return out


def register(app: FastAPI, settings: Settings, lock: threading.Lock, pool: ThreadPoolExecutor,
             get_client: Callable, uploads: Path, save_upload: Callable) -> Callable[[str], Optional[EEntry]]:
    """Add the /api/e routes to `app`. Returns the lookup the page-image routes use for converter files."""
    ews = EWorkspace()

    def entry_or_404(file_id: str) -> EEntry:
        with lock:
            entry = ews.files.get(file_id)
        if entry is None:
            raise HTTPException(404, f"Unknown file id {file_id!r}")
        return entry

    def entry_dict(entry: EEntry) -> Dict[str, Any]:
        inv = entry.invoice
        data: Dict[str, Any] = {"id": entry.id, "name": entry.name, "kind": entry.kind, "state": entry.state,
                                "error": entry.error, "invoice": None, "issues": {}, "hints": [], "totals": None,
                                "boxes": _boxes(entry.row)}
        if inv is not None:
            data["invoice"] = to_dict(inv)
            data["issues"] = {fmt: [asdict(i) for i in check(inv, fmt)] for fmt in FORMATS}
            data["hints"] = [asdict(i) for i in hints(inv)]
            data["totals"] = {k: str(v) for k, v in asdict(totals(inv)).items()}
        return data

    def read_job(entry: EEntry) -> None:
        with lock:
            entry.state = "running"
            profile = ews.profile
        error, row, invoice, kind = None, None, None, ""
        try:
            kind = detect(entry.path, settings)
            if kind in ("xml", "zugferd"):
                error = ALREADY
            else:
                row = _extract(entry.path, EINVOICE_FIELDS, settings, get_client)
                invoice = apply_profile(from_row(row), profile)
        except Exception as e:  # one broken file must not stop the others
            error = f"{type(e).__name__}: {e}"
        with lock:
            entry.kind, entry.row, entry.invoice, entry.error = kind, row, invoice, error
            entry.state = "done"

    @app.post("/api/e/files")
    async def upload(files: List[UploadFile] = File(...)) -> Dict[str, Any]:
        with lock:
            total = len(ews.files) + len(files)
        if total > settings.max_files:
            raise HTTPException(413, f"{total} files in total, the limit is {settings.max_files} "
                                     "(raise it with --max-files)")
        entries = []
        for upload_file in files:
            file_id, name, path = await save_upload(upload_file)
            entries.append(EEntry(id=file_id, name=name, path=path))
        with lock:
            for entry in entries:
                ews.files[entry.id] = entry
                pool.submit(read_job, entry)
            return {"files": [entry_dict(e) for e in entries]}

    @app.delete("/api/e/files")
    def clear() -> Dict[str, Any]:
        with lock:
            entries = list(ews.files.values())
            ews.files.clear()
        for entry in entries:
            entry.path.unlink(missing_ok=True)
        return {"files": []}

    @app.get("/api/e/state")
    def state() -> Dict[str, Any]:
        with lock:
            return {"profile": asdict(ews.profile), "formats": list(FORMATS),
                    "files": [entry_dict(e) for e in ews.files.values()]}

    @app.put("/api/e/files/{file_id}")
    def edit(file_id: str, body: InvoiceBody) -> Dict[str, Any]:
        entry = entry_or_404(file_id)
        try:
            invoice = from_dict(body.invoice)
        except InvoiceError as e:
            raise HTTPException(400, str(e))
        with lock:
            if entry.state != "done" or entry.invoice is None:
                raise HTTPException(409, "This file has no invoice to edit")
            entry.invoice = invoice
            return entry_dict(entry)

    @app.get("/api/e/files/{file_id}/download")
    def download(file_id: str, format: str = "xrechnung") -> Response:
        if format not in FORMATS:
            raise HTTPException(400, f"Unknown format {format!r}; use one of: {', '.join(FORMATS)}")
        entry = entry_or_404(file_id)
        with lock:
            invoice = entry.invoice
        if invoice is None:
            raise HTTPException(409, entry.error or "This file has no invoice")
        problems = errors(invoice, format)
        if problems:
            return JSONResponse({"detail": "The e-invoice is not valid yet",
                                 "issues": [asdict(i) for i in problems]}, status_code=409)
        xml = build_cii(invoice, format)
        data = xml if format == "xrechnung" else write_zugferd(entry.path, xml)
        name = output_name(entry.name, format)
        return Response(data, media_type=MEDIA[format], headers={"Content-Disposition": f'attachment; filename="{name}"'})

    def set_profile(profile: Profile) -> Dict[str, Any]:
        with lock:
            ews.profile = profile
            for entry in ews.files.values():
                if entry.invoice is not None:
                    entry.invoice = apply_profile(entry.invoice, profile)
            return asdict(profile)

    @app.put("/api/e/profile")
    def put_profile(body: Dict[str, Any]) -> Dict[str, Any]:
        try:
            profile = profile_loads(yaml.safe_dump(body))
        except InvoiceError as e:
            raise HTTPException(400, str(e))
        return set_profile(profile)

    @app.post("/api/e/profile")
    async def load_profile(file: UploadFile = File(...)) -> Dict[str, Any]:
        try:
            profile = profile_loads((await file.read()).decode("utf-8"))
        except (InvoiceError, UnicodeDecodeError) as e:
            raise HTTPException(400, f"Invalid company profile: {e}")
        return set_profile(profile)

    @app.get("/api/e/profile.yaml")
    def profile_yaml() -> Response:
        with lock:
            text = profile_dumps(ews.profile)
        return Response(text, media_type="application/x-yaml",
                        headers={"Content-Disposition": 'attachment; filename="company.yaml"'})

    def lookup(file_id: str) -> Optional[EEntry]:
        with lock:
            return ews.files.get(file_id)

    return lookup
