"""Column and table descriptions for the generated schema.yml.

The default is deterministic: descriptions are assembled from what discovery and validation
found (key, reference, allowed values, null rate). An optional local model can add a short
plain-language lead to each column, but the facts are always appended by code after it, so
the model can word a description and cannot change what it says about keys or values.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from pipeline_builder.discovery import Catalog
from pipeline_builder.validation import Rule

Generate = Callable[[str], str]
MAX_LEAD_CHARS = 160


def ollama_client(model: str = "llama3.2", host: str | None = None, timeout: float = 120.0) -> Generate:
    base = host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")

    def generate(prompt: str) -> str:
        body = json.dumps(
            {"model": model, "prompt": prompt, "stream": False, "format": "json", "options": {"temperature": 0}}
        ).encode()
        req = urllib.request.Request(f"{base}/api/generate", body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return str(json.loads(resp.read())["response"])

    return generate


@dataclass
class Descriptions:
    tables: dict[str, str] = field(default_factory=dict)
    columns: dict[tuple[str, str], str] = field(default_factory=dict)
    model_leads: int = 0
    model_rejected: int = 0

    def table(self, t: str) -> str:
        return self.tables.get(t, "")

    def column(self, t: str, c: str) -> str:
        return self.columns.get((t, c), "")


def _facts(catalog: Catalog, rules: list[Rule], t: str, c: str) -> str:
    schema = catalog.tables[t]
    col = schema.column(c)
    parts = [col.logical_type.capitalize() + "."]
    if schema.primary_key == [c]:
        parts.append("Primary key.")
    elif c in schema.primary_key:
        parts.append(f"Part of the key ({', '.join(schema.primary_key)}).")
    for fk in catalog.parents_of(t):
        if fk.column == c:
            parts.append(f"References {fk.ref_table}.{fk.ref_column}.")
    for r in rules:
        if r.table == t and r.column == c and r.expectation == "expect_column_values_to_be_in_set":
            parts.append("One of: " + ", ".join(r.kwargs["value_set"]) + ".")
    if col.formats:
        parts.append("Source formats: " + ", ".join(col.formats) + ".")
    if col.null_rate > 0.01:
        parts.append(f"Empty in {col.null_rate:.0%} of source rows.")
    return " ".join(parts)


def _model_leads(generate: Generate, catalog: Catalog, t: str) -> dict[str, str]:
    schema = catalog.tables[t]
    listing = "\n".join(f"- {c.name} ({c.logical_type})" for c in schema.columns)
    prompt = (
        f"Table `{t}` from a retailer's nightly data export. Columns:\n{listing}\n\n"
        "For each column write one short plain-English phrase (under 15 words) saying what it holds. "
        "Do not mention data types, keys or example values. "
        'Answer only with JSON: {"column_name": "phrase", ...}'
    )
    data = json.loads(generate(prompt))
    if not isinstance(data, dict):
        raise ValueError("model did not return an object")
    return {str(k): str(v) for k, v in data.items()}


def describe(catalog: Catalog, rules: list[Rule], generate: Generate | None = None) -> Descriptions:
    d = Descriptions()
    for t, schema in catalog.tables.items():
        refs = [f"{f.ref_table}" for f in catalog.parents_of(t)]
        key = ", ".join(schema.primary_key) or "none found"
        d.tables[t] = (
            f"{schema.row_count:,} source rows from {schema.file}. Key: {key}."
            + (f" Points to {', '.join(sorted(set(refs)))}." if refs else "")
        )
        leads: dict[str, str] = {}
        if generate is not None:
            try:
                leads = _model_leads(generate, catalog, t)
            except Exception:  # a model that is down or returns junk must not stop the build
                d.model_rejected += len(schema.columns)
        names = {c.name for c in schema.columns}
        for c in schema.columns:
            facts = _facts(catalog, rules, t, c.name)
            lead = " ".join(leads.get(c.name, "").split())
            if lead and len(lead) <= MAX_LEAD_CHARS and c.name in names:
                d.columns[(t, c.name)] = f"{lead.rstrip('.')}. {facts}"
                d.model_leads += 1
            else:
                if lead:
                    d.model_rejected += 1
                d.columns[(t, c.name)] = facts
    return d
