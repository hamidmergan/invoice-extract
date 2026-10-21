import os

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("INVOICE_EXTRACT_") or name == "OPENAI_API_KEY":
            monkeypatch.delenv(name)
