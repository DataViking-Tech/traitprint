# Traitprint

**Local-first career identity vault for the agent era.**

> Your resume is a lossy snapshot. Your Traitprint is a live API.

```
pip install traitprint
traitprint init
traitprint mcp-serve
```

Point Claude Desktop (or any MCP client) at `traitprint mcp-serve` and your
agent can talk to your career. No account. No cloud. No vendor lock-in. Your
vault is a file on your machine.

When you want a public profile, job matching, or a digital twin that
recruiters can chat with:

```
traitprint login
traitprint push
```

…and you're live at `traitprint.com/profile/you`.

## What's in the box

- **Local vault** — versioned, SQL-queryable storage on your laptop.
- **MCP server (stdio)** — `get_profile_summary`, `search_skills`,
  `find_story`, `get_philosophy`.
- **CLI** — `traitprint init`, `traitprint vault add-skill`, `add-experience`,
  `add-story`, `add-philosophy`, `add-education`, `remove`, `history`, `diff`,
  `rollback`, `export`, `import-resume`.
- **Resume import** with BYOK LLM (Anthropic, OpenAI, Ollama, OpenRouter) —
  install with `pip install 'traitprint[import]'`.
- **Optional cloud sync** — `login`, `logout`, `push`, `pull`.

## Local vs Cloud

Traitprint is local-first. Everything below runs on your laptop with no
account, no network calls, and no paywall.

| Capability | Free forever, no account | Requires traitprint.com account |
|---|---|---|
| Create + edit your vault (`init`, `vault add-*`, `remove`) | ✅ | — |
| Version history, diff, rollback | ✅ | — |
| MCP server for Claude Desktop / any MCP client (stdio) | ✅ | — |
| Resume import via BYOK LLM (Anthropic, OpenAI, Ollama, OpenRouter) | ✅ | — |
| SQL-queryable Dolt-backed storage | ✅ | — |
| MIT-licensed source, fork and self-host | ✅ | — |
| Public profile at `traitprint.com/profile/you` | — | ✅ |
| Hosted MCP endpoint reachable by recruiter agents | — | ✅ |
| Job matching against a shared job index | — | ✅ |
| Digital-twin chat | — | ✅ |
| Cross-device sync | — | ✅ |

A fresh install never talks to traitprint.com. Cloud features are opt-in via
`traitprint login` and `traitprint push`.

**Full details, privacy commitment, and migration guide:**
[docs/why-local.md](docs/why-local.md)

## License

[MIT](LICENSE)
