import pytest

from invoice_extract.columns import (
    RESERVED,
    STANDARD_COLUMNS,
    Column,
    Template,
    column,
    make_key,
)
from invoice_extract.errors import ConfigError


def test_standard_columns_order_and_keys():
    assert [c.key for c in STANDARD_COLUMNS] == [
        "file", "supplier", "invoice_no", "invoice_date", "due_date",
        "net", "vat", "gross", "currency", "iban", "status",
    ]
    required = {c.key for c in STANDARD_COLUMNS if c.required}
    assert required == {"supplier", "invoice_no", "invoice_date", "gross"}
    assert RESERVED == {"file", "status"}


def test_make_key_unique():
    assert make_key("Kd.-Nr.", []) == "kd_nr"
    assert make_key("Kd.-Nr.", ["kd_nr"]) == "kd_nr_2"
    assert make_key("Kd.-Nr.", ["kd_nr", "kd_nr_2"]) == "kd_nr_3"


def test_make_key_non_ascii():
    assert make_key("Größe", []) == "grosse"
    assert make_key("請求", []) == "column"


def test_column_builds_unique_key():
    c = column("Customer No.", hint="label 'Kd.-Nr.'", existing=["customer_no"])
    assert c == Column(key="customer_no_2", label="Customer No.", hint="label 'Kd.-Nr.'")


def test_template_roundtrip(tmp_path):
    t = Template(name="office", columns=[
        Column("supplier", "Supplier", required=True),
        Column("cost_center", "Cost Center", hint="near 'KST'", type="text"),
        Column("paid_on", "Paid On", type="date"),
    ])
    assert Template.loads(t.dumps()) == t
    path = tmp_path / "t.yaml"
    t.save(path)
    assert Template.load(path) == t


def test_template_rejects_unknown_type():
    with pytest.raises(ConfigError, match="type"):
        Template.loads("name: x\ncolumns:\n  - {key: a, label: A, type: money}\n")


def test_template_rejects_missing_label():
    with pytest.raises(ConfigError, match="label"):
        Template.loads("name: x\ncolumns:\n  - {key: a}\n")


def test_v1_template_loads_as_values():
    t = Template.loads("name: t\ncolumns:\n  - label: Order No.\n")
    col = t.columns[0]
    assert col.kind == "value" and col.role == "" and col.table_columns == []


def test_template_v2_roundtrip():
    cols = [Column("net", "Net", type="amount", role="net"),
            Column("payment_note", "Payment Note", kind="text"),
            Column("line_items", "Line Items", kind="table", table_columns=["Description", "Amount"])]
    assert Template.loads(Template("t", cols).dumps()).columns == cols


def test_template_rejects_unknown_kind_and_role():
    with pytest.raises(ConfigError, match="kind"):
        Template.loads("columns:\n  - label: X\n    kind: picture\n")
    with pytest.raises(ConfigError, match="role"):
        Template.loads("columns:\n  - label: X\n    role: boss\n")


def test_export_columns_and_role_column():
    from invoice_extract.columns import export_columns, role_column
    gross = role_column("gross")
    assert gross.label == "Gross" and gross.type == "amount" and gross.role == "gross"
    assert [c.key for c in export_columns([gross])] == ["file", "gross", "status"]
