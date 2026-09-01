#!/usr/bin/env python3
"""Generate the canonical taxonomy artifact (src/traitprint/data/taxonomy.json).

Item 4: Local adopts Cloud's full taxonomy as the canonical lineage. This
script is the reproducible source of the bundled artifact — re-run it after
refreshing the vendored source dumps in scripts/data/ (pulled from the live
Cloud skill_taxonomy / skill_aliases / skill_relationships tables).

Design notes:
- IDs are deterministic uuid5(name) so regeneration is stable and Local and
  Cloud need NOT share UUIDs (interop is by name — see docs/taxonomy-artifact.md).
- Local's `neighbors` (name -> distance in [0,1], smaller = closer) are derived
  from Cloud's typed/weighted relationships as distance = 1 - weight. Edges are
  symmetric / min-wins, which build_neighbor_index already enforces.
- The envelope carries version + lineage "canonical". subcategory /
  onet_element_id ride along for fidelity; TaxonomyEntry ignores them on load.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(__file__).resolve().parent / "data"
OUT = ROOT / "src" / "traitprint" / "data" / "taxonomy.json"

# Bump whenever the artifact content changes (the version handshake).
CANONICAL_VERSION = 3
CANONICAL_LINEAGE = "canonical"
# Stable namespace for deterministic skill ids (uuid5 over the lowercased name).
TP_NS = uuid5(NAMESPACE_URL, "https://traitprint.dev/taxonomy")


def skill_id(name: str) -> str:
    return str(uuid5(TP_NS, name.strip().lower()))


def main() -> None:
    skills = json.loads((SRC / "canonical-source-skills.json").read_text())
    rels = json.loads((SRC / "canonical-source-relationships.json").read_text())
    # Local's pre-adoption curated aliases (richer than Cloud's for the
    # overlap, e.g. Python's py/python3/cpython). Unioned onto canonical so
    # adopting the superset doesn't lose curated synonyms. Keyed by canonical
    # name (AWS/Vue already remapped to Amazon Web Services / Vue.js).
    overlay_path = SRC / "local-curated-aliases.json"
    alias_overlay: dict[str, list[str]] = (
        json.loads(overlay_path.read_text()) if overlay_path.exists() else {}
    )

    names = {s["name"] for s in skills}

    # Build name -> {neighbor_name: distance}, symmetric + min-wins.
    neighbors: dict[str, dict[str, float]] = {s["name"]: {} for s in skills}

    def add_edge(a: str, b: str, dist: float) -> None:
        if a == b or a not in neighbors or b not in names:
            return
        cur = neighbors[a].get(b)
        if cur is None or dist < cur:
            neighbors[a][b] = dist

    skipped = 0
    for r in rels:
        a, b = r["from_name"], r["to_name"]
        if a not in names or b not in names:
            skipped += 1
            continue
        weight = float(r.get("weight") or 1.0)
        dist = round(max(0.0, min(1.0, 1.0 - weight)), 3)
        add_edge(a, b, dist)
        add_edge(b, a, dist)  # symmetric

    out_skills = []
    for s in sorted(skills, key=lambda x: x["name"].lower()):
        entry: dict[str, Any] = {
            "id": skill_id(s["name"]),
            "name": s["name"],
            "category": s["category"],
        }
        if s.get("subcategory"):
            entry["subcategory"] = s["subcategory"]
        if s.get("onet_element_id"):
            entry["onet_element_id"] = s["onet_element_id"]
        # Union Cloud's aliases with Local's curated overlay (case-insensitive
        # dedup, stable order: Cloud first, then any new curated ones).
        aliases: list[str] = list(s.get("aliases") or [])
        seen = {a.lower() for a in aliases}
        for a in alias_overlay.get(s["name"], []):
            if a.lower() not in seen and a.lower() != s["name"].lower():
                aliases.append(a)
                seen.add(a.lower())
        if aliases:
            entry["aliases"] = aliases
        nbrs = neighbors.get(s["name"]) or {}
        if nbrs:
            entry["neighbors"] = dict(sorted(nbrs.items()))
        out_skills.append(entry)

    # Alias hygiene (global pass): an alias must unambiguously resolve to ONE
    # skill and must not shadow a real skill's canonical name. The union of
    # Cloud's aliases with Local's curated overlay introduced collisions —
    # e.g. "Data Science"/"ETL"/"Web Development" are real canonical skills
    # yet were also pasted on as aliases of Machine Learning / Data Engineering
    # / JavaScript, and "backend"/"frontend" mapped to two skills each. Drop
    # any alias that (a) equals a canonical skill name, or (b) is assigned to
    # more than one skill. (Own-name and exact dupes were already excluded.)
    names_lower = {e["name"].lower() for e in out_skills}
    assigned: dict[str, set[str]] = {}
    for e in out_skills:
        for a in e.get("aliases", []):
            assigned.setdefault(a.lower(), set()).add(e["name"])
    dropped = 0
    for e in out_skills:
        if "aliases" not in e:
            continue
        kept = [
            a for a in e["aliases"]
            if a.lower() not in names_lower and len(assigned[a.lower()]) == 1
        ]
        dropped += len(e["aliases"]) - len(kept)
        if kept:
            e["aliases"] = kept
        else:
            del e["aliases"]

    envelope = {
        "version": CANONICAL_VERSION,
        "lineage": CANONICAL_LINEAGE,
        "skills": out_skills,
    }
    OUT.write_text(json.dumps(envelope, indent=2, ensure_ascii=False) + "\n")

    edge_count = sum(len(v) for v in neighbors.values())
    alias_count = sum(len(e.get("aliases", [])) for e in out_skills)
    print(f"wrote {len(out_skills)} skills, {alias_count} aliases "
          f"({dropped} ambiguous/shadowing aliases dropped), {edge_count} directed "
          f"edges ({skipped} relationship rows skipped as unresolved) -> {OUT}")


if __name__ == "__main__":
    main()
