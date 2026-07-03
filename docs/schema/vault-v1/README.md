# Vault v1 — File Format Contract

**Status:** Stable contract (Phase 0 of the
[agent-native architecture](../../agent-native-architecture.md))
**Contract revision:** 1.3 (additive over 1.2 — see [Versioning](#versioning))
**Consumers:** the `traitprint` CLI/MCP server (read+write) and the
traitprint-cloud ingest pipeline (validate+project).

A v1 vault is a directory of plain files versioned by git. Structured lists
are JSON; narratives are markdown with YAML frontmatter. All machine
validation happens against [`vault-v1.schema.json`](vault-v1.schema.json)
(JSON Schema draft 2020-12, one `$defs` entry per entity).

## Layout

```
<vault>/
├── traitprint.json          # manifest             → $defs/manifest
├── profile.json             # identity block       → $defs/profile
├── skills.json              # array                → $defs/skill
├── education.json           # array                → $defs/education
├── experiences/*.md         # frontmatter          → $defs/experienceFrontmatter
├── stories/*.md             # frontmatter          → $defs/storyFrontmatter
├── philosophies/*.md        # frontmatter          → $defs/philosophyFrontmatter
├── proposals/*.json         # staged writes        → $defs/proposal
├── custom.md                # optional, schema-ignored user instructions
├── .credentials             # gitignored, never synced
└── .git/
```

`custom.md` is an **optional, schema-ignored** file: free-form user
instructions for wrapping agents (served alongside the MCP workflow
prompts). It has no `$defs` entry, is never created or written by tooling
(user-owned; read-only from the package's perspective), and readers must
ignore it. Like `.credentials`, its presence or absence carries no schema
meaning — no contract revision tracks it.

## Rules

1. **Identity is the frontmatter/JSON `id` (UUIDv4), never the filename.**
   Filenames are kebab-case slugs derived from the title; on collision the
   first 8 chars of the id are appended. Renames are git-tracked and do not
   break links.
2. **Cross-links are by UUID**: `skill_ids[]` (on stories *and*, since
   revision 1.1, experiences), `experience_id`, `evidence_story_ids[]`.
   Dangling references are a validation *warning*, never a parse error:
   Layer 1 referential checks warn locally (audit finding) and are
   accepted + quarantined as disputed at cloud ingest (architecture D10).
   - A story's `skill_ids[]` are the skills the story *evidences*.
   - An experience's `skill_ids[]` (optional, revision 1.1) are the
     skills *exercised in that role*. The field is additive — vaults
     written before 1.1 omit it and remain valid; readers treat a
     missing key as an empty list.
   - An experience's `skill_links[]` (optional, revision 1.2) annotate a
     per-skill `proficiency` (1–5) for skills already in `skill_ids[]`.
     `skill_ids[]` stays authoritative for membership: an entry whose
     `skill_id` is not in `skill_ids[]` carries no membership effect and
     is ignored, and a skill in `skill_ids[]` with no matching link has
     unset proficiency. Additive — vaults before 1.2 omit it; an empty
     `skill_links` is not emitted (kept out of frontmatter) so 1.1 vaults
     round-trip byte-identically.
3. **Markdown bodies are the source of truth for narrative text.**
   - `experiences/*.md`: body = role description.
   - `stories/*.md`: body uses `## Situation`, `## Task`, `## Action`,
     `## Result` headings (each required, in order) and optional `## Lesson`.
   - `philosophies/*.md`: body = the stance, in the user's own words.
4. **Timestamps** are ISO 8601 UTC (`2026-06-10T12:00:00Z`).
5. **Proficiency is 1–5**: 1 familiar, 2 working, 3 proficient, 4 expert,
   5 authority. (v0 used 1–10; migration maps `ceil(x/2)`.)
6. **`profile.json` is JSON Resume-compatible** for the keys that overlap
   (`basics.name`, `basics.label`, `basics.summary`, `basics.email`, and —
   since revision 1.3 — `basics.phone`, `basics.url`, `basics.profiles[]`).
   Deviation: `basics.location` is a plain string, not the JSON Resume
   location object. Each `profiles[]` entry is `{ network, username?, url? }`
   with `network` required non-empty. The 1.3 keys are omitted while
   empty/absent so pre-1.3 vaults round-trip byte-identically.
7. **Proposals** (`proposals/*.json`) are staged writes awaiting review.
   They are synced but never applied until approved (CLI
   `traitprint proposals list|show|approve|reject` — shipped in 0.9.0;
   also available in the Traitprint Cloud review queue). Approval
   applies the payload to the target file(s) and deletes the proposal in
   the same commit; rejection keeps the file with `status: rejected`
   and a `resolved_at` timestamp. Filenames are `<kind>-<id8>.json`
   slugs (kebab-case kind + first 8 hex chars of the id) — identity is
   the document `id`, never the filename.
8. **Readers accept v0 (`vault.json`) and v1; writers emit v1 only.**
   `traitprint vault migrate` converts v0→v1 in place as a single git
   commit. The lossless single-document JSON export remains available for
   v0 consumers.

## Versioning

`traitprint.json#schema_version` is the only version signal *on disk*.
Additive, backward-compatible changes (new optional fields) do not bump
the version; anything that changes meaning or layout does. Additive
changes are tracked as contract *revisions* of this document and the
schema (`$comment` in `vault-v1.schema.json`) so consumers can cite what
they implement.

### Revision history

- **1.3 (2026-07-02)** — additive: optional `basics.phone`, `basics.url`
  and `basics.profiles[]` on the profile entity (`$defs/profile`,
  `$defs/profileLink`), following the JSON Resume `basics` vocabulary:
  `phone` and `url` (personal website/portfolio) are plain strings;
  `profiles[]` entries are `{ network, username?, url? }` social links
  with `network` required non-empty. All three keys are omitted from
  `profile.json` while empty so pre-1.3 vaults round-trip
  byte-identically. The jsonresume exporter now emits these fields
  instead of a hardcoded empty `profiles` array. `update_profile`
  proposal payloads accept the new keys under `payload.basics`. Older
  vaults without the keys remain valid; `schema_version` stays `1`.
- **1.2 (2026-06-13)** — additive: optional `skill_links[]` on the
  experience entity (`$defs/experienceFrontmatter`, `$defs/skillLink`) —
  an array of `{ skill_id, proficiency? }` objects annotating a per-skill
  proficiency (1–5) for skills already listed in `skill_ids[]`.
  `skill_ids[]` remains authoritative for membership: an entry whose
  `skill_id` is not in `skill_ids[]` is ignored (no membership effect),
  and a skill in `skill_ids[]` with no matching link has unset
  proficiency. Empty `skill_links` is omitted from frontmatter so 1.1
  vaults round-trip byte-identically. Stories are unchanged. Older vaults
  without the key remain valid; `schema_version` stays `1`.
- **1.1 (2026-06-11)** — additive: optional `skill_ids[]` on the
  experience entity (`$defs/experienceFrontmatter`) — the skills
  exercised in that role, same UUID-array reference style as story
  `skill_ids[]`. Referential rules mirror story skill refs (rule 2):
  dangling references are a Layer 1 warning locally and are accepted +
  quarantined at cloud ingest, never rejected. Older vaults without the
  key remain valid; `schema_version` stays `1`.
- **1.0** — initial stable contract.
