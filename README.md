# Traitprint

**A structured career profile that AI tools can query — and that an AI agent
can help you build.**

> Your resume is a lossy snapshot. Your Traitprint is a live, queryable record
> of your skills, experience, stories, and philosophy — kept on your laptop,
> shared on your terms.

One rule holds everywhere: **the vault never asserts a fact you didn't put in
it.** Lenses re-order and re-weight what's true for a given audience — they
cannot invent. Agents stage what they extract as proposals *you* approve, and
they never fabricate a skill, an ID, or a link. Presentation flexes; facts
don't. (This isn't a promise in a README — it's enforced by the schema, the
proposals channel, and the audit.)

Traitprint ships as **two products**:

- **Traitprint Local** (`pip install traitprint`) — a local-first vault and
  MCP server. Zero accounts, zero network calls, MIT-licensed. This README is
  about Local.
- **Traitprint Cloud** (`pip install 'traitprint[cloud]'`) — the hosted version
  of exactly this, plus the few features that genuinely need a server: a public
  profile, a hosted MCP endpoint recruiters' agents can reach, job matching, and
  cross-device sync. Everything you can do locally, you can still do; cloud only
  *adds*. See [Traitprint Cloud (opt-in)](#traitprint-cloud-opt-in).

## The vault

The vault is the whole product: a directory on your machine (default
`~/.traitprint`) holding a file tree of plain JSON and markdown, versioned as
a git repo — no database, no proprietary format. Structured lists are JSON
(`profile.json`, `skills.json`, `education.json`); narratives are markdown
with YAML frontmatter (`experiences/`, `stories/`, `philosophies/`), so you
— or an agent — can edit them with any text editor. (Format contract:
[docs/schema/vault-v1/](docs/schema/vault-v1/). Upgrading from an older
single-file vault? Run `traitprint vault migrate`.) Six kinds of entry make
up the structure agents read:

| Entry | What it captures | CLI |
|---|---|---|
| **Profile** | Name, headline, summary, location | `vault set-profile` |
| **Skills** | What you can do, with a 1–5 proficiency (familiar → authority) and an O*NET taxonomy link | `vault add-skill` |
| **Experiences** | Roles you've held — title, company, dates, accomplishments, linked to the skills exercised in the role | `vault add-experience` |
| **Stories** | STAR-format narratives (Situation, Task, Action, Result), linked to the skills and experience they prove | `vault add-story` |
| **Philosophies** | Stated beliefs on leadership, collaboration, technical approach, culture, decision-making — each backed by evidence stories | `vault add-philosophy` |
| **Education** | Institutions, degrees, fields of study | `vault add-education` |

The payoff is the cross-links: a **skill** is credible because a **story**
demonstrates it; a **philosophy** lands because a story shows you living it; a
story is grounded because it belongs to a real **experience**. Those links are
what the MCP tools traverse, and what the [coherence audit](#audit-the-vault-for-coherence)
checks.

### User layer vs system layer

Traitprint splits cleanly into two layers with different owners:

- **User layer — yours, never touched by upgrades.** Everything in the vault
  directory: your JSON and markdown entries, git history, and an optional
  `custom.md` at the vault root. `custom.md` is a free-form instruction file
  for the agents that wrap traitprint — house rules, output preferences,
  off-limits topics. The package only ever *reads* it (the MCP prompts append
  it to every served workflow); it never creates, writes, or deletes it, so
  your customizations survive every `pip install --upgrade traitprint`.
- **System layer — ours, replaced on upgrade.** The wheel-shipped CLI, MCP
  server, and [Agent Skills](skills/). Any edits you make to installed skill
  files are silently reverted by the next upgrade — put durable instructions
  in `custom.md` instead.

Your `custom.md` rules win on style and workflow, but they cannot override
safety invariants: agents still stage extracted writes through the proposals
channel and never invent taxonomy IDs or UUIDs.

## Quickstart

```
pip install traitprint
traitprint init
traitprint vault set-profile --name "Your Name" --headline "Your Role"
traitprint vault add-skill "Postgres" --proficiency 4 --category technical
traitprint mcp-serve
```

Add some content before running `mcp-serve` — otherwise every MCP query returns
an empty vault. The fastest way to bootstrap is to point an LLM at your existing
resume:

```
pip install 'traitprint[import]'
traitprint vault import-resume ~/Downloads/resume.pdf   # BYOK: Anthropic / OpenAI / Ollama / OpenRouter
```

Point Claude Desktop (or any MCP client) at `traitprint mcp-serve` and any AI
assistant you use can answer questions about your career: which projects used
Postgres, what your management philosophy is, the story behind a job change.
No account. No cloud. No vendor lock-in. Your vault is a file on your machine.

A fresh `pip install traitprint` **never imports a network client at module
load**. Only `cloud.py` and `providers/*` import `httpx`, and neither path is
reachable without the `[cloud]` or `[import]` extras — so the base CLI cannot
make a network request. (See [docs/privacy.md](docs/privacy.md) for the full
threat model.)

### Claude Desktop MCP config

Add Traitprint to your Claude Desktop config file
(`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS,
`%APPDATA%\Claude\claude_desktop_config.json` on Windows):

```json
{
  "mcpServers": {
    "traitprint": {
      "command": "traitprint",
      "args": ["mcp-serve"],
      "env": {
        "TRAITPRINT_VAULT_DIR": "/Users/you/.traitprint"
      }
    }
  }
}
```

- `command` must resolve on Claude Desktop's `PATH`. If `pip install traitprint`
  landed in a venv or a user-local `bin/` that Claude Desktop can't see, use the
  absolute path (e.g. `/Users/you/.local/bin/traitprint` or
  `/opt/homebrew/bin/traitprint`). Run `which traitprint` to find it.
- `TRAITPRINT_VAULT_DIR` is optional — omit it to use the default `~/.traitprint`.
- Restart Claude Desktop after editing the config. The `traitprint` server should
  appear in the MCP tools list, exposing the query tools and workflow prompts
  described below.

The same snippet works for any MCP client that accepts an `mcpServers` block
(Cursor, Zed, Continue, etc.).

### Bootstrap any agent CLI (`traitprint agents init`)

```
traitprint agents init ~/my-project
```

One command scaffolds a project directory for agent CLIs: thin entrypoint
wrappers (`CLAUDE.md`, `QWEN.md`, `.grok/GROK.md`) that all delegate to a
single canonical `AGENTS.md` (Codex CLI, OpenCode, and Kimi CLI read it
natively), the bundled [Agent Skills](skills/) copied to `.agents/skills/` and
`.claude/skills/`, and project-scoped MCP registration for
`traitprint mcp-serve` (`.mcp.json`, `opencode.json`, `.qwen/settings.json`,
`.grok/settings.json`) — plus copy-paste snippets for home-directory configs
(Codex CLI, Kimi CLI). Existing files are never overwritten, and re-running
is safe.

Honest positioning: if you already use
`npx skills add DataViking-Tech/traitprint` (skills) or the Gemini CLI
extension (which this command deliberately skips — `gemini-extension.json`
covers it), much of this is covered. `agents init` earns its keep for
pip-only setups without Node and for the per-runtime MCP wiring.

## Working with an AI agent

Traitprint is designed so an AI agent does most of the heavy lifting — both
*reading* your vault and *helping you fill it out and keep it honest*. The MCP
server exposes two kinds of primitive:

**Seven read-only query tools.** They share the hosted cloud server's
response envelope, and every tool except `doctor` is served hosted too —
but the surfaces are not identical (the hosted server layers proposal and
jobs tools on top, and a few filters differ); the full local ↔ hosted
delta lives in [`AGENTS.md`](AGENTS.md):

| Tool | "Ask it…" |
|---|---|
| `get_profile_summary` | a one-shot identity primer — headline, bio, top skills; optional `lens` renders it through a positioning lens |
| `search_skills` | "what do they know about X?" — taxonomy- and graph-aware skill search |
| `find_story` | "tell me about a time when…" — STAR narrative retrieval by free-text query, situation, theme, or outcome |
| `get_philosophy` | "what's their stance on X?" — filter by topic and/or category |
| `vault_lens_list` | which positioning lenses exist, and which is the default |
| `vault_lens_get` | one lens in full — salience map, signature content, overrides |
| `doctor` | "where should we start?" — vault phase + freshness findings, each naming the skill that fixes it (local-only) |

**Eight prompts** — ready-made workflows an agent can pull to drive the vault
forward, most adapted from the Traitprint Cloud Experience Mining engine (the
same Socratic coach and its mining modes); `deepen_story` and
`improve_profile` are new here. In Claude Desktop they show up in the
slash-command menu; in an agentic client (Claude Code, Cursor) they pair with
shell access so the agent can run the CLI directly:

| Prompt | What it does |
|---|---|
| `fill_vault` | Socratic interview that writes what it learns to the vault via the CLI. Optional `focus` narrows to one section. |
| `mine_story_gaps` | STORY OPPORTUNITY mode — mines STAR stories for the skills and roles the audit flags as having none. |
| `discover_skills` | SKILL DISCOVERY mode — probes for latent skills you have but haven't added yet. |
| `draft_star_story` | FOCUSED deep dive — turns one raw accomplishment into a crisp, well-linked STAR story. Optional `experience` seeds the topic. |
| `audit_coherence` | Runs the coherence audit, then applies judgment on consistency, voice, and evidence quality. |
| `position_lens` | Curates a positioning lens for a target role — salience, signature content, headline/bio overrides — without inventing a fact. |
| `deepen_story` | CROSS-EXAMINATION mode — hardens one STAR story until it survives follow-up questions: sourced metrics, split attribution, honest outcome. Optional `story` names the target. |
| `improve_profile` | TRIAGE mode — ranks the whole vault by leverage and surfaces the 1-3 highest-payoff tasks, each with its exact next step. Optional `focus` narrows the ranking. |

### Agent Skills

The same workflows ship as [SKILL.md Agent Skills](skills/)
(agentskills.io open standard) for filesystem agents like Claude Code,
Codex CLI, Gemini CLI, and Cursor — plus three skills with no prompt
counterpart: `traitprint-import-resume` (agent-assisted resume import),
`traitprint-capture-story` (background STAR capture that stages a
story proposal whenever a work anecdote surfaces mid-session), and
`traitprint-agent-vault-sync` (round-trip sync with an external agent
career tool's working directory):

```
npx skills add DataViking-Tech/traitprint
```

The skills are the canonical text — the MCP prompts serve their bodies
verbatim, so the two surfaces never drift. A shared CLI cheatsheet lives at
[`skills/shared/cli-reference.md`](skills/shared/cli-reference.md), and the
agent operating manual is [`AGENTS.md`](AGENTS.md).

Gemini CLI users can install the whole bundle — skills, a
[`GEMINI.md`](GEMINI.md) context file, and the hosted MCP server — as one
extension:

```
gemini extensions install https://github.com/DataViking-Tech/traitprint
```

### Fill out the vault

Hand an agent the `fill_vault` prompt (or just ask it to "help me build my
Traitprint"). The intended loop:

1. The agent reads what's already there (`get_profile_summary`, `search_skills`)
   so it doesn't re-ask.
2. It interviews you one topic at a time, pushing for specifics — numbers,
   dates, what changed — rather than adjectives.
3. It writes each item with the CLI. Every `add-*` command takes flags for
   non-interactive use and a `--from-json` batch mode, so an agent can add a
   dozen items in one pass:

   ```bash
   traitprint vault add-skill --from-json - <<'JSON'
   [{"name": "Postgres", "proficiency": 4, "category": "technical"},
    {"name": "Incident Response", "proficiency": 3, "category": "soft"}]
   JSON
   ```

4. It links the pieces: a story gets `--skill-id` and `--experience-id`; a
   philosophy gets `--evidence-id` pointing at a story. Those links are what
   make later queries return real evidence instead of bare claims.

Every write auto-commits to the vault's git history, so `vault history`,
`vault diff`, and `vault rollback` give you (and the agent) a safety net.

### Audit the vault for coherence

A vault full of unsupported claims reads worse than a short, honest one. The
`audit` command is a deterministic, read-only pass — the same heuristics
Traitprint Cloud uses, ported to Python so Local and Cloud agree on what
"coherent" means. It scores each STAR story and flags where the narrative
doesn't hold together:

```
traitprint vault audit
```

```
[major] skills: Skill 'Kubernetes' is claimed at 5/5 but no story demonstrates it.
[major] philosophies: Philosophy 'Bias to ship' cites no evidence story.
[major] stories: 'The big migration': Result lacks measurable outcomes — add metrics
[minor] experiences: Experience 'Founding Engineer' has no description...

Story coherence:
  Polished  (88%) The big migration
  Draft     (24%) That one outage
  Overall: 56%

Philosophy tensions (nuance, not problems):
  ~ Your philosophy on leadership shows nuance — you value both autonomy and structure…

Summary: 0 critical, 3 major, 1 minor.
```

What it produces:

- **Per-story coherence scores** — each STAR story is graded
  `Polished` / `Strong` / `Solid` / `Draft` with an evidence level
  (`demonstrates` / `mentions` / `weak`), checking field substance, active
  language, measurable results, and the Situation→Task→Action→Result causal
  chain.
- **Findings** at `critical` / `major` / `minor` severity — unsupported strong
  skills, philosophies with no evidence, broken or thin stories, dangling
  references, orphaned roles, and **contradictions between stories** in the
  same role (conflicting metrics, or one story claiming leadership while
  another claims solo IC work).
- **Philosophy tensions** — when two beliefs in the same category pull in
  opposite directions, surfaced as *nuance* (context-dependent thinking), never
  as a bug to fix.

Flags:

- `--json` — the full report (`findings`, `story_scores`, `tensions`,
  `summary`) for an agent to act on.
- `--severity critical|major|minor` — minimum level to report.
- `--strict` — exit non-zero when any critical or major finding remains (handy
  in CI or a pre-`push` check).

Pair it with the `audit_coherence` prompt to go past the mechanical checks: an
agent reads the findings, then judges the things a script can't — whether your
headline, skills, and stories describe the same person, whether the voice is
consistent, whether "results" are real outcomes.

## Who it's for

Traitprint is useful if you want your career data to be structured, portable,
and queryable — not locked inside a PDF or a recruiter platform. Three concrete
examples:

### 🎯 The job seeker

> "I'm tired of rewriting my resume for every application, and I want recruiters
> who use AI tools to actually find me."

Build your vault once — import your resume, then have an agent run `fill_vault`
and `audit` to round it out. Export tailored resumes with `traitprint export`,
or (with the cloud extra) `traitprint push` to publish a profile recruiters'
agents query directly — skills, dates, stories — instead of guessing from
keyword-matched PDFs.

### 🧑‍💻 The developer using Claude Desktop / Cursor / any MCP client

> "I want my AI assistant to know my actual stack, projects, and decisions —
> not generic advice."

Run `traitprint mcp-serve` and add it to your MCP client config. Your assistant
can now call `search_skills`, `find_story`, and `get_philosophy` to ground its
suggestions in your real history. Ask "draft a cover letter for this role" and
it pulls from the vault, not a hallucinated resume.

### 🧭 The career coach

> "I work with a dozen clients and I need their career data structured the
> same way so I can compare, advise, and produce portfolios."

Use `traitprint vault import-resume` (BYOK LLM) to pull each client's resume into
a structured vault, then `vault audit` to spot the gaps to coach on. Edit,
version, and `export` polished portfolios. Same schema for every client means
coaching workflows compose instead of starting from scratch each time.

## What's in the box

- **Local vault** — a plain JSON + markdown file tree on your laptop,
  versioned with git.
- **MCP server (stdio)** — seven query tools (`get_profile_summary`,
  `search_skills`, `find_story`, `get_philosophy`, `vault_lens_list`,
  `vault_lens_get`, and local-only `doctor`) and eight workflow prompts
  (`fill_vault`, `mine_story_gaps`, `discover_skills`, `draft_star_story`,
  `audit_coherence`, `position_lens`, `deepen_story`, `improve_profile`).
- **Agent Skills** — the same workflows as SKILL.md skills under
  [`skills/`](skills/) for Claude Code, Codex CLI, Gemini CLI, Cursor, etc.
- **CLI** — `traitprint init`, `traitprint vault set-profile`, `add-skill`,
  `add-experience`, `add-story`, `add-philosophy`, `add-education`, `lens`,
  `remove`, `show`, `list`, `audit`, `history`, `diff`, `rollback`, `migrate`,
  `export`, `extract-text`, `import-resume`, `import-story-bank`, the
  `proposals` group (staged writes the user approves), plus top-level
  `doctor`, `sync`, and `agents init`.
- **Coherence audit** — `traitprint vault audit` flags unsupported claims,
  unbacked philosophies, and broken stories (text, `--json`, or `--strict`).
- **Resume import** with BYOK LLM (Anthropic, OpenAI, Ollama, OpenRouter) —
  install with `pip install 'traitprint[import]'`.
- **Optional cloud sync** — `login`, `logout`, and git-native
  `traitprint sync` (`push` / `pull` / `status` / `taxonomy`); the legacy
  whole-vault `push` / `pull` still work but are deprecated in favor of
  `sync`. Install with `pip install 'traitprint[cloud]'`.

## Local vs Cloud

Traitprint is local-first. The design rule is simple: **a feature belongs in
local unless it can't work locally.** Everything below the line runs on your
laptop with no account, no network calls, and no paywall — and cloud is the same
thing, hosted, plus the handful of features that genuinely need a server.

| Capability | Free forever, no account | Requires traitprint.com account |
|---|---|---|
| Create + edit your vault (`init`, `vault add-*`, `remove`) | ✅ | — |
| MCP query tools + workflow prompts + Agent Skills | ✅ | — |
| Narrative-coherence audit (`vault audit`) | ✅ | — |
| Version history, diff, rollback | ✅ | — |
| Resume import via BYOK LLM | ✅ | — |
| Export (`json`, `markdown`, `jsonresume`/`json-resume`, `synthpanel-persona`, `career-bundle`) | ✅ | — |
| MIT-licensed source, fork and self-host | ✅ | — |
| Public profile at `traitprint.com/profile/you` | — | ✅ |
| Hosted MCP endpoint reachable by recruiter agents | — | ✅ |
| Job matching against a shared job index | — | ✅ |
| Digital-twin chat | — | ✅ |
| Cross-device sync | — | ✅ |

A fresh install never talks to traitprint.com. Cloud features are opt-in via
`traitprint login` and `traitprint push`.

**Full details and migration guide:** [docs/why-local.md](docs/why-local.md)

**Privacy commitment (what leaves your machine on `push`, what we store,
what we don't do, how to delete everything):** [docs/privacy.md](docs/privacy.md)

## Traitprint Cloud (opt-in)

When you want a public profile, job matching, or a chat-ready twin that
recruiters can talk to, install the cloud extras:

```
pip install 'traitprint[cloud]'
traitprint login
traitprint push
```

…and you're live at `traitprint.com/profile/you`. Without the `[cloud]`
extras, `traitprint login` / `logout` / `push` / `pull` print:

```
Error: Cloud sync requires: pip install traitprint[cloud]
```

Before uploading, `push` runs the [coherence audit](#audit-the-vault-for-coherence)
and **blocks on critical findings** — broken stories, dangling references,
contradicting roles, anything you'd never want on a public profile. Major and
minor findings are advisory by default; pass `--strict` to block on major ones
too (full `vault audit --strict` semantics), or `--skip-audit` to bypass the
check entirely.

### Connect from claude.ai / ChatGPT / Gemini CLI

With a traitprint.com account, your synced vault is reachable from the
chat apps through the hosted MCP server — paste one URL, approve the
OAuth consent, done:

```
https://api.traitprint.com/functions/v1/mcp-server
```

- **claude.ai** — Settings → Connectors → **Add custom connector** →
  paste the URL. Claude walks you through sign-in and scope consent.
- **ChatGPT** — Settings → Apps & Connectors → enable Developer mode
  (Advanced settings), then Connectors → **Create** → paste the URL.
- **Gemini CLI** —
  `gemini extensions install https://github.com/DataViking-Tech/traitprint`
  wires the same server (plus the [Agent Skills](#agent-skills) and a
  `GEMINI.md` context file); approve the sign-in with `/mcp auth traitprint`.

Headless clients (CI, scripts, `mcp-remote`) can skip OAuth: generate an
`sk_` API key in the web app (Settings → API Keys) and send it as
`Authorization: Bearer sk_…`. Reads return your vault; writes are always
staged as proposals you approve — see
[`docs/distribution-runbook.md`](docs/distribution-runbook.md) for the
full surface list.

### Non-interactive auth (CI, agents, Docker)

`traitprint login` accepts three credential sources, in this precedence order:

1. **API token** — `--token <key>` or `TRAITPRINT_API_TOKEN`. Skips email and
   password entirely. Generate a token in the web portal (Settings → API Keys).
   Recommended for CI, AI agents, and any non-interactive shell.
2. **Password env var** — `TRAITPRINT_PASSWORD` (paired with `--email` /
   `TRAITPRINT_EMAIL`). Only consulted if no token is provided.
3. **Interactive prompt** — used as a last resort, only when stdin is a TTY.

```bash
# Recommended: API token via env var (generate an sk_ key in the web app)
export TRAITPRINT_API_TOKEN=sk_xxx
traitprint login        # one-time: persists token to <vault>/.credentials
traitprint sync push

# Or skip login entirely — sync/push/pull also honor TRAITPRINT_API_TOKEN directly
TRAITPRINT_API_TOKEN=sk_xxx traitprint sync push
```

Avoid `--password <pw>` on the command line: it lands in shell history and
process listings. Use `TRAITPRINT_PASSWORD` or, better, an API token.

## Contact

- **Bugs and feature requests:** [GitHub Issues](https://github.com/DataViking-Tech/traitprint/issues)
- **Privacy / data deletion:** `privacy@traitprint.com`
- **Everything else:** `hello@traitprint.com`

## License

[MIT](LICENSE)
