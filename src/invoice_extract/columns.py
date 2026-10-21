"""Columns of the result table and reusable column templates."""

import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Iterable, List, Union

import yaml

from .errors import ConfigError

TYPES = ("text", "date", "amount", "iban", "file", "status")

# Filled by invoice-extract itself, never sent to the LLM.
RESERVED = {"file", "status"}

# What a field holds: one value, a block of text, or a table of rows.
KINDS = ("value", "text", "table")

# Meanings the checks rely on; each equals the key of the standard column with that meaning.
ROLES = ("supplier", "invoice_no", "invoice_date", "due_date", "net", "vat", "gross", "currency", "iban")


@dataclass
class Column:
    key: str
    label: str
    hint: str = ""
    required: bool = False
    type: str = "text"
    kind: str = "value"
    role: str = ""
    table_columns: List[str] = field(default_factory=list)


STANDARD_COLUMNS: List[Column] = [
    Column("file", "File", type="file"),
    Column("supplier", "Supplier", required=True),
    Column("invoice_no", "Invoice No.", required=True),
    Column("invoice_date", "Invoice Date", required=True, type="date"),
    Column("due_date", "Due Date", type="date"),
    Column("net", "Net", type="amount"),
    Column("vat", "VAT", type="amount"),
    Column("gross", "Gross", required=True, type="amount"),
    Column("currency", "Currency"),
    Column("iban", "IBAN", type="iban"),
    Column("status", "Status", type="status"),
]

FILE_COLUMN = STANDARD_COLUMNS[0]
STATUS_COLUMN = STANDARD_COLUMNS[-1]


def role_column(role: str) -> Column:
    """A copy of the standard column that has this meaning, with `role` set."""
    standard = next(c for c in STANDARD_COLUMNS if c.key == role)
    return replace(standard, role=role)


def export_columns(fields: List[Column]) -> List[Column]:
    """The selected fields framed by File and Status, as the export expects them."""
    return [FILE_COLUMN] + list(fields) + [STATUS_COLUMN]


def make_key(label: str, existing: Iterable[str]) -> str:
    """Turn a label into a unique ASCII key, e.g. "Kd.-Nr." -> "kd_nr"."""
    text = label.lower().replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    base = "_".join(re.findall(r"[a-z0-9]+", text)) or "column"
    taken = set(existing)
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


def column(label: str, hint: str = "", type: str = "text", existing: Iterable[str] = ()) -> Column:
    """Build a custom column with a key that does not clash with `existing`."""
    return Column(key=make_key(label, existing), label=label, hint=hint, type=type)


@dataclass
class Template:
    name: str
    columns: List[Column]

    @classmethod
    def load(cls, path: Union[str, Path]) -> "Template":
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as e:
            raise ConfigError(f"Cannot read template {path}: {e}") from e
        return cls.loads(text)

    @classmethod
    def loads(cls, text: str) -> "Template":
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ConfigError(f"Template is not valid YAML: {e}") from e
        if not isinstance(data, dict) or not isinstance(data.get("columns"), list):
            raise ConfigError("Template must be a mapping with a 'columns' list.")
        columns: List[Column] = []
        for i, item in enumerate(data["columns"], 1):
            if not isinstance(item, dict) or not str(item.get("label") or "").strip():
                raise ConfigError(f"Template column {i} has no label.")
            label = str(item["label"])
            type_ = str(item.get("type") or "text")
            if type_ not in TYPES:
                raise ConfigError(
                    f"Template column {label!r} has unknown type {type_!r}; use one of {', '.join(TYPES)}."
                )
            kind = str(item.get("kind") or "value")
            if kind not in KINDS:
                raise ConfigError(f"Template column {label!r} has unknown kind {kind!r}; use one of {', '.join(KINDS)}.")
            role = str(item.get("role") or "")
            if role and role not in ROLES:
                raise ConfigError(f"Template column {label!r} has unknown role {role!r}; use one of {', '.join(ROLES)}.")
            table_columns = item.get("table_columns") or []
            if not isinstance(table_columns, list):
                raise ConfigError(f"Template column {label!r}: table_columns must be a list.")
            keys = [c.key for c in columns]
            key = str(item.get("key") or "") or make_key(label, keys)
            if key in keys:
                raise ConfigError(f"Template has duplicate column key {key!r}.")
            columns.append(Column(
                key=key,
                label=label,
                hint=str(item.get("hint") or ""),
                required=bool(item.get("required", False)),
                type=type_,
                kind=kind,
                role=role,
                table_columns=[str(name) for name in table_columns],
            ))
        return cls(name=str(data.get("name") or ""), columns=columns)

    def dumps(self) -> str:
        data = {"name": self.name, "columns": [asdict(c) for c in self.columns]}
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    def save(self, path: Union[str, Path]) -> None:
        Path(path).write_text(self.dumps(), encoding="utf-8")
