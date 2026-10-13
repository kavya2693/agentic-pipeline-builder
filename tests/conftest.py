from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from pipeline_builder.datagen import generate

AS_OF = date(2026, 2, 1)


@pytest.fixture(scope="session")
def drop(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small raw drop (about 30k rows) with its answer key."""
    root = tmp_path_factory.mktemp("drop")
    generate(root, scale=0.03, defect_rate=0.005, seed=11)
    return root
