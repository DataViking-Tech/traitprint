"""Tests for the embedded taxonomy loader and its versioned envelope."""

from __future__ import annotations

from traitprint.taxonomy import (
    TAXONOMY_LINEAGE,
    _entries_from_raw,
    _version_from_raw,
    load_taxonomy,
    load_taxonomy_version,
)


def test_packaged_taxonomy_loads_with_version() -> None:
    entries = load_taxonomy()
    assert len(entries) > 0
    # The shipped artifact is the versioned envelope, lineage local-curated.
    assert load_taxonomy_version() == 1
    assert TAXONOMY_LINEAGE == "local-curated"


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
    entries = _entries_from_raw(raw)
    assert len(entries) == 1
    assert entries[0].name == "Python"


def test_legacy_bare_array_is_still_accepted() -> None:
    # Backward compat: a pre-envelope taxonomy.json (bare array) loads, and
    # reports version 0 (unversioned) rather than raising.
    raw = [
        {
            "id": "a1b2c3d4-0001-4000-8000-000000000001",
            "name": "Python",
            "category": "technical",
        },
    ]
    assert _version_from_raw(raw) == 0
    entries = _entries_from_raw(raw)
    assert len(entries) == 1
    assert entries[0].name == "Python"
