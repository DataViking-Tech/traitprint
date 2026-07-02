# Syncing a vault with external agent career tools

Traitprint is the durable, git-versioned career identity store. External
agent-driven CLI career tools — career-ops and similar job-search
copilots that run on agent runtimes like Claude Code, Codex CLI, Gemini
CLI, or opencode — keep a *per-search working directory* of freeform
files: a `cv.md`, a `config/profile.yml`, interview-prep notes. This
page documents the supported round-trip between the two, and how to wire
it into the external tool's own user-layer configuration.

> **Brand & attribution note.** "career-ops" is a third-party tool and a
> reserved brand of its authors; it is referenced on this page
> nominatively only — as in "works with career-ops" — never as part of a
> Traitprint identifier. career-ops is MIT-licensed; where Traitprint
> adapts code from it, attribution lives in the adapting module's
> docstring. Traitprint's own identifiers stay brand-neutral: the skill
> is `traitprint-agent-vault-sync`.

## The sync loop

Four steps, shipped as the
[`traitprint-agent-vault-sync`](../skills/traitprint-agent-vault-sync/SKILL.md)
Agent Skill (`npx skills add DataViking-Tech/traitprint` installs it into
any skills-aware agent):

1. **Export — deterministic.**
   `traitprint vault export -f markdown -o <workdir>/cv.md` renders the
   CV, and `traitprint vault export -f json` supplies grounded,
   UUID-carrying facts the agent copies into the tool's other files
   (the fact fields of `config/profile.yml`, the story bank's grounding
   details). No LLM, no judgment, no vault writes. `-f` is a fixed
   choice list — `traitprint vault export --help` shows what an
   installed version supports.
2. **Judgment gaps — the agent asks the user.** The export fills
   everything the vault knows; what remains in `config/profile.yml` are
   judgment calls a vault does not store — compensation targets, the
   exit story, archetype/role-fit rankings. The wrapping agent fills
   ONLY those, from the user's answers, never from guesswork.
3. **Write-back — proposals, never direct writes.** Anything the
   external tool produced that belongs in the vault (a STAR story mined
   during interview prep, a sharper summary, a newly surfaced skill) is
   staged with `traitprint proposals add`. The user reviews with
   `traitprint proposals list` / `traitprint proposals show` and applies
   with `traitprint proposals approve`. Nothing touches the vault
   silently, and taxonomy IDs / UUIDs are never invented.
4. **Audit.** `traitprint vault audit --json` closes the loop; the agent
   reports findings alongside the pending-proposal queue.

This is the agent-is-the-model pattern (architecture decision D11): the
agent already driving the external tool does the judgment work on the
user's existing subscription — no BYOK key anywhere in the loop.

## Copy-paste: a "sync traitprint" custom workflow for career-ops

career-ops reads user-owned custom modes from `modes/_custom.md`; its
"Custom Workflows" section is the **user layer**, so entries there
survive the tool's own `npm run update`. Paste this block into the
`modes/_custom.md` of *your* career-ops install (it is your
configuration file — editing it is ordinary user-layer use):

```markdown
### sync traitprint

When I say "sync traitprint":

1. Refresh this working directory from my Traitprint vault:
   `traitprint vault export -f markdown -o cv.md`, plus
   `traitprint vault export -f json -o traitprint-export.json` for
   grounded facts with UUIDs — copy from these, never from memory.
2. Ask me — do not guess — for any judgment fields still blank in
   config/profile.yml: compensation targets, exit story, archetype fits.
3. Stage anything new this directory produced since the last sync
   (stories, summary edits, new skills) back to the vault with
   `traitprint proposals add` — one JSON payload per item, STAR text in
   payload.body. Never write vault files directly; never invent UUIDs
   or taxonomy IDs.
4. Run `traitprint vault audit --json`, then show me
   `traitprint proposals list`; I approve with
   `traitprint proposals approve`.

Full workflow: the traitprint-agent-vault-sync Agent Skill
(npx skills add DataViking-Tech/traitprint).
```

## Prefer MCP for grounded facts

Every agent runtime these tools run on can also attach MCP servers.
`traitprint mcp-serve` (stdio) gives the same agent grounded,
UUID-linked vault facts on demand — `get_profile_summary` for the
identity primer, `find_story` for "tell me about a time when…"
retrieval, plus `search_skills` and `get_philosophy` — instead of
re-reading a possibly stale freeform `cv.md` snapshot. Client
configuration:

```json
{"mcpServers": {"traitprint": {"command": "traitprint", "args": ["mcp-serve"],
  "env": {"TRAITPRINT_VAULT_DIR": "/home/you/.traitprint"}}}}
```

The file export and the MCP server compose: files for tools that only
read the working directory, MCP for runtimes that can query live. Either
way the vault stays the single source of truth.

## Safety invariants (unchanged by this workflow)

- **Extraction is a proposal.** Content produced outside the vault is
  staged for the user's review — never silently written
  (`docs/schema/vault-v1/`, D9).
- **Never invent taxonomy IDs or UUIDs.** Cross-links are copied from
  `traitprint vault list` output or left off.
- **Modest entry proficiency.** Skills surfaced by external work enter
  at 2-3 pending the user confirming stronger demonstrated evidence.
