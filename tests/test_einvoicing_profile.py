import pytest

from invoice_extract.einvoicing.model import EInvoice, Party
from invoice_extract.einvoicing.profile import Profile, apply_profile, load_profile, profile_dumps, profile_loads
from invoice_extract.errors import ConfigError

YAML = """seller:
  name: My Shop GmbH
  street: Main Street 1
  postcode: "12345"
  city: Berlin
  country: DE
  vat_id: DE123456789
  contact_name: Jane Doe
  phone: "+49 30 123456"
  email: billing@example.com
iban: DE89370400440532013000
payment_terms: Payable within 14 days.
"""


def test_profile_roundtrip(tmp_path):
    p = profile_loads(YAML)
    assert p.seller.name == "My Shop GmbH" and p.seller.postcode == "12345" and p.bic == ""
    assert profile_loads(profile_dumps(p)) == p
    path = tmp_path / "company.yaml"
    path.write_text(YAML, encoding="utf-8")
    assert load_profile(path) == p


def test_profile_values_win_and_empty_values_keep():
    inv = EInvoice(seller=Party(name="Read Name", city="Hamburg", tax_number="12/345"), iban="DE00READ", bic="READBIC")
    out = apply_profile(inv, profile_loads(YAML))
    assert out.seller.name == "My Shop GmbH" and out.seller.city == "Berlin"
    assert out.seller.tax_number == "12/345"  # not in the profile: kept
    assert out.iban == "DE89370400440532013000" and out.bic == "READBIC"
    assert out.payment_terms == "Payable within 14 days."
    assert inv.seller.name == "Read Name"  # the input is not changed


def test_profile_rejects_unknown_keys():
    with pytest.raises(ConfigError, match="colour"):
        profile_loads("seller:\n  name: X\ncolour: red\n")
    with pytest.raises(ConfigError, match="nickname"):
        profile_loads("seller:\n  nickname: X\n")
    with pytest.raises(ConfigError):
        profile_loads("- just\n- a list\n")


def test_empty_profile():
    assert profile_loads("") == Profile()
