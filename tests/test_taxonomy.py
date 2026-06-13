"""Tests for the embedded taxonomy loader and its versioned envelope."""

from __future__ import annotations

from traitprint.taxonomy import (
    DEFAULT_LINEAGE,
    _entries_from_raw,
    _lineage_from_raw,
    _version_from_raw,
    build_neighbor_index,
    find_exact,
    load_taxonomy,
    load_taxonomy_lineage,
    load_taxonomy_version,
    taxonomy_update_advisory,
)


def test_packaged_taxonomy_is_canonical_v2() -> None:
    entries = load_taxonomy()
    # The shipped artifact is now Cloud's full superset (canonical lineage).
    assert len(entries) > 500
    assert load_taxonomy_version() == 2
    assert load_taxonomy_lineage() == "canonical"


def test_no_alias_shadows_or_collisions() -> None:
    # Alias hygiene: every alias resolves to exactly one skill and never
    # shadows a real skill's canonical name (the union previously imported
    # collisions like "Data Science"/"ETL" as aliases of other skills).
    tax = load_taxonomy()
    names_lower = {e.name.lower() for e in tax}
    assigned: dict[str, set[str]] = {}
    for e in tax:
        for a in e.aliases:
            assigned.setdefault(a.lower(), set()).add(e.name)
    shadows = [a for a in assigned if a in names_lower]
    multi = {a: s for a, s in assigned.items() if len(s) > 1}
    assert shadows == [], f"shadowing aliases: {shadows[:5]}"
    assert multi == {}, f"multi-mapped aliases: {dict(list(multi.items())[:5])}"


def test_update_advisory() -> None:
    local_v = load_taxonomy_version()
    local_lin = load_taxonomy_lineage()
    # Same lineage, server ahead → advisory.
    assert taxonomy_update_advisory(local_v + 1, local_lin) is not None
    # Same lineage, current/behind → no advisory.
    assert taxonomy_update_advisory(local_v, local_lin) is None
    assert taxonomy_update_advisory(local_v - 1, local_lin) is None
    # Different lineage → "different taxonomy" note regardless of version.
    msg = taxonomy_update_advisory(local_v + 5, "cloud-onet")
    assert msg is not None and "different" in msg.lower()


def test_packaged_taxonomy_neighbors_resolve() -> None:
    tax = load_taxonomy()
    # A handful of well-known names exist and the neighbor index builds.
    for name in ["Python", "JavaScript", "Leadership", "Amazon Web Services"]:
        assert find_exact(name, tax) is not None, name
    idx = build_neighbor_index(tax)
    assert any(neighbors for neighbors in idx.values())


def test_envelope_layout_is_parsed() -> None:
    raw = {
        "version": 7,
        "lineage": "canonical",
        "skills": [
            {
                "id": "a1b2c3d4-0001-4000-8000-000000000001",
                "name": "Python",
                "category": "technical",
            },
        ],
    }
    assert _version_from_raw(raw) == 7
    assert _lineage_from_raw(raw) == "canonical"
    entries = _entries_from_raw(raw)
    assert len(entries) == 1
    assert entries[0].name == "Python"


def test_legacy_bare_array_is_still_accepted() -> None:
    # Backward compat: a pre-envelope taxonomy.json (bare array) loads, and
    # reports version 0 + the default lineage rather than raising.
    raw = [
        {
            "id": "a1b2c3d4-0001-4000-8000-000000000001",
            "name": "Python",
            "category": "technical",
        },
    ]
    assert _version_from_raw(raw) == 0
    assert _lineage_from_raw(raw) == DEFAULT_LINEAGE
    entries = _entries_from_raw(raw)
    assert len(entries) == 1
    assert entries[0].name == "Python"
