# Dispute schema v1 (canonical)

Single source of truth for the `dispute` object emitted by **both** Traitprint
MCP servers:

- **Local** — `traitprint` (this repo), `src/traitprint/mcp_server.py`
- **Cloud** — `traitprint-cloud`, `supabase/functions/mcp-server/index.ts`

The design goal: a consumer **must not be able to tell which server produced a
record except by the `sources` field**. Both servers emit the same `dispute`
shape; they differ only in (a) which `sources` contributed, (b) the optional
cloud-only `file` field, and (c) `since` (one server persists flags, the other
recomputes — see [§ since](#since)).

Introduced at response contract `server_version` **1.1.0**. This replaces the
day-old flat keys (`reasons[]`, `flag_types[]`, `dangling_refs[]`) — they are
**removed**, not aliased (hard cut at 1.1.0).

## The `dispute` object

Every record a tool emits carries a `dispute` field (and a back-compat
`disputed` boolean where `disputed == (dispute !== null)`):

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
  "file": "experiences/903392e5.json"                           // OPTIONAL, cloud-only; omitted when unknown
}
```

- `flags` — one entry per detected condition, never empty when `dispute !== null`.
- `sources` — the origin engines that produced the flags, de-duplicated and
  sorted. Local always emits `["local-referential-integrity"]`; cloud always
  emits `["trust-layer"]`. The array (rather than a scalar) lets a future server
  that runs both engines represent a record flagged by both.
- `reason` — `flags[].reason` joined with `"; "`. Kept so string-only consumers
  of the pre-1.1.0 top-level `reason` keep working.
- `since` — the earliest timestamp among the contributing flags.
- `file` — vault file the record was ingested from. Cloud-only (the local
  server reads an in-memory vault and has no per-record file); **omitted**, not
  null, when unknown.

`disputed` (boolean) is retained for backward compatibility and MUST equal
`dispute !== null`.

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
  "id": "<uuid>",           // the unresolved UUID
  "target": "skill"         // "skill" | "experience" | "story" — what the field references
}
```

`reason` format: `dangling reference: skill_ids[2] does not resolve` (scalar
fields render without the `[index]`, e.g. `experience_id does not resolve`).

| Produced by | From |
|---|---|
| Local | the in-memory vault (mirrors `src/traitprint/audit.py`) |
| Cloud | ingest `quarantined[]` rows (`{entity_id, file, reason}`); sets `file` |

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
open end date (`""`/null) is treated as `present`. Two ranges `[aStart, aEnd]`
and `[bStart, bEnd]` overlap iff:

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

Entities are ordered `(kind, label, entity_id)` for deterministic output.

## <a name="since"></a>Cross-server differences (intentional)

The golden-fixture acceptance (`docs/schema/dispute-v1/` fixtures in both repos)
diffs the two servers' `dispute` output and tolerates exactly these fields:

- `sources` — names the producing engine; this is the *intended* distinguisher.
- `file` — cloud-only; the local server omits it.
- `since` — cloud persists flag rows (timestamp = scan time); local recomputes
  (timestamp = the entity's `updated_at`). The two cannot agree, by design.

Everything else — `flags[].type`, `flags[].reason`, `flags[].detail`, the
derived `reason`, and the `disputes` roll-up `count`/`entities` (modulo the same
fields) — must be byte-identical for records both servers can evaluate.

## Version history

- **1.1.0** — canonical `flags[]`/`sources[]` shape; `date_overlap` contradiction
  flags (month-granular, strict, symmetric); `disputes` roll-up on both servers;
  removed the flat `reasons[]`/`flag_types[]`/`dangling_refs[]` keys.
- **1.0.x** — per-server divergent shapes (cloud `reasons[]`/`flag_types[]`,
  local `dangling_refs[]`); superseded.
