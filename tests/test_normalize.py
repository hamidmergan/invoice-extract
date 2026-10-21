import datetime
from decimal import Decimal

import pytest

from invoice_extract.normalize import display_variants, normalize


def names(checks):
    return [c.name for c in checks]


def test_amount_german():
    value, checks = normalize("amount", "1234.50", "1.234,50 €")
    assert value == Decimal("1234.50")
    assert checks == []


def test_amount_english():
    value, checks = normalize("amount", "1234.50", "1,234.50")
    assert value == Decimal("1234.50")
    assert checks == []


def test_amount_ambiguous():
    value, checks = normalize("amount", "1234", "1.234")
    assert value == Decimal("1234")
    assert names(checks) == ["amount_ambiguous"]
    assert checks[0].level == "warning"


def test_amount_mismatch():
    value, checks = normalize("amount", "99.00", "1.234,50")
    assert value == Decimal("99.00")
    assert names(checks) == ["value_mismatch"]


def test_amount_from_raw_when_llm_value_is_not_a_number():
    value, checks = normalize("amount", "1.234,50", "EUR 1.234,50")
    assert value == Decimal("1234.50")
    assert checks == []


def test_amount_unparsed_kept():
    value, checks = normalize("amount", "n/a", "")
    assert value == "n/a"
    assert names(checks) == ["unparsed"]


def test_date_iso_from_german():
    value, checks = normalize("date", "2026-10-21", "21.10.2026")
    assert value == datetime.date(2026, 10, 21)
    assert checks == []


def test_date_english_month_and_short_year():
    assert normalize("date", "2026-10-21", "21 October 2026") == (datetime.date(2026, 10, 21), [])
    assert normalize("date", "2026-10-21", "21.10.26") == (datetime.date(2026, 10, 21), [])


def test_date_ambiguous():
    value, checks = normalize("date", "2026-04-03", "03/04/2026")
    assert value == datetime.date(2026, 4, 3)
    assert names(checks) == ["date_ambiguous"]


def test_date_mismatch():
    value, checks = normalize("date", "2026-10-22", "21.10.2026")
    assert value == datetime.date(2026, 10, 22)
    assert names(checks) == ["value_mismatch"]


def test_date_unparsed_kept():
    value, checks = normalize("date", "sometime", "sometime")
    assert value == "sometime"
    assert names(checks) == ["unparsed"]


def test_no_raw_no_checks():
    assert normalize("date", "2026-10-21", None) == (datetime.date(2026, 10, 21), [])
    assert normalize("amount", 12.5, "") == (Decimal("12.5"), [])


def test_text_and_none():
    assert normalize("text", "  ACME GmbH ", "") == ("ACME GmbH", [])
    assert normalize("text", None, "") == (None, [])


@pytest.mark.parametrize("value, expected", [
    ("19 %", "19"), ("0.19", "19"), ("19,00", "19"), ("19 % / 7 %", "19, 7"), (19, "19"), ("0", "0"),
    ("5.5", "5.5"), ("MwSt 19% zzgl. 7%", "19, 7"), ("19 %, 19 %", "19"), ("", None), (None, None), (0.07, "7"),
    ("20", "20"),
])
def test_percent_forms(value, expected):
    assert normalize("percent", value, "") == (expected, [])


def test_percent_unparsed_kept():
    value, checks = normalize("percent", "none", "")
    assert value == "none" and names(checks) == ["unparsed"]
    assert display_variants("percent", "19, 7") == ["19 %", "19%", "7 %", "7%", "19", "7"]


@pytest.mark.parametrize("value, expected", [
    ("Gutschrift", "credit note"), ("Credit Note", "credit note"), ("Stornorechnung", "credit note"),
    ("Rechnung", "invoice"), ("invoice", "invoice"), ("", None), (None, None),
])
def test_doctype_forms(value, expected):
    assert normalize("doctype", value, "") == (expected, [])


def test_iban_cleanup():
    assert normalize("iban", "de89 3704 0044 0532 0130 00", "") == ("DE89370400440532013000", [])


def test_display_variants_amount():
    assert display_variants("amount", Decimal("1234.50")) == [
        "1.234,50",
        "1,234.50",
        "1234,50",
        "1234.50",
        "1 234,50",
    ]


def test_display_variants_date_and_text():
    assert display_variants("date", datetime.date(2026, 10, 21)) == [
        "21.10.2026",
        "21/10/2026",
        "2026-10-21",
        "10/21/2026",
    ]
    assert display_variants("text", "ACME") == ["ACME"]


def test_amount_hyphen_in_label_is_not_a_minus():
    value, checks = normalize("amount", "10.0756", "Nettorechnungs-Betrag 10,0756 EUR")
    assert value == Decimal("10.0756")
    assert checks == []


def test_amount_raw_with_several_numbers():
    value, checks = normalize("amount", "10.0756", "19 % 10,0756 EUR")
    assert value == Decimal("10.0756")
    assert checks == []


def test_amount_negative_forms():
    assert normalize("amount", "-5.00", "Gutschrift -5,00 EUR") == (Decimal("-5.00"), [])
    assert normalize("amount", "-100.00", "100,00-") == (Decimal("-100.00"), [])
