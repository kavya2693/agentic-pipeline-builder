import json
from pathlib import Path

import pandas as pd

from pipeline_builder.discovery import discover, infer_type


def test_catalog_matches_answer_key(drop: Path) -> None:
    catalog, _ = discover(drop / "raw")
    truth = json.loads((drop / "ground_truth" / "schema.json").read_text())
    for t, spec in truth["tables"].items():
        got = {c.name: c.logical_type for c in catalog.tables[t].columns}
        assert got == spec["columns"], t
        assert catalog.tables[t].primary_key == spec["primary_key"], t
    found = {(f.table, f.column, f.ref_table, f.ref_column) for f in catalog.foreign_keys}
    expected = {(f["table"], f["column"], f["ref_table"], f["ref_column"]) for f in truth["foreign_keys"]}
    assert found == expected


def test_renamed_foreign_key_is_found_by_values_and_name_token(drop: Path) -> None:
    catalog, _ = discover(drop / "raw")
    fk = next(f for f in catalog.foreign_keys if f.table == "payments")
    assert (fk.column, fk.ref_table, fk.ref_column) == ("order_ref", "orders", "order_id")


def test_type_tolerates_a_little_corruption_but_not_a_lot() -> None:
    clean = [str(i) for i in range(1000)]
    slightly = pd.Series(clean[:990] + ["two"] * 10)
    heavily = pd.Series(clean[:900] + ["two"] * 100)
    assert infer_type("quantity", slightly)[0] == "integer"
    assert infer_type("quantity", heavily)[0] == "string"


def test_zero_one_column_is_boolean_only_when_named_as_a_flag() -> None:
    values = pd.Series(["0", "1"] * 50)
    assert infer_type("is_member", values)[0] == "boolean"
    assert infer_type("items", values)[0] == "integer"
