"""Pin checks for the vendored career-ops interop fixtures.

These tests validate the *fixtures themselves* (parseable, expected
contract markers present, license preserved) so a bad re-vendor fails
fast. The exporter round-trip and importer conformance classes live
with the exporter (#62) and importer (#63) test suites respectively —
this module must not import either feature.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

PINNED_TAG = "career-ops-v1.16.0"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "careerops" / PINNED_TAG


class TestVendoredFixtures:
    def test_all_contract_files_vendored(self) -> None:
        for rel in (
            "LICENSE",
            "match-star.mjs",
            "config/profile.example.yml",
            "templates/states.yml",
        ):
            assert (FIXTURES / rel).is_file(), f"missing vendored {rel}"

    def test_upstream_license_preserved_verbatim(self) -> None:
        text = (FIXTURES / "LICENSE").read_text(encoding="utf-8")
        assert text.startswith("MIT License")
        assert "Copyright (c)" in text

    def test_profile_example_parses_with_expected_structure(self) -> None:
        doc = yaml.safe_load(
            (FIXTURES / "config" / "profile.example.yml").read_text(encoding="utf-8")
        )
        assert isinstance(doc, dict)
        # The keys our exporter emits / importer maps must exist upstream.
        assert "candidate" in doc
        assert "narrative" in doc
        assert "target_roles" in doc

    def test_states_template_parses(self) -> None:
        doc = yaml.safe_load(
            (FIXTURES / "templates" / "states.yml").read_text(encoding="utf-8")
        )
        assert isinstance(doc, dict)

    def test_match_star_still_parses_star_blocks(self) -> None:
        """The regex family our importer mirrors must still be present:
        '###' story headings and the bold STAR field-line convention."""
        source = (FIXTURES / "match-star.mjs").read_text(encoding="utf-8")
        assert "story-bank.md" in source
        assert re.search(r"###", source), "story heading marker gone"
        for marker in ("Situation", "Task", "Action", "Result"):
            assert marker in source, f"STAR field {marker!r} gone from matcher"
