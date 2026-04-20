"""Embedded taxonomy loader and search."""

from __future__ import annotations

import json
from importlib.resources import files
from uuid import UUID

from pydantic import BaseModel, Field


class TaxonomyEntry(BaseModel):
    """A single taxonomy skill entry."""

    id: UUID
    name: str
    category: str
    aliases: list[str] = Field(default_factory=list)


def load_taxonomy() -> list[TaxonomyEntry]:
    """Load the embedded taxonomy packaged with traitprint."""
    resource = files("traitprint.data").joinpath("taxonomy.json")
    raw = resource.read_text(encoding="utf-8")
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


def find_exact(
    name: str,
    taxonomy: list[TaxonomyEntry] | None = None,
) -> TaxonomyEntry | None:
    """Return an exact (case-insensitive) match by name or alias, or None."""
    if taxonomy is None:
        taxonomy = load_taxonomy()
    lower = name.lower()
    for entry in taxonomy:
        if entry.name.lower() == lower:
            return entry
        for alias in entry.aliases:
            if alias.lower() == lower:
                return entry
    return None


def suggest_matches(
    name: str,
    taxonomy: list[TaxonomyEntry] | None = None,
    limit: int = 5,
) -> list[TaxonomyEntry]:
    """Return close matches (substring search), excluding exact matches.

    Useful for "Did you mean ...?" prompts.
    """
    if taxonomy is None:
        taxonomy = load_taxonomy()
    exact = find_exact(name, taxonomy)
    results = search(name, taxonomy)
    # Exclude the exact match if present
    if exact is not None:
        results = [r for r in results if r.id != exact.id]
    return results[:limit]
