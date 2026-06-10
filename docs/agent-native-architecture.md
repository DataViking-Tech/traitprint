# Agent-Native Architecture & Roadmap

**Status:** Accepted (decisions ratified 2026-06-10)
**Owner:** wesley@dataviking.tech
**Companion doc:** [`traitprint-cloud/docs/specs/agent-native-rearchitecture.md`](https://github.com/DataViking-Tech/traitprint-cloud/blob/main/docs/specs/agent-native-rearchitecture.md) (cloud workstreams)

This document is the canonical record of the agent-native re-architecture of
Traitprint (Local + Cloud). It captures the decisions, the target
architecture, the vault v1 file format, and the phased roadmap. Cloud-side
implementation detail lives in the companion spec; this doc owns the shared
vision and the Local roadmap.

---

## 1. Vision & Positioning

**Traitprint is the portable, user-owned professional context file — the
career profile an AI agent can carry anywhere.**

Users build and refine their professional identity (skills, experiences,
STAR stories, philosophies, education) from whichever interface they prefer:

| Surface | How it works |
|---|---|
| Agent CLIs (Claude Code, Codex CLI, Gemini CLI, Cursor) | Agent Skills + `traitprint` CLI operating directly on the local vault |
| LLM web/chat (claude.ai, ChatGPT, Claude mobile) | Hosted remote MCP server (OAuth) with staged writes |
| Hosted web app (traitprint.com) | React app over the cloud projection |
| Fully local, zero accounts | Local vault + CLI + skills + stdio MCP; MIT, no network |

Market context (researched June 2026): MCP is Linux Foundation-governed
neutral infrastructure with a stateless spec landing 2026-07-28; SKILL.md is
a cross-vendor open standard (Claude, Codex, Gemini CLI, Cursor, ~20+ tools);
JSON Resume is the documented on-ramp to HR Open LER-RS v2 / W3C Verifiable
Credentials. Nobody in the career space has claimed the
*profile-as-canonical-user-owned-data* position. That is the position we take.

## 2. Ratified Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Source of truth | **Local-first; cloud is a projection.** The user-owned vault (files + git) is canonical. Supabase Postgres is a synced projection powering the web app, hosted MCP, and public profile. Cloud-only users have their canonical copy hosted for them. | Strongest ownership/portability story; matches "feature belongs local unless it can't work locally". |
| D2 | Agent writes on hosted MCP | **Staged writes: propose + review.** Write tools create proposals; the user approves from any surface. Direct mutation is reserved for the local CLI (the user's own machine, audit-gated). | Agents must not silently rewrite someone's professional identity; pairs with the coherence audit and the cloud trust layer. |
| D3 | Jobs/matching/swipe | **Keep, but agent-first.** Job ingestion + matching remain cloud differentiators, exposed as MCP tools (`jobs_match`, `resume_tailor`). The swipe/tracker web UI stays but is no longer the primary interface investment. | The job index can't work locally (it's a shared catalog); agents become the discovery surface. |
| D4 | Vault format | **File tree: JSON + markdown** (spec in §4). Replaces single `vault.json` as the native format at schema v1. | Agents edit narratives with their native file tools; diffs are human-readable; the vault *is* the portable career-context artifact. |
| D5 | Sync | **Git-native.** The vault is already a git repo with auto-commits. Sync = push/pull to a hosted per-user remote; the server ingests commits into the Postgres projection. Export = `git clone`. | Real merges, full history, agents are fluent in git, and "clone your career" is the export story. Replaces whole-vault last-write-wins. |
| D6 | Monetization boundary | **Protocol surfaces are free** (MCP, skills, CLI, local product). Monetize hosted features: sync, public digital-twin profile, job index/matching, Pro analytics. | The GitHub/Linear/Notion pattern: MCP is a retention/distribution surface, not a SKU. |
| D7 | Distribution wave 1 | **All four:** Agent Skills via skills.sh, Claude connector directory, ChatGPT app directory, Gemini CLI extension. Plus the official MCP Registry. | Skills are near-zero cost; the three directories all front the same OAuth-enabled remote MCP server. |
| D8 | Schema unification | One canonical schema shared by Local and Cloud (§5). Local leads; Cloud conforms. | Today local proficiency is 1–10 vs cloud 1–5, and philosophy shapes differ. Drift breaks the local↔cloud MCP parity promise. |

## 3. Target Architecture

```
                 ┌────────────────────────────────────────────┐
                 │       USER-OWNED VAULT (canonical)         │
                 │  file tree (JSON + markdown) + git history │
                 └───────┬───────────────────────────┬────────┘
        local surfaces   │                           │  git push/pull (D5)
   ┌─────────────────────┴──────────┐      ┌─────────┴──────────────────────┐
   │ traitprint CLI (--json, batch) │      │  HOSTED GIT REMOTE (sync hub)  │
   │ stdio MCP server               │      │  per-user repo; ingest on push │
   │ Agent Skills (SKILL.md)        │      └─────────┬──────────────────────┘
   │ AGENTS.md                      │                │ ingest / commit-through
   │ → Claude Code, Codex CLI,      │      ┌─────────┴──────────────────────┐
   │   Gemini CLI, Cursor, offline  │      │  POSTGRES PROJECTION (Supabase)│
   └────────────────────────────────┘      │  tp_vault / tp_market / tp_match│
                                           └───┬──────────┬─────────┬───────┘
                                               │          │         │
                                     ┌─────────┴───┐ ┌────┴────┐ ┌──┴──────────┐
                                     │ remote MCP  │ │ web app │ │ public      │
                                     │ (OAuth 2.1, │ │ (React) │ │ digital-twin│
                                     │ staged      │ └─────────┘ │ profile     │
                                     │ writes,     │             └─────────────┘
                                     │ jobs tools) │
                                     └─────┬───────┘
                                           │
                          claude.ai · ChatGPT apps · Gemini ext · Cursor
```

Key invariants:

1. **Everything writes through git commits.** Web-app edits and approved
   proposals are committed by the server to the user's hosted repo; the
   Postgres projection is rebuilt from commits, never written around them.
   For cloud-only users the hosted repo *is* their canonical vault.
2. **Local↔cloud MCP parity** is preserved: identical tool names and
   signatures so agents swap stdio ↔ HTTPS without code change.
3. **Proposals, not mutations, from remote agents** (D2). The local CLI
   gains `traitprint proposals list|show|approve|reject` so review works
   from any surface.
4. **BYOK everywhere.** No platform-proxied LLM calls in Local; Cloud keeps
   keys in Supabase Vault, server-side only.

## 4. Vault v1 File Format (schema_version: 1)

Replaces the single `vault.json` (schema v0). Structured lists stay JSON;
narratives become markdown with YAML frontmatter.

```
~/.traitprint/                  # or any directory (TRAITPRINT_VAULT_DIR)
├── traitprint.json             # manifest: schema_version, vault id, updated_at
├── profile.json                # identity block, JSON Resume-compatible keys
├── skills.json                 # [{id, name, taxonomy_id, proficiency, ...}]
├── education.json
├── experiences/
│   └── 2023-acme-staff-eng.md  # frontmatter: id, title, company, dates,
│                               #   accomplishments[]; body: narrative
├── stories/
│   └── incident-recovery.md    # frontmatter: id, skill_ids[], experience_id,
│                               #   outcome, theme_tags[]; body: S/T/A/R sections
├── philosophies/
│   └── code-review.md          # frontmatter: id, topic, category,
│                               #   evidence_story_ids[]; body: stance
├── proposals/                  # staged writes pending review (synced)
├── .credentials                # gitignored, never synced
├── .gitignore
└── .git/
```

Format rules:

- **Filenames are slugs; identity lives in frontmatter `id` (UUID).** Renames
  are git-tracked and don't break links.
- **Cross-links are by UUID** (`skill_ids`, `experience_id`,
  `evidence_story_ids`) exactly as in schema v0 — the graph survives the split.
- **`profile.json` keys are JSON Resume-compatible** where they overlap
  (`basics.name`, `basics.label`, …) so `export jsonresume` becomes a near
  projection and LER-RS/VC export is a future feature, not a rewrite.
- **Loader accepts v0 and v1; writer emits v1.** `traitprint vault migrate`
  converts in place with a git commit; `export json` still emits the lossless
  single-document form for v0 consumers (SynthPanel etc.).
- **Markdown bodies are the source of truth for narrative text**; extraction
  back to structured STAR fields uses the `## Situation` / `## Task` /
  `## Action` / `## Result` heading convention.

## 5. Canonical Schema Unification

Local leads; Cloud conforms (migration plan in the cloud spec).

- **Proficiency: 1–5 named levels** (1 familiar, 2 working, 3 proficient,
  4 expert, 5 authority). Local migrates from 1–10 via `ceil(x/2)` during
  the v0→v1 vault migration. Rationale: 10 points is false precision; Cloud
  and its existing user data already use 1–5.
- **Stories:** superset of both shapes — STAR fields + `lesson`,
  `outcome (win|failure|learning)`, `theme_tags[]` (from Cloud) +
  `skill_ids[]`, `experience_id` (from Local).
- **Philosophies:** `topic` + `stance` (Cloud) + optional `category` enum +
  `evidence_story_ids[]` (Local). `supporting_examples[]` folds into the
  markdown body.
- A versioned JSON Schema for the vault is published in this repo
  (`docs/schema/vault-v1/`) and consumed by Cloud's ingest pipeline as the
  contract.

## 6. Roadmap

Phases are ordered by dependency; Local items here, Cloud items in the
companion spec. Wave-1 distribution (D7) closes the loop.

### Phase 0 — Foundations (contract first)
- **tp-an-001** Publish vault v1 JSON Schema + format spec (§4, §5) under `docs/schema/vault-v1/`.
- **tp-an-002** `--json` and non-interactive audit across every CLI command; structured, actionable errors; meaningful exit codes.
- **tp-an-003** Rewrite `AGENTS.md` as the agent operating manual for the CLI (commands, JSON contracts, gotchas); slim `CLAUDE.md` to a pointer.

### Phase 1 — Vault v1 + Skills (local product, zero server)
- **tp-an-010** Implement file-tree vault store (read v0+v1, write v1) + `traitprint vault migrate` with proficiency remap.
- **tp-an-011** Port the 5 MCP prompts (`fill_vault`, `mine_story_gaps`, `discover_skills`, `draft_star_story`, `audit_coherence`) to SKILL.md skills under `skills/`; keep MCP prompts as thin wrappers for compat.
- **tp-an-012** Publish skills to skills.sh (`npx skills add dataviking-tech/traitprint`).
- **tp-an-013** Update coherence audit + exports + stdio MCP server for the file-tree store.

### Phase 2 — Git-native sync
- **tp-an-020** Local side: replace `push`/`pull` whole-vault sync with git remote add/push/pull against the hosted remote; conflict UX = standard git with guidance.
- **tp-an-021** Proposal review in CLI: `traitprint proposals list|show|approve|reject` (reads `proposals/`, approval applies the change + commits).
- *(Cloud: hosted remotes, ingest pipeline, commit-through writes — see companion spec.)*

### Phase 3 — Hosted MCP v2 (cloud spec owns detail)
- OAuth 2.1 + RFC 9728/8707; keep `sk_*` keys for headless use.
- Staged-write tools (`vault_propose`, `vault_list_proposals`); jobs tools (`jobs_match`, `resume_tailor`); tool annotations (`readOnlyHint`); 2026-07-28 stateless-spec readiness.

### Phase 4 — Distribution (wave 1, D7)
- MCP Registry publish; Claude connector directory; ChatGPT app directory; Gemini CLI extension (manifest wrapping MCP + GEMINI.md + skills).

> **Issue tracking note:** `bd` was unavailable in the session that authored
> this doc. File the `tp-an-*` items above into beads verbatim at next local
> session (`bd create` per row, phase as label, this doc as the design link).

## 7. Out of Scope / Explicitly Not Doing

- CRDT sync layer (revisit only if concurrent human+agent editing of the
  *same file* becomes a real pain; git merge granularity is acceptable for v1).
- Monetizing the MCP server or skills directly (D6).
- Verifiable Credentials / LER-RS issuance — designed-for (profile.json is
  JSON Resume-compatible) but not built until wallet adoption matures.
- Merging Local and Cloud repos. They remain separate products; the contract
  between them is the vault v1 schema + git sync protocol.
