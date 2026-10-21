"""The company profile: your own seller data, saved once and used for every e-invoice you write."""

import copy
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Union

import yaml

from ..errors import ConfigError
from .model import EInvoice, Party

PARTY_KEYS = {f.name for f in fields(Party)}
TOP_KEYS = {"seller", "iban", "bic", "payment_terms"}


@dataclass
class Profile:
    seller: Party = field(default_factory=Party)
    iban: str = ""
    bic: str = ""
    payment_terms: str = ""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def profile_loads(text: str) -> Profile:
    """A profile from YAML. Unknown keys are an error, so a typo is not silently ignored."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"Company profile is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError("Company profile must be a mapping.")
    unknown = sorted(set(data) - TOP_KEYS)
    if unknown:
        raise ConfigError(f"Company profile has unknown keys: {', '.join(unknown)}")
    seller = data.get("seller") or {}
    if not isinstance(seller, dict):
        raise ConfigError("Company profile: 'seller' must be a mapping.")
    unknown = sorted(set(seller) - PARTY_KEYS)
    if unknown:
        raise ConfigError(f"Company profile has unknown seller keys: {', '.join(unknown)}")
    return Profile(seller=Party(**{k: _text(seller.get(k)) for k in PARTY_KEYS}),
                   **{k: _text(data.get(k)) for k in ("iban", "bic", "payment_terms")})


def profile_dumps(profile: Profile) -> str:
    return yaml.safe_dump(asdict(profile), sort_keys=False, allow_unicode=True)


def load_profile(path: Union[str, Path]) -> Profile:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError(f"Cannot read company profile {path}: {e}") from e
    return profile_loads(text)


def apply_profile(inv: EInvoice, profile: Profile) -> EInvoice:
    """A copy of `inv` where every non-empty profile value replaces the seller value read from the invoice."""
    out = copy.deepcopy(inv)
    for key in PARTY_KEYS:
        value = getattr(profile.seller, key)
        if value:
            setattr(out.seller, key, value)
    for key in ("iban", "bic", "payment_terms"):
        value = getattr(profile, key)
        if value:
            setattr(out, key, value)
    return out
