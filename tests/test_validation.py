from datetime import date
from pathlib import Path

import pandas as pd

from pipeline_builder.discovery import discover
from pipeline_builder.evaluate import score_flags
from pipeline_builder.validation import (
    _looks_like_typo,
    _shape_regex,
    build_rules,
    naive_rules,
    read_rules,
    run_rules,
    write_rules,
)

AS_OF = date(2026, 2, 1)


def test_repeated_typo_is_demoted_but_rare_real_value_is_not() -> None:
    share = pd.Series({"delivered": 0.78, "shipped": 0.07, "shiped": 0.002, "disputed": 0.0015})
    assert _looks_like_typo("shiped", share)
    assert not _looks_like_typo("disputed", share)
    assert _looks_like_typo("?", share)


def test_shape_regex() -> None:
    assert _shape_regex("SKU-000123") == r"^[A-Z]{3}\-\d{6}$"


def test_rules_round_trip_through_yaml(drop: Path, tmp_path: Path) -> None:
    catalog, frames = discover(drop / "raw")
    rules = build_rules(catalog, frames, AS_OF)
    write_rules(rules, tmp_path / "rules.yml")
    assert read_rules(tmp_path / "rules.yml") == rules


def test_agent_rules_beat_naive_rules_on_precision(drop: Path) -> None:
    catalog, frames = discover(drop / "raw")
    defects = pd.read_csv(drop / "ground_truth" / "defects.csv")
    agent = score_flags(run_rules(build_rules(catalog, frames, AS_OF), catalog, frames), defects)
    naive = score_flags(run_rules(naive_rules(catalog), catalog, frames), defects)
    for kind in ("null_required", "orphan_fk", "duplicate_row", "type_corruption", "future_date"):
        assert agent["by_defect"][kind]["recall"] == 1.0, kind
    assert agent["row_precision"] > 0.9
    assert naive["row_precision"] < 0.2


def test_future_dates_are_judged_against_the_run_date(drop: Path) -> None:
    catalog, frames = discover(drop / "raw")
    early = build_rules(catalog, frames, date(2024, 6, 1))
    flags = run_rules(early, catalog, frames)
    # With a run date inside the data range, legitimate later orders become violations.
    window = flags[flags["rule_id"] == "orders__ordered_at__window"]
    assert len(window) > 0.3 * len(frames["orders"])
