"""Embedded taxonomy loader and search."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field

_PACKAGE_DIR = Path(__file__).resolve().parent
_DATA_DIR = _PACKAGE_DIR.parent.parent / "data"


class TaxonomyEntry(BaseModel):
    """A single taxonomy skill entry."""

    id: UUID
    name: str
    category: str
    aliases: list[str] = Field(default_factory=list)


def load_taxonomy() -> list[TaxonomyEntry]:
    """Load the embedded taxonomy from data/taxonomy.json."""
    taxonomy_path = _DATA_DIR / "taxonomy.json"
    raw = taxonomy_path.read_text(encoding="utf-8")
    entries = json.loads(raw)
    return [TaxonomyEntry.model_validate(e) for e in entries]


def search(
    query: str,
    taxonomy: list[TaxonomyEntry] | None = None,
) -> list[TaxonomyEntry]:
    """Case-insensitive search across taxonomy names and aliases."""
    if taxonomy is None:
        taxonomy = load_taxonomy()
    q = query.lower()
    results: list[TaxonomyEntry] = []
    for entry in taxonomy:
        if q in entry.name.lower():
            results.append(entry)
            continue
        for alias in entry.aliases:
            if q in alias.lower():
                results.append(entry)
                break
    return results
