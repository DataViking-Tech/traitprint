# Versioned shared taxonomy artifact

Status: **canonical adoption shipped.** Local now bundles Cloud's full
superset as the `canonical` lineage (v2), generated reproducibly by
`scripts/build_canonical_taxonomy.py` from the vendored source dumps in
`scripts/data/`. The maintainer decision in [§6](#6-the-decision-made) was
"adopt full superset". Remaining follow-ups in [§7](#7-adoption-status).

`traitprint` (this repo, MIT, local-first) is the canonical owner of the skill
taxonomy. Cloud (`traitprint-cloud`) maintains its own taxonomy today; the goal
is for both products to converge on **one versioned artifact** so skill names,
aliases, categories, and relationship edges stay in sync, with drift made
observable rather than silent.

This doc specifies the artifact format and the distribution/handshake
mechanism (both partly shipped), and lays out the content reconciliation that
must NOT land without a maintainer signing off on the resulting skill set.

## 1. Why

Skill references cross the Local↔Cloud boundary **by name** (a skill is
resolved to each product's taxonomy at `add_skill` time via name/alias), so the
two products do not need to share UUIDs — but they DO need to agree on names,
aliases, and edges, or matching and alias resolution drift apart. Today they
don't agree, and nothing detects it.

## 2. Current state (measured 2026-06-13)

| | Local (`traitprint`) | Cloud (`traitprint-cloud`) |
|---|---|---|
| Skills | 26 (curated) | 618 (full O*NET + tech) |
| Source | `src/traitprint/data/taxonomy.json` | `scripts/seed-skill-taxonomy.ts` → `skill_taxonomy` |
| IDs | fixed UUIDs in JSON | DB-assigned; `onet_element_id` is the stable seed key |
| Categories | 4 (`technical/soft/domain/tool`) | same 4 + 51 subcategories |
| Edges | `neighbors` (name→distance, symmetric, ~50) | `skill_relationships` (typed + weighted, 525) |
| Aliases | per-entry `aliases[]` | `skill_aliases` table (+ `source`) |
| Version | **was none** — now `version`/`lineage` envelope (this work) | none (hook identified) |

Name overlap: **24 shared names**; Local is ~92% covered by Cloud. Local-only:
`AWS` (Cloud: `Amazon Web Services`), `Vue` (Cloud: `Vue.js`) — two naming
mismatches to reconcile. Cloud is effectively a superset.

## 3. Artifact format

The canonical artifact is a versioned envelope (Local's `taxonomy.json` now
uses it; the loader accepts the legacy bare array too, reporting version `0`):

```jsonc
{
  "version": 1,              // monotonic int; bump on any content change
  "lineage": "local-curated", // which taxonomy this is (see §4)
  "skills": [
    {
      "id": "<uuid>",        // stable per lineage; never reused (tombstone on removal)
      "name": "Python",
      "category": "technical",
      "subcategory": "Programming Languages", // optional (Cloud carries it)
      "aliases": ["py", "cpython"],
      "neighbors": { "Machine Learning": 0.4 } // name→distance
    }
  ]
}
```

`onet_element_id` is carried when present (Cloud's stable seed key). The
canonical lineage will also carry the typed/weighted edge set; Local derives
its symmetric `neighbors` (distance = `1 - weight`, min-wins) from it.

## 4. Versioning & lineage

- **version**: monotonic integer, bumped whenever the artifact content changes.
- **lineage**: a string identifying *which* taxonomy this is. Pre-unification,
  Local ships `local-curated` and Cloud ships `cloud-onet`; both start at
  version 1 but are **not the same content**. The handshake compares the
  **pair `(lineage, version)`** so a client never mistakes two different
  lineages that share a version number for being aligned. Post-unification both
  converge to `lineage: "canonical"` and a shared version line.
- **IDs are additive and tombstoned**: a skill id is never reused; a removed
  skill is tombstoned, never re-pointed. This makes a stale-but-recent client a
  strict subset (benign), never in conflict.

## 5. Distribution (hybrid) & handshake

Reference data changes on the order of weeks, not per-request, so the model is
**bundle + cheap version handshake + opportunistic refresh** — not live-per-check
(which would break Local's offline/MIT promise) and not bundle-and-forget.

- **Local** bundles the artifact (already does) and exposes its version via
  `taxonomy.load_taxonomy_version()` + `TAXONOMY_LINEAGE` (shipped here). It
  polls the server's reported version opportunistically (e.g. on sync) and
  refreshes when behind. Never a per-skill server hit.
- **Cloud / hosted MCP** serves its taxonomy version live in the per-response
  `meta` block (`mcp-server` `makeMeta()`): `meta.taxonomy = { version, lineage }`
  (shipped on the Cloud side). Thin/online agents can also read the taxonomy
  live; bundled and live serve the same version.
- Both consume the **same** artifact once unified, so live == bundled for a
  given version.

The handshake is O(1) and cacheable; the artifact is a static asset fetched
only on a bump — strictly cheaper than live lookups, and offline survives.

## 6. The decision (made)

The maintainer chose **adopt full superset**: Local ships Cloud's entire
taxonomy as the `canonical` lineage. (UUID preservation was waived — there are
no meaningful existing local-user vaults to keep back-compatible — so canonical
ids are fresh deterministic `uuid5(name)`, stable across regenerations.)

Considered and not chosen: *stay curated* (Local as a named subset view) and
*middle tier*. Trade-off accepted: the bundled package grew 26 → 1083 skills
and the curated CLI character is replaced by full O*NET + tech coverage.

## 7. Adoption status

1. **[shipped]** Versioned envelope + lineage on Local; `load_taxonomy_version()`
   / `load_taxonomy_lineage()`.
2. **[shipped, Cloud]** `meta.taxonomy = { version, lineage }` in the hosted MCP
   handshake.
3. **[shipped]** `scripts/build_canonical_taxonomy.py` generates the canonical
   `taxonomy.json` (1083 skills, lineage `canonical`, v2) from the vendored
   source dumps (`scripts/data/canonical-source-*.json`, pulled from the live
   Cloud tables). Deterministic ids; neighbors derived from Cloud's weighted
   relationships (distance = 1 − weight); Local's curated aliases unioned in so
   adopting the superset doesn't lose synonyms (`py`, `python3`, …).
4. **[shipped]** Local bundles canonical v2; Cloud's MCP handshake reports
   lineage `canonical` v2. Both lineages now match.

### Remaining follow-ups (not blocking)

- **Cloud sources from the artifact**: Cloud's `seed-skill-taxonomy.ts` is still
  the hand-authored origin the artifact was derived *from*; ideally Cloud
  regenerates its seed *from* the canonical artifact so traitprint is the true
  single source. Until then a drift-detection check (regenerate from a fresh
  Cloud dump, diff against the committed artifact) keeps them honest.
- **Cloud alias backfill**: the canonical artifact carries Local's unioned
  curated aliases for the overlap; Cloud's `skill_aliases` is sparser. Backfill
  for full parity.
- **Local opportunistic-refresh client**: poll the handshake on sync, fetch a
  newer canonical artifact when behind.
