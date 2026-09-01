"""Tests for the embedded taxonomy loader and its versioned envelope."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    taxonomy_cache_path,
    taxonomy_update_advisory,
    write_taxonomy_cache,
)


def _envelope(version: int, lineage: str, skill_name: str = "OnlyInCache") -> dict:
    """A minimal valid taxonomy envelope for cache tests."""
    return {
        "version": version,
        "lineage": lineage,
        "skills": [
            {
                "id": "a1b2c3d4-0001-4000-8000-000000000001",
                "name": skill_name,
                "category": "technical",
            }
        ],
    }


def test_cache_supersedes_bundle_when_newer_same_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled_version = load_taxonomy_version()
    bundled_lineage = load_taxonomy_lineage()
    cache = tmp_path / "tax.json"
    monkeypatch.setenv("TRAITPRINT_TAXONOMY_CACHE", str(cache))
    cache.write_text(
        json.dumps(_envelope(bundled_version + 1, bundled_lineage)), encoding="utf-8"
    )
    # The newer same-lineage cache wins.
    assert load_taxonomy_version() == bundled_version + 1
    assert find_exact("OnlyInCache") is not None


def test_cache_ignored_when_not_newer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled_version = load_taxonomy_version()
    bundled_lineage = load_taxonomy_lineage()
    cache = tmp_path / "tax.json"
    monkeypatch.setenv("TRAITPRINT_TAXONOMY_CACHE", str(cache))
    # Same version → no benefit → bundle stays authoritative.
    cache.write_text(
        json.dumps(_envelope(bundled_version, bundled_lineage)), encoding="utf-8"
    )
    assert load_taxonomy_version() == bundled_version
    assert find_exact("OnlyInCache") is None


def test_cache_ignored_when_different_lineage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled_version = load_taxonomy_version()
    cache = tmp_path / "tax.json"
    monkeypatch.setenv("TRAITPRINT_TAXONOMY_CACHE", str(cache))
    # A far-newer but DIFFERENT lineage is not adopted automatically.
    cache.write_text(
        json.dumps(_envelope(bundled_version + 99, "some-other-lineage")),
        encoding="utf-8",
    )
    assert load_taxonomy_version() == bundled_version
    assert load_taxonomy_lineage() == load_taxonomy_lineage()
    assert find_exact("OnlyInCache") is None


def test_corrupt_cache_falls_back_to_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled_version = load_taxonomy_version()
    cache = tmp_path / "tax.json"
    monkeypatch.setenv("TRAITPRINT_TAXONOMY_CACHE", str(cache))
    cache.write_text("{ not valid json", encoding="utf-8")
    # Unreadable cache never breaks loading — silently falls back to the bundle.
    assert load_taxonomy_version() == bundled_version
    assert len(load_taxonomy()) > 500


@pytest.mark.parametrize(
    "body",
    [
        "{}",  # valid JSON, but not an envelope (no version/skills)
        '{"version": "two", "lineage": "canonical", "skills": []}',  # non-int version
        '[{"id": "x", "name": "Y", "category": "technical"}]',  # bare array
        '{"version": 999, "lineage": "canonical"}',  # missing skills
    ],
)
def test_malformed_envelope_cache_is_a_miss_not_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    # A valid-JSON-but-not-an-envelope cache must NOT crash the version compare;
    # it is treated as a cache miss and loading falls back to the bundle.
    bundled_version = load_taxonomy_version()
    cache = tmp_path / "tax.json"
    monkeypatch.setenv("TRAITPRINT_TAXONOMY_CACHE", str(cache))
    cache.write_text(body, encoding="utf-8")
    assert load_taxonomy_version() == bundled_version
    assert len(load_taxonomy()) > 500


def test_cache_path_is_under_a_cache_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    # No override → the cache lives under an OS cache dir, never the vault.
    monkeypatch.delenv("TRAITPRINT_TAXONOMY_CACHE", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg-cache-home")
    path = taxonomy_cache_path()
    assert path == Path("/tmp/xdg-cache-home/traitprint/taxonomy.json")


def test_write_taxonomy_cache_validates_and_activates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled_version = load_taxonomy_version()
    bundled_lineage = load_taxonomy_lineage()
    cache = tmp_path / "tax.json"
    monkeypatch.setenv("TRAITPRINT_TAXONOMY_CACHE", str(cache))

    written = write_taxonomy_cache(_envelope(bundled_version + 1, bundled_lineage))
    assert written == taxonomy_cache_path() == cache
    # Once written, the loader picks it up.
    assert load_taxonomy_version() == bundled_version + 1
    assert find_exact("OnlyInCache") is not None


def test_write_taxonomy_cache_rejects_non_envelope_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "tax.json"
    monkeypatch.setenv("TRAITPRINT_TAXONOMY_CACHE", str(cache))
    # A bare array (legacy/no envelope) is not an acceptable downloaded artifact.
    with pytest.raises(ValueError):
        write_taxonomy_cache([{"id": "x", "name": "Y", "category": "technical"}])
    # Validation happens BEFORE the write, so nothing was persisted.
    assert not cache.exists()


def test_packaged_taxonomy_is_canonical_v3() -> None:
    entries = load_taxonomy()
    # The shipped artifact is Cloud's full superset (canonical lineage);
    # v3 added the education/academia pack.
    assert len(entries) > 500
    assert load_taxonomy_version() == 3
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
