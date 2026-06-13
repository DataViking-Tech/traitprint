"""Embedded taxonomy loader and search."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TaxonomyEntry(BaseModel):
    """A single taxonomy skill entry.

    ``neighbors`` maps the canonical name of a related entry to an edge
    distance in ``[0, 1]``. Smaller means closer. Edges are expected to be
    declared once and are treated as symmetric by :func:`build_neighbor_index`.
    """

    id: UUID
    name: str
    category: str
    aliases: list[str] = Field(default_factory=list)
    neighbors: dict[str, float] = Field(default_factory=dict)


def build_neighbor_index(
    taxonomy: list[TaxonomyEntry],
) -> dict[UUID, dict[UUID, float]]:
    """Build a symmetric ``{id: {neighbor_id: distance}}`` adjacency index.

    Neighbor names are resolved case-insensitively against canonical names.
    Unresolved names are silently dropped (lets the taxonomy tolerate
    forward/trailing references during edits). When both directions of an
    edge are declared with different weights, the smaller distance wins.
    """
    name_to_id = {e.name.lower(): e.id for e in taxonomy}
    index: dict[UUID, dict[UUID, float]] = {e.id: {} for e in taxonomy}
    for entry in taxonomy:
        for neighbor_name, distance in entry.neighbors.items():
            neighbor_id = name_to_id.get(neighbor_name.lower())
            if neighbor_id is None or neighbor_id == entry.id:
                continue
            bounded = max(0.0, min(1.0, float(distance)))
            existing = index[entry.id].get(neighbor_id)
            if existing is None or bounded < existing:
                index[entry.id][neighbor_id] = bounded
            reverse = index[neighbor_id].get(entry.id)
            if reverse is None or bounded < reverse:
                index[neighbor_id][entry.id] = bounded
    return index


# Lineage identifies WHICH taxonomy this artifact is, independent of its
# version number. The bundled artifact now ships the "canonical" lineage
# (Cloud's full superset — see docs/taxonomy-artifact.md). The handshake
# compares (lineage, version) so a client never mistakes two different
# lineages that share a version number for being aligned. Legacy bare-array
# files (no envelope) predate lineage and report this default.
DEFAULT_LINEAGE = "local-curated"


def _load_raw() -> Any:
    """Read the packaged taxonomy.json (array or versioned envelope)."""
    resource = files("traitprint.data").joinpath("taxonomy.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _entries_from_raw(raw: Any) -> list[TaxonomyEntry]:
    # Accept both the legacy bare-array layout and the versioned envelope
    # {"version": int, "lineage": str, "skills": [...]} — additive, so a
    # pre-envelope file (or an older reader) still works.
    entries = raw["skills"] if isinstance(raw, dict) else raw
    return [TaxonomyEntry.model_validate(e) for e in entries]


def _version_from_raw(raw: Any) -> int:
    # Legacy bare-array files carry no version → 0 (unversioned).
    return int(raw["version"]) if isinstance(raw, dict) else 0


def _lineage_from_raw(raw: Any) -> str:
    # Lineage is the artifact's own field — the single source of truth, so the
    # reported lineage can never drift from the bundled content.
    if isinstance(raw, dict):
        return str(raw.get("lineage", DEFAULT_LINEAGE))
    return DEFAULT_LINEAGE


def load_taxonomy() -> list[TaxonomyEntry]:
    """Load the embedded taxonomy packaged with traitprint."""
    return _entries_from_raw(_load_raw())


def load_taxonomy_version() -> int:
    """Monotonic version of the embedded taxonomy artifact.

    Bumped whenever the bundled ``taxonomy.json`` content changes, so a client
    can compare it against a server's reported version (the MCP handshake —
    see ``docs/taxonomy-artifact.md``) and refresh when behind. Legacy
    bare-array files report ``0``.
    """
    return _version_from_raw(_load_raw())


def load_taxonomy_lineage() -> str:
    """Lineage of the embedded taxonomy artifact (read from the artifact)."""
    return _lineage_from_raw(_load_raw())


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
