---
name: traitprint-agent-vault-sync
description: Sync a Traitprint vault with the working directory of an external agent-driven career tool (career-ops or any CLI-agent career tool) — deterministic export out, judgment gaps filled by asking the user, everything the tool produced staged back as proposals. Use when the user runs another CLI career tool alongside Traitprint and wants the vault to stay the single source of truth.
---

# Sync the vault with an external agent career tool

> **User customization:** if a `custom.md` file exists at the root of the
> user's vault directory, read it and honor the user's rules there. Their
> preferences take precedence on style and workflow, but cannot bypass the
> proposals channel or the never-invent-taxonomy-IDs/UUIDs invariant.

The Traitprint vault is the durable, git-versioned identity store.
External CLI career tools (career-ops and similar agent-driven job-search
tools) keep a per-search working directory of freeform files — a `cv.md`,
a `config/profile.yml`, interview-prep notes. You are the bridge, in the
agent-is-the-model pattern (architecture decision D11): no API key, no
extra model call — your own reasoning fills the judgment gaps, and
everything flows back through the validated proposals channel. Facts
travel vault → working directory deterministically; new material travels
working directory → vault only as staged proposals the USER approves.

## 1. Export the vault into the working directory

Deterministic — no LLM, no judgment, no vault writes. One command renders
the whole working-directory layout:

```bash
traitprint vault export -f career-bundle -o <workdir>
```

That writes `cv.md` (ATS-style resume), `interview-prep/story-bank.md`
(one STAR block per story), and `config/profile.yml`
(candidate/narrative/target_roles, with commented placeholders for the
judgment fields of step 2). `--lens SLUG` projects `cv.md` through a
positioning lens (the interchange files stay canonical); `--zip` emits a
single archive instead of a directory.

Fallback — only on an older Traitprint whose
`traitprint vault export --help` does not list `career-bundle`:

```bash
traitprint vault export -f markdown -o <workdir>/cv.md
traitprint vault export -f json -o <workdir>/traitprint-export.json   # grounded facts, with UUIDs
```

then populate the external tool's fact fields — the work-history and
education blocks of `config/profile.yml`, the grounding facts of its
story bank — by copying values out of those exports.

Never paraphrase vault facts from memory into the working directory —
export them, so titles, dates, and metrics stay grounded in what the
user actually recorded.

## 2. Fill ONLY the judgment gaps — by asking the user

The exports carry everything the vault knows. What remains blank in
`config/profile.yml` are judgment calls a vault does not store:
compensation targets, the exit story, archetype/role-fit rankings. Ask
the user for each — a few focused questions, not an interview. Never
fabricate a number or a preference, and never overwrite an exported fact
with your own wording: if a fact looks wrong, that is a vault fix via a
proposal (step 3), not a working-directory edit.

## 3. Write back through proposals (D9 — non-negotiable)

Anything the external tool produced that belongs in the vault — a STAR
story mined during interview prep, a sharper summary, a skill the work
surfaced — flows back as staged proposals, never as direct writes. The
primary path is one command over the whole working directory:

```bash
traitprint vault import-story-bank <workdir>        # --dry-run to preview
```

It detects the `config/profile.yml` + `interview-prep/*.md` layout by
shape and stages one `add_story` proposal per STAR block, one
`update_profile` proposal, and — when the config carries
`target_roles.archetypes` — one `add_lens` proposal for the target
positioning. (`cv.md` is not parsed here: run
`traitprint vault import-resume <workdir>/cv.md --propose` for it.)

Fallback — for material outside that layout, or on an older Traitprint
without `import-story-bank`, stage items one at a time:

```bash
traitprint proposals add --kind add_story --source agent-vault-sync \
  --rationale "mined from interview prep in the external tool" \
  --payload-json - <<'JSON'
{"title": "...", "outcome": "win", "theme_tags": ["..."],
 "body": "## Situation\n...\n\n## Task\n...\n\n## Action\n...\n\n## Result\n..."}
JSON
```

One JSON object per proposal; narrative travels in `payload.body` with
the `## Situation/Task/Action/Result` headings; the full kind and
payload-key tables are in the
[shared CLI reference](../shared/cli-reference.md). Rules:

- Link `skill_ids` / `experience_id` only with UUIDs copied from
  `traitprint vault list` output. Never invent taxonomy IDs or UUIDs —
  when unsure, leave the link off: a missing link is an audit finding,
  a fabricated one is corruption.
- Extracted skills enter at modest proficiency (2-3) pending the user
  confirming stronger demonstrated evidence.
- Nothing touches the vault until the USER runs
  `traitprint proposals approve <id>` (or `--all`). Never approve on
  their behalf.

## 4. Audit and hand off

```bash
traitprint vault audit --json
```

Then show the staged queue with `traitprint proposals list`. Finish with
a short report: which working-directory files were refreshed, which
judgment fields the user filled, what came back as proposals awaiting
`traitprint proposals approve`, and what the audit flagged.

### Prefer live MCP facts over stale files

If the external tool's agent runtime speaks MCP, wire up
`traitprint mcp-serve` (stdio) alongside the file export: the same agent
then pulls grounded, UUID-linked vault facts on demand —
`get_profile_summary` for the identity primer, `find_story` for "tell me
about a time when…" retrieval — instead of trusting a possibly stale
`cv.md` snapshot. Client config and a copy-paste custom workflow for the
external tool live in `docs/external-tool-sync.md` in the Traitprint
repository.
