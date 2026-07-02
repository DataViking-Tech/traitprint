# Lens schema v1 (canonical)

Single source of truth for the **positioning lens** — a named, non-destructive
projection over the one vault, emitted by **both** Traitprint MCP servers:

- **Local** — `traitprint` (this repo), `src/traitprint/mcp_server.py`
- **Cloud** — `traitprint-cloud`, `supabase/functions/mcp-server/index.ts`

A single vault renders a single canonical profile, but a person legitimately
pursues multiple tracks that demand different emphasis (e.g. an IC architecture
track and a people-leadership track). A lens **selects, orders, and re-weights**
what leads — and drives per-direction scoring — **without ever contradicting the
underlying facts**. That integrity guarantee (§ Trust layer) is the whole point:
several honest positionings, provably backed by one auditable vault, versus two
contradictory Word résumés.

## The `lens` object

```jsonc
{
  "id": "uuid",
  "slug": "ic-data-architecture",          // lowercase kebab-case, unique per vault
  "name": "IC / Data Architecture",
  "target_archetypes": ["staff_data_engineer", "data_architect"],  // empirical archetypes (v1+: jobs index)
  "headline_override": "Staff/Principal Data & Platform Engineer",  // optional; no new factual claim
  "bio_override": "…optional; must not assert facts absent from the vault…",
  "signature_experience_ids": ["…ordered subset of vault experiences…"],
  "signature_story_ids": ["…ordered subset of vault stories…"],
  "skill_salience": {                       // skill_id -> salience; unspecified = "supporting"
    "<skill_id Data Architecture>": "core",
    "<skill_id Product Management>": "suppressed"
  },
  "is_default": false,                      // at most one default; the bare profile renders it
  "created_at": "…", "updated_at": "…"
}
```

- A vault may hold multiple lenses (cap: **5**). Storage: a `lenses.json` array
  in the v1 file tree (alongside `skills.json` / `education.json`).
- **Omitting a lens everywhere preserves the canonical rendering** — an un-lensed
  vault is byte-identical to pre-lens output.

## Salience

`skill_salience[skill_id] ∈ {core, supporting, suppressed}` (a 3-level enum for
v1); unspecified skills default to `supporting`.

| Salience | Rendering | Scoring weight |
|---|---|---|
| `core` | foregrounded — top of the skill list | boosted |
| `supporting` | included, normal order | normal |
| `suppressed` | **hidden** from the rendered profile | excluded from the lensed skill set |

`suppressed` is **fully hidden**, not merely down-ranked (a high-proficiency but
off-narrative skill — e.g. Product Management for the IC lens — disappears from
the rendered profile and from related-skill lists; it remains in the vault, an
omission, not a denial).

## Projection (renderers)

`get_profile_summary(depth, lens?)` applies, when a lens is passed **or a default
lens exists**:

- `headline_override` / `bio_override` (only when set — a lens never blanks the
  canonical text);
- the lens's ordered `signature_experience_ids` for the signature section;
- salience ordering on `top_skills` — `core` skills lead, `suppressed` skills are
  dropped. With no lens every skill is `supporting`, so the order reduces to the
  canonical `(-proficiency, -created_at)` sort.

The result carries `"lens": "<slug>"` when a lens was applied (absent otherwise).
Read tools: `vault_lens_list` (inventory + emphasis counts) and `vault_lens_get`
(one lens, references resolved to names).

## Trust layer — lenses are emphasis, never contradiction

A lens may only select/order/weight existing vault content, plus override
`headline`/`bio` with text that introduces **no new factual claim** (no skill,
proficiency, date, employer, or metric absent from the vault). The trust layer
validates each lens and reuses the dispute machinery
(`docs/schema/dispute-v1/`):

- every `signature_experience_id` / `signature_story_id` / `skill_salience` key
  must resolve to a vault entity — a dangling id raises the same
  `dangling_reference` flag class as the dispute work. **Enforced**: a lens with
  an unresolved reference is `disputed`, surfaced on `vault_lens_list` /
  `vault_lens_get` and in the `get_profile_summary` disputes roll-up (kind
  `lens`).
- `headline_override` / `bio_override` must assert nothing unsupported — an
  unsupported assertion raises an `unsupported_claim` flag. **Reserved /
  deferred**: deterministically proving free text introduces no new fact is not
  tractable, so the flag class is reserved and the detector is a follow-up
  (heuristic or BYOK-assisted). When it lands, the flag flows through the same
  dispute path.

A failing lens is `disputed` with a reason, via the identical mechanism that
records record-level disputes.

## Version history

- **1.0.0** — the `lens` object: slug/name, `target_archetypes`,
  headline/bio overrides, ordered signature selections, 3-level
  `skill_salience`, `is_default`. Lens-aware `get_profile_summary` +
  `vault_lens_list` / `vault_lens_get`. Lens-aware `jobs_match` (salience-weighted
  scoring), archetype rubrics, and trust-layer validation land incrementally on
  top of this contract.
