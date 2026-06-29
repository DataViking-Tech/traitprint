# Dispute schema v1 (canonical)

Single source of truth for the `dispute` object emitted by **both** Traitprint
MCP servers:

- **Local** — `traitprint` (this repo), `src/traitprint/mcp_server.py`
- **Cloud** — `traitprint-cloud`, `supabase/functions/mcp-server/index.ts`

The design goal: a consumer **must not be able to tell which server produced a
record except by the `sources` field**. Both servers emit the same `dispute`
shape; they differ only in (a) which `sources` contributed, (b) `since` (one
server persists flags, the other recomputes — see [§ since](#since)), (c) the
dangling `detail.id` (the hosted server quarantines the unresolved id at ingest
and emits `null`), and (d) the optional `file` field (reserved; currently
emitted by neither server). The full tolerated set is enumerated in
[§ Cross-server differences](#since).

Introduced at response contract `server_version` **1.1.0**. This replaces the
day-old flat keys (`reasons[]`, `flag_types[]`, `dangling_refs[]`) — they are
**removed**, not aliased (hard cut at 1.1.0).

## The `dispute` object

Every record a tool emits carries a `dispute` field (and a back-compat
`disputed` boolean where `disputed == (dispute !== null)`, with one hosted-server
scope exception noted [below](#disputed-scope)):

```jsonc
"dispute": null | {
  "flags": [
    {
      "type": "dangling_reference" | "contradiction" | "<registered type>",
      "reason": "human-readable single-flag explanation",
      "detail": { /* type-specific, see the registry below */ }
    }
  ],
  "sources": ["local-referential-integrity" | "trust-layer"],  // de-duped origins that contributed flags
  "reason": "flags[].reason joined with '; '",                  // derived convenience string
  "since": "2026-06-28T07:26:12Z",                              // ISO-8601, earliest contributing flag
  "file": "experiences/903392e5.json"                           // OPTIONAL, reserved; currently omitted by both servers
}
```

- `flags` — one entry per detected condition, never empty when `dispute !== null`.
  Emitted in a **canonical order** both servers reproduce from shared data
  (independent of discovery / DB-row order): `dangling_reference` first (by field
  — `skill_ids` < `experience_id` < `evidence_story_ids` — then array index),
  then `contradiction` (`date_overlap` before any other subtype, ordered by the
  partner entity id). This keeps `flags[]` and the derived `reason`
  byte-identical across servers.
- `sources` — the origin engines that produced the flags, de-duplicated and
  sorted. Local always emits `["local-referential-integrity"]`; cloud always
  emits `["trust-layer"]`. The array (rather than a scalar) lets a future server
  that runs both engines represent a record flagged by both.
- `reason` — `flags[].reason` joined with `"; "`. Kept so string-only consumers
  of the pre-1.1.0 top-level `reason` keep working.
- `since` — the earliest timestamp among the contributing flags.
- `file` — vault file the record was ingested from. **Optional and reserved.**
  The local server reads an in-memory vault and has no per-record file; the
  hosted server's MCP reshape layer does not have it either (the originating
  file is not carried into the trust-layer flag rows). So **both servers
  currently omit it** — it is **omitted**, not null, when unknown. Reserved so a
  future server that tracks per-record provenance can populate it.

<a name="disputed-scope"></a>
`disputed` (boolean) is retained for backward compatibility. It equals
`dispute !== null` **except** on the hosted server under a scope-limited
credential: dispute *detail* is gated on `read:profile` (a reason recomputed
from a cross-section scan can encode facts from a section the credential cannot
read), so a section-only grant — e.g. `read:skills` — still sees
`disputed: true` but receives `dispute: null`. The local server has no scopes
and always emits the detail, so the equality always holds there.

## Flag taxonomy registry

Each `type` fixes the shape of its `detail`. Adding a flag type = one entry
here + each server populates it if it has the data.

### `dangling_reference`

An entity holds a UUID cross-reference that does not resolve to a vault entity
(Decision D10 / Layer-1 referential integrity).

```jsonc
"detail": {
  "field": "skill_ids",     // the holding field
  "index": 2,               // array index, or null for a scalar field (experience_id)
  "id": "<uuid>",           // the unresolved UUID; null on the hosted server (quarantined at ingest, not persisted)
  "target": "skill"         // "skill" | "experience" | "story" — what the field references
}
```

`reason` format: `dangling reference: skill_ids[2] does not resolve` (scalar
fields render without the `[index]`, e.g. `experience_id does not resolve`).

| Produced by | From |
|---|---|
| Local | the in-memory vault (mirrors `src/traitprint/audit.py`) |
| Cloud | persisted `trait_flags` (vault-git dangling rows); the MCP layer parses `field`/`index` from the reason text and maps `field`→`target`. The unresolved `id` is quarantined at ingest and not persisted, so the hosted server emits `id: null` and does not set `file`. |

### `contradiction`

Two facts cannot both hold. The first (and currently only) subtype is
`date_overlap`.

```jsonc
"detail": {
  "kind": "date_overlap",
  "entities": ["<this id>", "<other id>"],   // this record's entity first
  "ranges": [["2021-10", "2023-04"], ["2021-09", "2023-01"]]  // aligned with entities; open end -> "present"
}
```

`reason` format: `These two roles overlap in time (2021-10 to 2023-04 and 2021-09 to 2023-01). An interviewer might notice if both were full-time positions.`

**Symmetric:** an overlapping pair flags **both** experiences (each record's own
entity is listed first in `entities`/`ranges`). This keeps output independent of
iteration order, so the two servers agree regardless of how they enumerate
experiences.

**Overlap rule (date_overlap):** dates are compared at **month granularity**
(`YYYY-MM`; a bare `YYYY` is treated as January, a day component is ignored). An
open end date (`""`/null) is treated as `present` — a boundary **strictly after
the current month** (current month + 1), so two ongoing roles still overlap and a
role started in the current month keeps positive width under the strict
comparison. Two ranges `[aStart, aEnd]` and `[bStart, bEnd]` overlap iff:

```
aStart < bEnd  AND  bStart < aEnd      // strict
```

The comparison is **strict**, so a shared boundary month (one role's `end`
equals the next role's `start`) is **adjacency, not overlap** — back-to-back
internal promotions are not flagged. A pair is only flagged when **neither**
title/description matches the part-time pattern
`\b(intern|part[\s-]?time|freelance|contract|consulting|volunteer|advisor)\b`
(case-insensitive).

| Produced by | From |
|---|---|
| Local | experiences in the in-memory vault (dates are local) |
| Cloud | the trust-layer contradiction scanner (`contradiction-scanner`); MCP layer symmetrizes the persisted rows |

## `disputes` roll-up (`get_profile_summary`)

`get_profile_summary` additionally returns a vault-wide inventory, mirroring the
`sections` inventory style:

```jsonc
"disputes": {
  "count": 2,
  "sources": ["local-referential-integrity"],   // de-duped across all entities
  "entities": [
    {
      "entity_id": "903392e5-…",
      "kind": "experience",                       // skill | experience | story | philosophy
      "label": "Data Engineer",
      "flag_types": ["contradiction"],            // de-duped flag types on this entity
      "reason": "These two roles overlap in time (…)…"   // the entity's dispute.reason
    }
  ]
}
```

Entities are ordered `(kind, label, entity_id)` for deterministic output. The
hosted server serves this **same** roll-up shape from its
`traitprint://profile/disputes` MCP resource, so a consumer parses one dispute
contract everywhere (per-record `dispute`, the `get_profile_summary` roll-up,
and the resource).

## <a name="since"></a>Cross-server differences (intentional)

The golden-fixture acceptance (`docs/schema/dispute-v1/` fixtures in both repos)
diffs the two servers' `dispute` output and tolerates exactly these fields:

- `sources` — names the producing engine; this is the *intended* distinguisher.
- `since` — cloud persists flag rows (timestamp = scan time); local recomputes
  (timestamp = the entity's `updated_at`). The two cannot agree, by design.
- dangling `detail.id` — the hosted server quarantines the unresolved id at
  ingest and does not persist it, so it emits `null` where the local server
  emits the offending UUID. Every other dangling `detail` field — `field`,
  `index`, `target` — is identical.
- `file` — optional / reserved; **currently omitted by both servers** (see the
  `file` note above). Tolerated only so a future server that begins to populate
  it does not break the diff.

Everything else — `flags[].type`, `flags[].reason`, `flags[].detail` (except the
dangling `id` noted above), the derived `reason`, the canonical `flags[]` order,
and the `disputes` roll-up `count`/`entities` (modulo the same fields) — must be
byte-identical for records both servers can evaluate.

## Version history

- **1.1.0** — canonical `flags[]`/`sources[]` shape; `date_overlap` contradiction
  flags (month-granular, strict, symmetric); `disputes` roll-up on both servers;
  removed the flat `reasons[]`/`flag_types[]`/`dangling_refs[]` keys.
- **1.0.x** — per-server divergent shapes (cloud `reasons[]`/`flag_types[]`,
  local `dangling_refs[]`); superseded.
