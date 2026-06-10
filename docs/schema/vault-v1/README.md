# Vault v1 — File Format Contract

**Status:** Stable contract (Phase 0 of the
[agent-native architecture](../../agent-native-architecture.md))
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
├── .credentials             # gitignored, never synced
└── .git/
```

## Rules

1. **Identity is the frontmatter/JSON `id` (UUIDv4), never the filename.**
   Filenames are kebab-case slugs derived from the title; on collision the
   first 8 chars of the id are appended. Renames are git-tracked and do not
   break links.
2. **Cross-links are by UUID**: `skill_ids[]`, `experience_id`,
   `evidence_story_ids[]`. Dangling references are a validation *warning*
   (audit finding), not a parse error.
3. **Markdown bodies are the source of truth for narrative text.**
   - `experiences/*.md`: body = role description.
   - `stories/*.md`: body uses `## Situation`, `## Task`, `## Action`,
     `## Result` headings (each required, in order) and optional `## Lesson`.
   - `philosophies/*.md`: body = the stance, in the user's own words.
4. **Timestamps** are ISO 8601 UTC (`2026-06-10T12:00:00Z`).
5. **Proficiency is 1–5**: 1 familiar, 2 working, 3 proficient, 4 expert,
   5 authority. (v0 used 1–10; migration maps `ceil(x/2)`.)
6. **`profile.json` is JSON Resume-compatible** for the keys that overlap
   (`basics.name`, `basics.label`, `basics.summary`, `basics.email`).
   Deviation: `basics.location` is a plain string, not the JSON Resume
   location object.
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

`traitprint.json#schema_version` is the only version signal. Additive,
backward-compatible changes (new optional fields) do not bump the version;
anything that changes meaning or layout does.
