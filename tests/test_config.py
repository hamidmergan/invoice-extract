import pytest

from invoice_extract.config import PLACEHOLDER_API_KEY, resolve
from invoice_extract.errors import ConfigError


def test_resolve_order(monkeypatch):
    monkeypatch.setenv("INVOICE_EXTRACT_MODEL", "env-model")
    assert resolve(model="arg-model").model == "arg-model"
    assert resolve().model == "env-model"
    monkeypatch.delenv("INVOICE_EXTRACT_MODEL")
    assert resolve().model is None
    assert resolve().max_files == 100


def test_require_llm_message():
    with pytest.raises(ConfigError) as exc:
        resolve().require_llm()
    message = str(exc.value)
    for part in ("--base-url", "INVOICE_EXTRACT_BASE_URL", "--model", "INVOICE_EXTRACT_MODEL"):
        assert part in message


def test_require_llm_passes_when_set():
    resolve(base_url="http://localhost:11434/v1", model="m").require_llm()


def test_no_error_without_llm_need():
    settings = resolve()
    assert settings.base_url is None


def test_api_key_fallback(monkeypatch):
    assert resolve().api_key == PLACEHOLDER_API_KEY == "not-needed"
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    assert resolve().api_key == "k"


def test_vision_falls_back_to_model():
    assert resolve(model="m").vision() == "m"
    assert resolve(model="m", vision_model="v").vision() == "v"


def test_numeric_env(monkeypatch):
    monkeypatch.setenv("INVOICE_EXTRACT_MAX_FILES", "5")
    assert resolve().max_files == 5
    monkeypatch.setenv("INVOICE_EXTRACT_MAX_FILES", "many")
    with pytest.raises(ConfigError) as exc:
        resolve()
    assert "INVOICE_EXTRACT_MAX_FILES" in str(exc.value)


def test_invalid_limit_rejected():
    with pytest.raises(ConfigError):
        resolve(max_files=0)
