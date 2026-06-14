"""Embedded taxonomy loader and search."""

from __future__ import annotations

import json
import os
from importlib.resources import files
from pathlib import Path
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


#: Env override for the downloaded-taxonomy cache location (used by tests and
#: anyone wanting a non-default path). Defaults to ``~/.traitprint/taxonomy.json``.
_CACHE_ENV = "TRAITPRINT_TAXONOMY_CACHE"


def taxonomy_cache_path() -> Path:
    """Location of the downloaded taxonomy cache.

    The cache is written by ``traitprint sync taxonomy`` (see
    :func:`write_taxonomy_cache`) and supersedes the bundled artifact when it is
    a strictly newer version of the SAME lineage — so a client can refresh the
    skill taxonomy without a pip upgrade.
    """
    override = os.environ.get(_CACHE_ENV)
    if override:
        return Path(override)
    return Path.home() / ".traitprint" / "taxonomy.json"


def _load_bundled_raw() -> Any:
    """Read the taxonomy.json packaged in the wheel (array or envelope)."""
    resource = files("traitprint.data").joinpath("taxonomy.json")
    return json.loads(resource.read_text(encoding="utf-8"))


def _read_cache_raw() -> Any | None:
    """Read the downloaded cache, or None if absent/unreadable/corrupt."""
    path = taxonomy_cache_path()
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _cache_supersedes(cache: Any, bundled: Any) -> bool:
    # Adopt the cache only for the SAME lineage and a strictly newer version: a
    # different lineage is not "newer" (just different), and an equal/older one
    # adds nothing. This keeps the bundled artifact authoritative for its lineage
    # and means a stale/foreign cache silently falls back to the bundle.
    return _lineage_from_raw(cache) == _lineage_from_raw(bundled) and _version_from_raw(
        cache
    ) > _version_from_raw(bundled)


def _load_raw() -> Any:
    """Taxonomy source: the downloaded cache when it supersedes the bundle, else
    the packaged artifact."""
    bundled = _load_bundled_raw()
    cache = _read_cache_raw()
    if cache is not None and _cache_supersedes(cache, bundled):
        return cache
    return bundled


def write_taxonomy_cache(raw: Any) -> Path:
    """Validate a downloaded taxonomy envelope and persist it to the cache.

    Validation happens BEFORE the write (every entry is parsed, and the
    version/lineage are read), so a malformed download can never be cached and
    shadow the bundled artifact. Returns the cache path. Raises ``ValueError`` on
    an envelope that is not a versioned ``{version, lineage, skills}`` object.
    """
    if not isinstance(raw, dict) or "skills" not in raw or "version" not in raw:
        raise ValueError(
            "taxonomy artifact must be a {version, lineage, skills} envelope"
        )
    # Parse every entry + the version/lineage; raises on malformed content.
    _entries_from_raw(raw)
    _version_from_raw(raw)
    _lineage_from_raw(raw)
    path = taxonomy_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    return path


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


def taxonomy_update_advisory(server_version: int, server_lineage: str) -> str | None:
    """Advisory string when the server's taxonomy is ahead of the bundled one.

    The handshake compares the (lineage, version) pair: an advisory is returned
    only when the lineages MATCH and the server's version is strictly greater —
    a different lineage is not "newer", just different, and says so. Returns
    None when the bundle is current (or ahead, or on a different lineage).
    """
    local_version = load_taxonomy_version()
    local_lineage = load_taxonomy_lineage()
    if server_lineage != local_lineage:
        return (
            f"Server taxonomy lineage is {server_lineage!r}, but this build "
            f"bundles {local_lineage!r} — different taxonomies, not version-comparable."
        )
    if server_version > local_version:
        return (
            f"Local taxonomy is {local_lineage} v{local_version}; the server has "
            f"v{server_version}. Run 'traitprint sync taxonomy' to refresh it."
        )
    return None


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
