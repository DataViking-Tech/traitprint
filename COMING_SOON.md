# Traitprint

**Local-first career identity vault for the agent era.**

`pip install traitprint` → `traitprint init` → `traitprint mcp-serve` → Claude Desktop connects to your career.

No account. No cloud. No vendor lock-in. Your vault is a file on your machine.

When you want a public profile, job matching, or a digital twin that recruiters can chat with:
`traitprint login` → `traitprint push` → live at traitprint.com/profile/you

## Status

🚧 **Shipping soon.** Announced at AI Engineer Miami 2026-04-21.

Follow [@DataViking](https://github.com/DataViking-Tech) for launch updates.

## What's coming

- Local vault (Dolt-backed, versioned, SQL-queryable)
- MCP server (stdio): `get_profile_summary`, `search_skills`, `find_story`, `get_philosophy`
- CLI: `traitprint vault add-skill`, `add-story`, `add-experience`, `add-philosophy`
- Resume import with BYOK LLM (Anthropic, OpenAI, Ollama, OpenRouter)
- Optional cloud sync to [traitprint.com](https://traitprint.com)
- MIT licensed

## License

MIT
