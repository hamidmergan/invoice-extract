"""Turn the invoices you received into one checked table."""

from .checks import duplicate_checks
from .columns import KINDS, ROLES, STANDARD_COLUMNS, Column, Template, column, export_columns, role_column
from .config import Settings, resolve
from .core import add_columns, extract, extract_many
from .errors import ConfigError, InvoiceError, LimitError, ModelError, UnsupportedFileError
from .export import export
from .results import Box, Cell, Check, Row

__version__ = "0.1.0"

__all__ = [
    "KINDS",
    "ROLES",
    "STANDARD_COLUMNS",
    "Box",
    "Cell",
    "Check",
    "Column",
    "ConfigError",
    "InvoiceError",
    "LimitError",
    "ModelError",
    "Row",
    "Settings",
    "Template",
    "UnsupportedFileError",
    "add_columns",
    "column",
    "duplicate_checks",
    "export",
    "export_columns",
    "extract",
    "extract_many",
    "resolve",
    "role_column",
]
