"""Errors raised by invoice-extract."""


class InvoiceError(Exception):
    """Base class of all errors in this package."""


class ConfigError(InvoiceError):
    """A setting is missing or a value is invalid."""


class ModelError(InvoiceError):
    """The model server did not answer, or the answer was unusable."""


class UnsupportedFileError(InvoiceError):
    """The file type is not supported or the file cannot be read."""


class LimitError(InvoiceError):
    """A limit was exceeded."""
