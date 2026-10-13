"""Discovery agent: read a folder of raw files and infer types, keys and relationships.

Everything here is a deterministic rule over the data itself. The thresholds are written
down as constants because they are the decisions a reviewer will ask about: how much
corruption a column may carry and still be called an integer, how unique a column must be
to be a key, how many values must match before two columns are called related.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq
import yaml

from pipeline_builder import typing_rules as tr

# A column whose values parse as type T at this share is called T. The remainder are the
# column's defects, which the validation agent will catch; requiring 100% would let one bad
# value demote a whole integer column to text.
TYPE_SHARE = 0.98
# Keys: almost every row present and almost every value distinct. Not 100%, because the
# raw drop is exactly where duplicates and missing keys show up.
KEY_MAX_NULL = 0.01
KEY_MIN_DISTINCT = 0.99
# Relationships: share of the child's distinct values found in the parent key.
FK_MIN_CONTAINMENT = 0.9
FK_MIN_CONTAINMENT_UNNAMED = 0.99
SAMPLE_ROWS = 50_000
SUPPORTED = {".csv", ".json", ".jsonl", ".parquet"}


@dataclass
class ColumnSchema:
    name: str
    logical_type: str
    formats: list[str] = field(default_factory=list)
    null_rate: float = 0.0
    distinct_ratio: float = 0.0
    is_identifier: bool = False
    description: str = ""


@dataclass
class TableSchema:
    name: str
    file: str
    row_count: int
    columns: list[ColumnSchema]
    primary_key: list[str]

    def column(self, name: str) -> ColumnSchema:
        return next(c for c in self.columns if c.name == name)


@dataclass
class ForeignKey:
    table: str
    column: str
    ref_table: str
    ref_column: str
    containment: float
    named_match: bool


@dataclass
class Catalog:
    tables: dict[str, TableSchema]
    foreign_keys: list[ForeignKey]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tables": {k: asdict(v) for k, v in self.tables.items()},
            "foreign_keys": [asdict(f) for f in self.foreign_keys],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Catalog:
        tables = {
            k: TableSchema(
                name=v["name"],
                file=v["file"],
                row_count=v["row_count"],
                columns=[ColumnSchema(**c) for c in v["columns"]],
                primary_key=list(v["primary_key"]),
            )
            for k, v in d["tables"].items()
        }
        return cls(tables, [ForeignKey(**f) for f in d["foreign_keys"]])

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(self.to_dict(), sort_keys=False))

    def parents_of(self, table: str) -> list[ForeignKey]:
        return [f for f in self.foreign_keys if f.table == table]


def table_name(path: Path) -> str:
    return re.sub(r"[^a-z0-9_]", "_", path.stem.lower())


def _to_text(v: Any) -> Any:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


def load_raw(path: Path) -> pd.DataFrame:
    """Read any supported file as text columns with nulls normalised.

    Everything lands as text on purpose: the file's own type metadata is a guess made by
    whichever system exported it, and the point of discovery is to make that guess again
    from the values.
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path, dtype=str, keep_default_na=False, na_filter=False)
    elif suffix in {".json", ".jsonl"}:
        text = path.read_text()
        records = json.loads(text) if text.lstrip().startswith("[") else [
            json.loads(line) for line in text.splitlines() if line.strip()
        ]
        df = pd.json_normalize(records, sep="_").map(_to_text)
    elif suffix == ".parquet":
        table = pq.read_table(path)
        df = table.to_pandas(types_mapper=lambda t: pd.ArrowDtype(t))
        df = df.astype("object").map(_to_text)
    else:
        raise ValueError(f"unsupported file type: {path}")
    return df.apply(tr.normalise_nulls)


def load_folder(raw: Path) -> dict[str, tuple[Path, pd.DataFrame]]:
    files = sorted(p for p in raw.iterdir() if p.suffix.lower() in SUPPORTED)
    return {table_name(p): (p, load_raw(p)) for p in files}


def _is_identifier(name: str) -> bool:
    n = name.lower()
    return n in {"id", "sku"} or n.endswith(("_id", "_ref", "_key", "_sku"))


def infer_type(name: str, values: pd.Series) -> tuple[str, list[str]]:
    """Infer the logical type of one column of normalised text."""
    present = values.dropna()
    if present.empty:
        return "string", []
    if len(present) > SAMPLE_ROWS:
        present = present.sample(SAMPLE_ROWS, random_state=0)

    low = set(present.astype(str).str.lower().unique()[:50])
    bool_vocab = tr.TRUE_TOKENS | tr.FALSE_TOKENS
    if low <= bool_vocab and present.nunique() <= 8:
        # A column of only 0/1 is a flag only if its name says so; otherwise it is a count.
        flag_name = name.startswith(("is_", "has_")) or name.endswith(("_flag", "_opt_in"))
        if not low <= {"0", "1"} or flag_name:
            return "boolean", []

    for logical in ("integer", "decimal"):
        _, ok = tr.parse(present, logical)
        if ok.mean() >= TYPE_SHARE:
            return logical, []

    for logical, candidates in (("timestamp", tr.TIMESTAMP_FORMATS), ("date", tr.DATE_FORMATS)):
        fmts = tr.formats_used(present, candidates)
        if fmts:
            _, ok = tr.parse(present, logical, fmts)
            if ok.mean() >= TYPE_SHARE:
                return logical, fmts
    return "string", []


def _singular(table: str) -> str:
    if table.endswith("ies"):
        return table[:-3] + "y"
    if table.endswith("s"):
        return table[:-1]
    return table


def _key_text(values: pd.Series, logical: str) -> pd.Series:
    """Canonical text for comparing key values across tables (1,234 and 1234 are one key)."""
    if logical == "integer":
        parsed, ok = tr.parse(values, "integer")
        return parsed.astype("string").where(ok)
    return values.astype("string")


def infer_primary_key(name: str, df: pd.DataFrame, cols: dict[str, ColumnSchema]) -> list[str]:
    n = len(df)
    eligible = [
        c
        for c, s in cols.items()
        if s.logical_type in ("integer", "string") and s.null_rate <= KEY_MAX_NULL
    ]
    singles = [c for c in eligible if cols[c].distinct_ratio >= KEY_MIN_DISTINCT]

    def name_score(c: str) -> int:
        if c in {f"{_singular(name)}_id", "id"}:
            return 3
        return 2 if cols[c].is_identifier else 1

    if singles:
        order = list(df.columns)
        return [max(singles, key=lambda c: (name_score(c), cols[c].distinct_ratio, -order.index(c)))]

    best: tuple[float, int, list[str]] | None = None
    keyish = [c for c in eligible if cols[c].is_identifier or cols[c].logical_type == "integer"]
    for a, b in combinations(keyish, 2):
        ratio = (~df.duplicated(subset=[a, b])).sum() / max(n, 1)
        if ratio < KEY_MIN_DISTINCT:
            continue
        score = (ratio, int(cols[a].is_identifier) + int(cols[b].is_identifier), [a, b])
        if best is None or score[:2] > best[:2]:
            best = score
    return best[2] if best else []


def infer_foreign_keys(
    frames: dict[str, pd.DataFrame], tables: dict[str, TableSchema]
) -> list[ForeignKey]:
    parent_keys: dict[str, tuple[str, str, set[str]]] = {}
    for t, schema in tables.items():
        if len(schema.primary_key) == 1:
            pk = schema.primary_key[0]
            logical = schema.column(pk).logical_type
            parent_keys[t] = (pk, logical, set(_key_text(frames[t][pk], logical).dropna()))

    found: list[ForeignKey] = []
    for t, schema in tables.items():
        for col in schema.columns:
            if schema.primary_key == [col.name] or col.logical_type not in ("integer", "string"):
                continue
            tokens = set(col.name.lower().split("_"))
            best: tuple[int, float, str, str] | None = None
            for parent, (pk, logical, keys) in parent_keys.items():
                if parent == t or logical != col.logical_type:
                    continue
                named = 2 if col.name == pk else int(bool(tokens & {_singular(parent), pk.split("_")[0]}))
                if not named and not col.is_identifier:
                    continue
                # Row-weighted: a handful of orphan rows should not outweigh thousands of
                # rows that resolve, which a distinct-value ratio lets happen.
                values = _key_text(frames[t][col.name], logical).dropna()
                if values.nunique() < 2:
                    continue
                containment = float(values.isin(keys).mean())
                threshold = FK_MIN_CONTAINMENT if named else FK_MIN_CONTAINMENT_UNNAMED
                if containment < threshold:
                    continue
                cand = (named, containment, parent, pk)
                if best is None or cand[:2] > best[:2]:
                    best = cand
            if best:
                found.append(ForeignKey(t, col.name, best[2], best[3], round(best[1], 4), best[0] > 0))
    return found


def discover(raw: Path) -> tuple[Catalog, dict[str, pd.DataFrame]]:
    """Infer the full catalog for a raw folder. Returns the catalog and the loaded frames."""
    loaded = load_folder(raw)
    frames = {t: df for t, (_, df) in loaded.items()}
    tables: dict[str, TableSchema] = {}
    for t, (path, df) in loaded.items():
        cols: dict[str, ColumnSchema] = {}
        n = max(len(df), 1)
        for c in df.columns:
            logical, fmts = infer_type(c, df[c])
            present = df[c].dropna()
            cols[c] = ColumnSchema(
                name=c,
                logical_type=logical,
                formats=fmts,
                null_rate=round(1 - len(present) / n, 6),
                distinct_ratio=round(present.nunique() / max(len(present), 1), 6),
                is_identifier=_is_identifier(c),
            )
        pk = infer_primary_key(t, df, cols)
        tables[t] = TableSchema(t, path.name, len(df), list(cols.values()), pk)
    return Catalog(tables, infer_foreign_keys(frames, tables)), frames
