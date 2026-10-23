"""Command-line interface: `invoice-extract FILES...` and `invoice-extract serve`."""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from .columns import RESERVED, STANDARD_COLUMNS, Template, column, export_columns
from .config import resolve
from .core import extract_many
from .errors import ConfigError, InvoiceError
from .einvoicing import FORMATS as FORMATS_E
from .einvoicing import convert_many, output_name, write
from .einvoicing.profile import load_profile
from .export import FORMATS, export

LOOPBACK = ("127.0.0.1", "localhost", "::1")
LIMITS = ("max_files", "max_file_mb", "max_pages", "concurrency", "timeout")
UI_HINT = (
    'The web UI needs extra packages: pip install '
    '"invoice-extract[ui] @ git+https://github.com/hamidmergan/invoice-extract.git"'
)
NO_LOGIN = "Warning: the UI has no login. Only expose it on a trusted network."


def _model_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("model (any OpenAI-compatible server)")
    group.add_argument("--base-url", help="server URL, e.g. http://localhost:11434/v1 (env INVOICE_EXTRACT_BASE_URL)")
    group.add_argument("--model", help="model name (env INVOICE_EXTRACT_MODEL)")
    group.add_argument("--vision-model", help="model for scans, if different (env INVOICE_EXTRACT_VISION_MODEL)")
    group.add_argument("--api-key", help="API key, not needed for local servers (env INVOICE_EXTRACT_API_KEY)")
    limits = parser.add_argument_group("limits")
    limits.add_argument("--max-files", type=int, help="most files per run")
    limits.add_argument("--max-file-mb", type=float, help="largest file in MB")
    limits.add_argument("--max-pages", type=int, help="most pages per file")
    limits.add_argument("--concurrency", type=int, help="files processed at once")
    limits.add_argument("--timeout", type=float, help="seconds per model request")


def _extract_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invoice-extract",
        description="Turn the invoices you received (PDF, scans, photos, XRechnung, ZUGFeRD) into one checked "
                    "table. Run 'invoice-extract serve' for the web page and 'invoice-extract einvoice' to write "
                    "XRechnung/ZUGFeRD e-invoices.",
    )
    parser.add_argument("files", nargs="+", metavar="FILE", help="PDF, image or XML invoices")
    parser.add_argument("-o", "--output", help="output file (.xlsx, .csv or .json); default invoices.xlsx")
    parser.add_argument("--template", help="YAML file with the columns to extract")
    parser.add_argument("--column", action="append", default=[], metavar="LABEL",
                        help="extra column to extract (repeatable)")
    parser.add_argument("--version", action="version", version=f"invoice-extract {__version__}")
    _model_flags(parser)
    return parser


def _serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="invoice-extract serve", description="Start the web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="port (default 8000)")
    parser.add_argument("--template", help="YAML file with the table's columns (default: the standard columns)")
    _model_flags(parser)
    return parser


def _options(args: argparse.Namespace) -> Dict[str, Any]:
    options = {"base_url": args.base_url, "model": args.model, "vision_model": args.vision_model,
               "api_key": args.api_key}
    options.update({name: getattr(args, name) for name in LIMITS if getattr(args, name) is not None})
    return options


def _default_output() -> Path:
    path = Path("invoices.xlsx")
    n = 1
    while path.exists():
        path = Path(f"invoices ({n}).xlsx")
        n += 1
    return path


def _output(value: Optional[str]) -> Path:
    if value is None:
        return _default_output()
    path = Path(value)
    fmt = path.suffix.lower().lstrip(".")
    if fmt not in FORMATS:
        raise ConfigError(f"Unknown export format {fmt!r}; use one of: {', '.join(FORMATS)}")
    if not path.parent.is_dir():
        raise ConfigError(f"Output folder does not exist: {path.parent}")
    return path


def _columns(args: argparse.Namespace) -> list:
    """The columns to fill: a template's, else the standard columns, plus any --column; framed by File and Status."""
    cols = list(Template.load(args.template).columns) if args.template else list(STANDARD_COLUMNS)
    cols = [c for c in cols if c.key not in RESERVED]
    for label in args.column:
        cols.append(column(label, existing=[c.key for c in cols]))
    return export_columns(cols)


def _run_extract(argv: List[str]) -> int:
    args = _extract_parser().parse_args(argv)
    try:
        output = _output(args.output)
        columns = _columns(args)
        rows = extract_many(args.files, columns, **_options(args))
    except InvoiceError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    for row in rows:
        line = f"{Path(row.path).name}: {row.status}"
        if row.error:
            line += f": {row.error}"
        print(line, file=sys.stderr)
    try:
        export(rows, output, columns)
    except OSError as e:
        print(f"Error: Cannot write {output}: {e}", file=sys.stderr)
        return 2
    to_check = sum(1 for r in rows if r.status != "ok")
    print(f"Wrote {output} ({len(rows)} files, {to_check} to check)", file=sys.stderr)
    return 1 if any(r.error for r in rows) else 0


def _einvoice_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invoice-extract einvoice",
        description="Turn normal invoices (PDF, scan, photo) into e-invoices: XRechnung (CII XML) or ZUGFeRD "
                    "(PDF with the XML inside). Invalid invoices are not written; their problems are listed.",
    )
    parser.add_argument("files", nargs="+", metavar="FILE", help="PDF or image invoices")
    parser.add_argument("--format", choices=FORMATS_E, default="xrechnung", help="output format (default xrechnung)")
    parser.add_argument("--profile", help="YAML file with your company's seller data")
    parser.add_argument("-o", "--output", default=".", help="output folder (default: current folder)")
    _model_flags(parser)
    return parser


def _unique_name(name: str, taken: set) -> str:
    """`name`, or `stem (2).ext` etc. when another file of this run already got it."""
    path = Path(name)
    candidate, n = name, 2
    while candidate in taken:
        candidate = f"{path.stem} ({n}){path.suffix}"
        n += 1
    taken.add(candidate)
    return candidate


def _run_einvoice(argv: List[str]) -> int:
    args = _einvoice_parser().parse_args(argv)
    try:
        profile = load_profile(args.profile) if args.profile else None
        out_dir = Path(args.output)
        if not out_dir.is_dir():
            raise ConfigError(f"Output folder does not exist: {out_dir}")
        conversions = convert_many(args.files, profile=profile, **_options(args))
    except InvoiceError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    failed = 0
    taken: set = set()
    for conv in conversions:
        name = Path(conv.path).name
        if conv.error:
            print(f"{name}: error: {conv.error}", file=sys.stderr)
            failed += 1
            continue
        issues = conv.issues(args.format)
        problems = [i for i in issues if i.level == "error"]
        if problems:
            failed += 1
            print(f"{name}: {len(problems)} problem{'s' if len(problems) != 1 else ''}", file=sys.stderr)
            for issue in problems:
                print(f"  - {issue.field}: {issue.message} [{issue.rule}]", file=sys.stderr)
            continue
        target = _unique_name(output_name(conv.path, args.format), taken)
        try:
            path = write(conv, args.format, out_dir, target)
        except Exception as e:  # one file that cannot be written must not stop the others
            print(f"{name}: error: {type(e).__name__}: {e}", file=sys.stderr)
            failed += 1
            continue
        print(f"{name}: written {path}", file=sys.stderr)
        for issue in issues:
            print(f"  (warning) {issue.field}: {issue.message} [{issue.rule}]", file=sys.stderr)
    return 1 if failed else 0


def _run_serve(argv: List[str]) -> int:
    args = _serve_parser().parse_args(argv)
    try:
        import uvicorn

        from .web.app import create_app
    except ImportError:
        print(UI_HINT, file=sys.stderr)
        return 2
    try:
        settings = resolve(**_options(args))
        columns = Template.load(args.template).columns if args.template else None
    except InvoiceError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    if args.host not in LOOPBACK:
        print(NO_LOGIN, file=sys.stderr)
    # on loopback, only accept our own host names, so other web pages cannot reach the UI via DNS rebinding
    allowed = list(LOOPBACK) if args.host in LOOPBACK else None
    uvicorn.run(create_app(settings, allowed_hosts=allowed, columns=columns), host=args.host, port=args.port)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "serve":
        return _run_serve(argv[1:])
    if argv and argv[0] == "einvoice":
        return _run_einvoice(argv[1:])
    return _run_extract(argv)


if __name__ == "__main__":
    sys.exit(main())
