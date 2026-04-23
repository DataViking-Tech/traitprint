# Traitprint

**A structured career profile that AI tools can query.**

> Your resume is a lossy snapshot. Your Traitprint is a live, queryable record
> of your skills, experience, stories, and philosophy — kept on your laptop,
> shared on your terms.

Traitprint ships as **two products**:

- **Traitprint Local** (`pip install traitprint`) — a local-first vault and
  MCP server. Zero accounts, zero network calls, MIT-licensed.
- **Traitprint Cloud** (`pip install 'traitprint[cloud]'`) — opt-in cloud sync
  on top of Local: a public profile at `traitprint.com/profile/you`, a hosted
  MCP endpoint, and cross-device sync.

## Traitprint Local

```
pip install traitprint
traitprint init
traitprint mcp-serve
```

Point Claude Desktop (or any MCP client) at `traitprint mcp-serve` and any AI
assistant you use can answer questions about your career: which projects used
Postgres, what your management philosophy is, the story behind a job change.
No account. No cloud. No vendor lock-in. Your vault is a file on your machine.

A fresh `pip install traitprint` ships with **no networking dependency** —
`httpx` is not even installed. The base CLI cannot make a network request.

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
  appear in the MCP tools list, exposing `get_profile_summary`, `search_skills`,
  `find_story`, and `get_philosophy`.

The same snippet works for any MCP client that accepts an `mcpServers` block
(Cursor, Zed, Continue, etc.).

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

## Who it's for

Traitprint is useful if you want your career data to be structured, portable,
and queryable — not locked inside a PDF or a recruiter platform. Three concrete
examples:

### 🎯 The job seeker

> "I'm tired of rewriting my resume for every application, and I want recruiters
> who use AI tools to actually find me."

Build your vault once with `traitprint init` and `vault add-*`. Run
`traitprint push` to publish a profile at `traitprint.com/profile/you`.
Recruiters' agents query your structured profile directly — skills, dates,
stories — instead of guessing from keyword-matched PDFs.

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
a structured vault. Edit, version, and `export` polished portfolios. Same
schema for every client means coaching workflows compose instead of starting
from scratch each time.

## What's in the box

- **Local vault** — plain-JSON storage on your laptop, versioned with git.
- **MCP server (stdio)** — `get_profile_summary`, `search_skills`,
  `find_story`, `get_philosophy`.
- **CLI** — `traitprint init`, `traitprint vault add-skill`, `add-experience`,
  `add-story`, `add-philosophy`, `add-education`, `remove`, `history`, `diff`,
  `rollback`, `export`, `import-resume`.
- **Resume import** with BYOK LLM (Anthropic, OpenAI, Ollama, OpenRouter) —
  install with `pip install 'traitprint[import]'`.
- **Optional cloud sync** — `login`, `logout`, `push`, `pull`. Install with
  `pip install 'traitprint[cloud]'`.

## Local vs Cloud

Traitprint is local-first. Everything below runs on your laptop with no
account, no network calls, and no paywall.

| Capability | Free forever, no account | Requires traitprint.com account |
|---|---|---|
| Create + edit your vault (`init`, `vault add-*`, `remove`) | ✅ | — |
| Version history, diff, rollback | ✅ | — |
| MCP server for Claude Desktop / any MCP client (stdio) | ✅ | — |
| Resume import via BYOK LLM (Anthropic, OpenAI, Ollama, OpenRouter) | ✅ | — |
| Plain-JSON vault with git-backed version history | ✅ | — |
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

## License

[MIT](LICENSE)
