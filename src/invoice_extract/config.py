"""Default values and resolution of settings.

Order: argument, then environment variable, then default.
"""

import os
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .errors import ConfigError

PLACEHOLDER_API_KEY = "not-needed"
ENV_PREFIX = "INVOICE_EXTRACT_"

DEFAULT_MAX_FILES = 100
DEFAULT_MAX_FILE_MB = 20.0
DEFAULT_MAX_PAGES = 50
DEFAULT_CONCURRENCY = 4
DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_RETRIES = 2


@dataclass(frozen=True)
class Settings:
    base_url: Optional[str] = None
    model: Optional[str] = None
    vision_model: Optional[str] = None
    api_key: Optional[str] = None
    max_files: int = DEFAULT_MAX_FILES
    max_file_mb: float = DEFAULT_MAX_FILE_MB
    max_pages: int = DEFAULT_MAX_PAGES
    concurrency: int = DEFAULT_CONCURRENCY
    timeout: float = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES

    def require_llm(self) -> None:
        """Raise ConfigError if base_url or model is missing."""
        missing = []
        if not self.base_url:
            missing.append(f"base_url (--base-url or {ENV_PREFIX}BASE_URL)")
        if not self.model:
            missing.append(f"model (--model or {ENV_PREFIX}MODEL)")
        if missing:
            raise ConfigError(
                "Missing: "
                + " and ".join(missing)
                + ". Example for Ollama: base_url http://localhost:11434/v1 and a model name."
            )

    def vision(self) -> str:
        """Model used for scans and box requests."""
        return self.vision_model or self.model or ""


def _env(name: str) -> Optional[str]:
    value = os.environ.get(ENV_PREFIX + name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _pick(arg: Any, env_name: str, default: Any, cast: Callable[[str], Any]) -> Any:
    if arg is not None:
        return arg
    raw = _env(env_name)
    if raw is None:
        return default
    try:
        return cast(raw)
    except ValueError:
        raise ConfigError(
            f"Environment variable {ENV_PREFIX}{env_name} has an invalid value: {raw!r}"
        ) from None


def _validate(settings: Settings) -> None:
    for name in ("max_files", "max_file_mb", "max_pages", "concurrency", "timeout"):
        if getattr(settings, name) <= 0:
            raise ConfigError(f"{name} must be greater than 0.")
    if settings.max_retries < 0:
        raise ConfigError("max_retries must be 0 or greater.")


def resolve(
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    vision_model: Optional[str] = None,
    api_key: Optional[str] = None,
    max_files: Optional[int] = None,
    max_file_mb: Optional[float] = None,
    max_pages: Optional[int] = None,
    concurrency: Optional[int] = None,
    timeout: Optional[float] = None,
    max_retries: Optional[int] = None,
) -> Settings:
    """Build Settings from arguments, environment variables and defaults."""
    key = _pick(api_key, "API_KEY", None, str) or os.environ.get("OPENAI_API_KEY")
    settings = Settings(
        base_url=_pick(base_url, "BASE_URL", None, str),
        model=_pick(model, "MODEL", None, str),
        vision_model=_pick(vision_model, "VISION_MODEL", None, str),
        api_key=key or PLACEHOLDER_API_KEY,
        max_files=_pick(max_files, "MAX_FILES", DEFAULT_MAX_FILES, int),
        max_file_mb=_pick(max_file_mb, "MAX_FILE_MB", DEFAULT_MAX_FILE_MB, float),
        max_pages=_pick(max_pages, "MAX_PAGES", DEFAULT_MAX_PAGES, int),
        concurrency=_pick(concurrency, "CONCURRENCY", DEFAULT_CONCURRENCY, int),
        timeout=_pick(timeout, "TIMEOUT", DEFAULT_TIMEOUT, float),
        max_retries=_pick(max_retries, "MAX_RETRIES", DEFAULT_MAX_RETRIES, int),
    )
    _validate(settings)
    return settings
