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
)


def test_packaged_taxonomy_is_canonical_v2() -> None:
    entries = load_taxonomy()
    # The shipped artifact is now Cloud's full superset (canonical lineage).
    assert len(entries) > 500
    assert load_taxonomy_version() == 2
    assert load_taxonomy_lineage() == "canonical"


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
