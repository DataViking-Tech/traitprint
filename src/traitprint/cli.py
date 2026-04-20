"""Click CLI entrypoint for traitprint."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import click

from traitprint import __version__
from traitprint.git_ops import commit, init_repo
from traitprint.git_ops import diff as git_diff
from traitprint.git_ops import log as git_log
from traitprint.git_ops import rollback as git_rollback
from traitprint.schema import PhilosophyCategory
from traitprint.taxonomy import find_exact, suggest_matches
from traitprint.vault import VaultStore

if TYPE_CHECKING:
    from traitprint.credentials import Credentials
    from traitprint.sync import SyncPlan


def _get_store(ctx: click.Context) -> VaultStore:
    """Retrieve the VaultStore from the Click context."""
    path: str | None = ctx.obj.get("path") if ctx.obj else None
    return VaultStore(path)


@click.group()
@click.version_option(version=__version__, prog_name="traitprint")
@click.option(
    "--path",
    type=click.Path(),
    default=None,
    help="Vault directory (default: ~/.traitprint).",
)
@click.pass_context
def cli(ctx: click.Context, path: str | None) -> None:
    """Traitprint -- local-first career identity vault for the agent era."""
    ctx.ensure_object(dict)
    ctx.obj["path"] = path


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Create a new Traitprint vault."""
    store = _get_store(ctx)
    vault_dir = store.directory

    if store.exists():
        click.echo(f"Vault already exists at {vault_dir}")
        return

    # Create directory
    vault_dir.mkdir(parents=True, exist_ok=True)

    # Write .gitignore
    gitignore = vault_dir / ".gitignore"
    gitignore.write_text(".credentials\n", encoding="utf-8")

    # Initialize git repo
    init_repo(vault_dir)

    # Write empty vault
    vault_obj = store.create_empty()
    store.save(vault_obj)

    # Initial commit
    commit(vault_dir, "traitprint init")

    click.echo(f"Vault initialized at {vault_dir}")


# ------------------------------------------------------------------
# vault command group
# ------------------------------------------------------------------


@cli.group()
def vault() -> None:
    """Manage your career identity vault."""


# --- vault show ---


@vault.command(name="show")
@click.pass_context
def vault_show(ctx: click.Context) -> None:
    """Pretty-print a summary of vault contents."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return
    v = store.load()
    click.echo(
        f"{len(v.skills)} skills, "
        f"{len(v.experiences)} experiences, "
        f"{len(v.stories)} stories, "
        f"{len(v.philosophies)} philosophies, "
        f"{len(v.education)} education"
    )


# --- vault list ---


@vault.command(name="list")
@click.argument(
    "section",
    type=click.Choice(
        ["skills", "experiences", "stories", "philosophies", "education"],
        case_sensitive=False,
    ),
)
@click.pass_context
def vault_list(ctx: click.Context, section: str) -> None:
    """List items in a vault section (table format)."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return
    v = store.load()
    items = getattr(v, section)

    if not items:
        click.echo(f"No {section} found.")
        return

    if section == "skills":
        click.echo(f"{'Name':<25} {'Prof':>4}  {'Category':<15} {'ID'}")
        click.echo("-" * 80)
        for s in items:
            click.echo(f"{s.name:<25} {s.proficiency:>4}  {s.category:<15} {s.id}")
    elif section == "experiences":
        click.echo(f"{'Title':<30} {'Company':<20} {'Dates':<15} {'ID'}")
        click.echo("-" * 80)
        for e in items:
            dates = f"{e.start_date}-{e.end_date}" if e.end_date else e.start_date
            click.echo(f"{e.title:<30} {e.company:<20} {dates:<15} {e.id}")
    elif section == "stories":
        click.echo(f"{'Title':<40} {'Skills':>6}  {'ID'}")
        click.echo("-" * 80)
        for s in items:
            click.echo(f"{s.title:<40} {len(s.skill_ids):>6}  {s.id}")
    elif section == "philosophies":
        click.echo(f"{'Title':<35} {'Category':<20} {'ID'}")
        click.echo("-" * 80)
        for p in items:
            click.echo(f"{p.title:<35} {p.category.value:<20} {p.id}")
    elif section == "education":
        click.echo(f"{'Institution':<30} {'Degree':<20} {'Field':<20} {'ID'}")
        click.echo("-" * 80)
        for ed in items:
            click.echo(
                f"{ed.institution:<30} {ed.degree:<20} {ed.field_of_study:<20} {ed.id}"
            )


# --- vault set-profile ---


@vault.command(name="set-profile")
@click.option("--name", "display_name", default=None, help="Display name.")
@click.option("--headline", default=None, help="Short professional headline.")
@click.option("--summary", default=None, help="Longer professional summary.")
@click.option("--location", default=None, help="Location (e.g. city, country).")
@click.option("--email", "contact_email", default=None, help="Contact email.")
@click.pass_context
def vault_set_profile(
    ctx: click.Context,
    display_name: str | None,
    headline: str | None,
    summary: str | None,
    location: str | None,
    contact_email: str | None,
) -> None:
    """Set profile fields on the vault.

    Only fields you pass are updated; omitted fields keep their current
    values. Pass an empty string to clear a field.
    """
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    if all(
        v is None for v in (display_name, headline, summary, location, contact_email)
    ):
        click.echo(
            "No fields provided. Pass at least one of "
            "--name, --headline, --summary, --location, --email."
        )
        ctx.exit(1)
        return

    profile = store.set_profile(
        display_name=display_name,
        headline=headline,
        summary=summary,
        location=location,
        contact_email=contact_email,
    )
    click.echo("Updated profile:")
    click.echo(f"  display_name:  {profile.display_name}")
    click.echo(f"  headline:      {profile.headline}")
    click.echo(f"  summary:       {profile.summary}")
    click.echo(f"  location:      {profile.location}")
    click.echo(f"  contact_email: {profile.contact_email}")


# --- vault add-skill ---


@vault.command(name="add-skill")
@click.argument("name")
@click.option(
    "--proficiency",
    "-p",
    type=click.IntRange(1, 10),
    required=True,
    help="Proficiency level (1-10).",
)
@click.option(
    "--category",
    "-c",
    required=True,
    help="Skill category (e.g. technical, soft, domain, tool).",
)
@click.option("--notes", "-n", default=None, help="Optional notes about the skill.")
@click.pass_context
def vault_add_skill(
    ctx: click.Context,
    name: str,
    proficiency: int,
    category: str,
    notes: str | None,
) -> None:
    """Add a skill to your vault."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    # Taxonomy integration
    taxonomy_id = None
    exact = find_exact(name)
    if exact:
        taxonomy_id = exact.id
        click.echo(f"Matched taxonomy: {exact.name} ({exact.category})")
    else:
        suggestions = suggest_matches(name)
        if suggestions:
            names = ", ".join(s.name for s in suggestions)
            click.echo(f"Did you mean: {names}?")
            click.echo("Adding as custom skill (no taxonomy match).")

    skill = store.add_skill(
        name=name,
        proficiency=proficiency,
        category=category,
        notes=notes,
        taxonomy_id=taxonomy_id,
    )
    click.echo(f"Added skill: {skill.name} ({skill.proficiency}/10) [{skill.id}]")


# --- vault add-experience (interactive) ---


@vault.command(name="add-experience")
@click.option("--title", default=None, help="Job title.")
@click.option("--company", default=None, help="Company name.")
@click.option("--start-date", default=None, help="Start date (YYYY-MM).")
@click.option(
    "--end-date", default=None, help="End date (YYYY-MM); blank/omitted means current."
)
@click.option("--description", default=None, help="Description of the role.")
@click.option(
    "--accomplishment",
    "accomplishments",
    multiple=True,
    help="An accomplishment line (repeatable).",
)
@click.option("--interactive", "-i", is_flag=True, default=True, help="Guided prompts.")
@click.pass_context
def vault_add_experience(
    ctx: click.Context,
    title: str | None,
    company: str | None,
    start_date: str | None,
    end_date: str | None,
    description: str | None,
    accomplishments: tuple[str, ...],
    interactive: bool,
) -> None:
    """Add a work experience to your vault.

    Pass --title, --company, --start-date (and optional fields) for
    non-interactive use. Any missing required field will be prompted for.
    """
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    non_interactive = title is not None
    if non_interactive:
        company = company if company is not None else ""
        start_date = start_date if start_date is not None else ""
        end_date_val = end_date if end_date else None
        description = description if description is not None else ""
        accomplishment_list = [a for a in accomplishments if a]
    else:
        title = click.prompt("Job title")
        company = click.prompt("Company")
        start_date = click.prompt("Start date (YYYY-MM)", default="")
        end_date_prompt = click.prompt(
            "End date (YYYY-MM, blank for current)", default=""
        )
        end_date_val = end_date_prompt if end_date_prompt else None
        description = click.prompt("Description", default="")
        if accomplishments:
            accomplishment_list = [a for a in accomplishments if a]
        else:
            raw_acc = click.prompt(
                "Accomplishments (comma-separated, or blank)", default=""
            )
            accomplishment_list = (
                [a.strip() for a in raw_acc.split(",") if a.strip()] if raw_acc else []
            )

    assert title is not None
    exp = store.add_experience(
        title=title,
        company=company or "",
        start_date=start_date or "",
        end_date=end_date_val,
        description=description or "",
        accomplishments=accomplishment_list,
    )
    click.echo(f"Added experience: {exp.title} at {exp.company} [{exp.id}]")


# --- vault add-story (interactive, STAR format) ---


@vault.command(name="add-story")
@click.option("--title", default=None, help="Story title.")
@click.option("--situation", default=None, help="STAR: situation.")
@click.option("--task", default=None, help="STAR: task.")
@click.option("--action", default=None, help="STAR: action.")
@click.option("--result", default=None, help="STAR: result.")
@click.option(
    "--skill-id",
    "skill_ids_opt",
    multiple=True,
    help="Skill UUID (repeatable).",
)
@click.option(
    "--experience-id", default=None, help="Experience UUID this story belongs to."
)
@click.option(
    "--interactive", "-i", is_flag=True, default=True, help="Guided STAR prompts."
)
@click.pass_context
def vault_add_story(
    ctx: click.Context,
    title: str | None,
    situation: str | None,
    task: str | None,
    action: str | None,
    result: str | None,
    skill_ids_opt: tuple[str, ...],
    experience_id: str | None,
    interactive: bool,
) -> None:
    """Add a STAR-format story to your vault.

    Pass --title and STAR fields for non-interactive use. Any missing field
    will be prompted for when --title is omitted.
    """
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    def _parse_uuids(raw: tuple[str, ...]) -> list[UUID]:
        return [UUID(s.strip()) for s in raw if s and s.strip()]

    non_interactive = title is not None
    if non_interactive:
        situation = situation if situation is not None else ""
        task = task if task is not None else ""
        action = action if action is not None else ""
        result = result if result is not None else ""
        skill_ids = _parse_uuids(skill_ids_opt)
        experience_uuid = UUID(experience_id) if experience_id else None
    else:
        click.echo("Enter your story in STAR format:")
        title = click.prompt("Title")
        situation = click.prompt("Situation")
        task = click.prompt("Task")
        action = click.prompt("Action")
        result = click.prompt("Result")
        if skill_ids_opt:
            skill_ids = _parse_uuids(skill_ids_opt)
        else:
            raw_skills = click.prompt(
                "Skill IDs (comma-separated UUIDs, or blank)", default=""
            )
            skill_ids = []
            if raw_skills:
                for sid in raw_skills.split(","):
                    sid = sid.strip()
                    if sid:
                        skill_ids.append(UUID(sid))
        if experience_id:
            experience_uuid = UUID(experience_id)
        else:
            raw_exp = click.prompt("Experience ID (UUID, or blank)", default="")
            experience_uuid = UUID(raw_exp) if raw_exp else None

    assert title is not None
    story = store.add_story(
        title=title,
        situation=situation or "",
        task=task or "",
        action=action or "",
        result=result or "",
        skill_ids=skill_ids,
        experience_id=experience_uuid,
    )
    click.echo(f"Added story: {story.title} [{story.id}]")


# --- vault add-philosophy (interactive) ---

_PHILOSOPHY_CATEGORIES = [c.value for c in PhilosophyCategory]


@vault.command(name="add-philosophy")
@click.option("--title", default=None, help="Philosophy title.")
@click.option("--description", default=None, help="Description of the philosophy.")
@click.option(
    "--category",
    default=None,
    type=click.Choice(_PHILOSOPHY_CATEGORIES, case_sensitive=False),
    help="Philosophy category.",
)
@click.option(
    "--evidence-id",
    "evidence_ids_opt",
    multiple=True,
    help="Evidence story UUID (repeatable).",
)
@click.option("--interactive", "-i", is_flag=True, default=True, help="Guided prompts.")
@click.pass_context
def vault_add_philosophy(
    ctx: click.Context,
    title: str | None,
    description: str | None,
    category: str | None,
    evidence_ids_opt: tuple[str, ...],
    interactive: bool,
) -> None:
    """Add a work philosophy to your vault.

    Pass --title, --description, --category for non-interactive use.
    """
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    non_interactive = title is not None
    if non_interactive:
        description = description if description is not None else ""
        if category is None:
            click.echo(
                "--category is required when --title is provided. "
                f"Choices: {', '.join(_PHILOSOPHY_CATEGORIES)}"
            )
            ctx.exit(1)
            return
        evidence_ids = [UUID(s.strip()) for s in evidence_ids_opt if s and s.strip()]
    else:
        title = click.prompt("Philosophy title")
        description = click.prompt("Description")
        click.echo(f"Categories: {', '.join(_PHILOSOPHY_CATEGORIES)}")
        category = click.prompt(
            "Category",
            type=click.Choice(_PHILOSOPHY_CATEGORIES, case_sensitive=False),
        )
        if evidence_ids_opt:
            evidence_ids = [
                UUID(s.strip()) for s in evidence_ids_opt if s and s.strip()
            ]
        else:
            raw_evidence = click.prompt(
                "Evidence story IDs (comma-separated UUIDs, or blank)", default=""
            )
            evidence_ids = []
            if raw_evidence:
                for eid in raw_evidence.split(","):
                    eid = eid.strip()
                    if eid:
                        evidence_ids.append(UUID(eid))

    assert title is not None
    assert category is not None
    philosophy = store.add_philosophy(
        title=title,
        description=description or "",
        category=category,
        evidence_story_ids=evidence_ids,
    )
    click.echo(f"Added philosophy: {philosophy.title} [{philosophy.id}]")


# --- vault add-education (interactive) ---


@vault.command(name="add-education")
@click.option("--interactive", "-i", is_flag=True, default=True, help="Guided prompts.")
@click.pass_context
def vault_add_education(ctx: click.Context, interactive: bool) -> None:
    """Add an education entry to your vault (interactive)."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    institution = click.prompt("Institution")
    degree = click.prompt("Degree (e.g. Bachelor, Master, PhD)", default="")
    field_of_study = click.prompt("Field of study", default="")
    start_date = click.prompt("Start date (YYYY)", default="")
    end_date = click.prompt("End date (YYYY, blank for current)", default="")
    description = click.prompt("Description", default="")

    edu = store.add_education(
        institution=institution,
        degree=degree,
        field_of_study=field_of_study,
        start_date=start_date,
        end_date=end_date if end_date else None,
        description=description,
    )
    click.echo(f"Added education: {edu.degree} at {edu.institution} [{edu.id}]")


# --- vault remove ---


@vault.command(name="remove")
@click.argument("item_id")
@click.option(
    "--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt."
)
@click.pass_context
def vault_remove(ctx: click.Context, item_id: str, yes: bool) -> None:
    """Remove an item from the vault by UUID."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    try:
        uid = UUID(item_id)
    except ValueError:
        click.echo(f"Invalid UUID: {item_id}")
        return

    if not yes and not click.confirm(f"Remove item {uid}?"):
        click.echo("Cancelled.")
        return

    # Search all sections for the ID
    from traitprint.vault import SECTIONS

    for section in SECTIONS:
        if store.remove_item(section, uid):
            click.echo(f"Removed from {section}: {uid}")
            return

    click.echo(f"Item not found: {uid}")


# --- vault export ---


@vault.command(name="export")
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(
        ["json", "markdown", "jsonresume", "synthpanel-persona"],
        case_sensitive=False,
    ),
    default="json",
    show_default=True,
    help="Export format.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Write to file instead of stdout.",
)
@click.pass_context
def vault_export(ctx: click.Context, fmt: str, output: str | None) -> None:
    """Export the vault as JSON, Markdown, JSON Resume, or a SynthPanel persona."""
    from traitprint.export import export_vault

    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return
    v = store.load()
    rendered = export_vault(v, fmt.lower())
    if output:
        from pathlib import Path

        Path(output).write_text(rendered, encoding="utf-8")
        click.echo(f"Wrote {fmt} export to {output}")
    else:
        click.echo(rendered, nl=False)


# --- vault history ---


@vault.command(name="history")
@click.option(
    "--count", "-n", default=10, help="Number of entries to show.", show_default=True
)
@click.pass_context
def vault_history(ctx: click.Context, count: int) -> None:
    """Show vault git history."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    entries = git_log(store.directory, n=count)
    if not entries:
        click.echo("No history found.")
        return
    for entry in entries:
        click.echo(entry)


# --- vault diff ---


@vault.command(name="diff")
@click.pass_context
def vault_diff_cmd(ctx: click.Context) -> None:
    """Show changes since the last commit."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    changes = git_diff(store.directory)
    if not changes:
        click.echo("No changes since last commit.")
        return
    click.echo(changes)


# --- vault rollback ---


@vault.command(name="rollback")
@click.option(
    "--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt."
)
@click.pass_context
def vault_rollback_cmd(ctx: click.Context, yes: bool) -> None:
    """Roll back vault to the previous commit."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    if not yes and not click.confirm("Roll back vault to previous state?"):
        click.echo("Cancelled.")
        return

    git_rollback(store.directory)
    click.echo("Vault rolled back to previous state.")


# --- vault import-resume ---


@vault.command(name="import-resume")
@click.argument(
    "path",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=str),
)
@click.option(
    "--provider",
    type=click.Choice(
        ["anthropic", "openai", "ollama", "openrouter"], case_sensitive=False
    ),
    default=None,
    help="LLM provider (auto-detected from env if omitted).",
)
@click.option(
    "--model", default=None, help="Override the default model for the provider."
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Skip the confirmation prompt and import everything.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would be imported without modifying the vault.",
)
@click.pass_context
def vault_import_resume(
    ctx: click.Context,
    path: str,
    provider: str | None,
    model: str | None,
    yes: bool,
    dry_run: bool,
) -> None:
    """Import a resume via a BYOK LLM provider.

    Extracts profile, skills, experiences, and education from PDF, DOCX,
    TXT, or MD. Shows a summary and asks for confirmation before writing
    to the vault.
    """
    from pathlib import Path as _Path

    from traitprint.mining import ResumeExtractionError, resume_to_draft
    from traitprint.providers import (
        LLMError,
        ProviderNotConfigured,
        detect_provider,
    )

    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        ctx.exit(1)
        return

    try:
        llm = detect_provider(preferred=provider, model=model)
    except ProviderNotConfigured as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"Using provider: {llm.name} (model: {llm.model})")
    click.echo(f"Reading resume: {path}")

    try:
        draft = resume_to_draft(_Path(path), llm)
    except ResumeExtractionError as exc:
        raise click.ClickException(f"Resume extraction failed: {exc}") from exc
    except LLMError as exc:
        raise click.ClickException(f"LLM extraction failed: {exc}") from exc

    click.echo("")
    click.echo("Extracted:")
    for line in draft.summary_lines():
        click.echo("  " + line)

    if draft.usage is not None:
        usage = draft.usage
        click.echo("")
        click.echo(
            f"Usage: {usage.input_tokens} input + {usage.output_tokens} "
            f"output tokens (~${usage.cost_usd:.4f})"
        )

    if dry_run:
        click.echo("")
        click.echo("Dry run — vault not modified.")
        return

    if not yes:
        click.echo("")
        if not click.confirm("Import these items into your vault?", default=True):
            click.echo("Cancelled — vault unchanged.")
            return

    counts = store.import_from_draft(
        profile=draft.profile,
        skills=draft.skills,
        experiences=draft.experiences,
        education=draft.education,
        commit_message=f"Import resume: {_Path(path).name}",
    )

    summary = ", ".join(f"{v} {k}" for k, v in counts.items()) or "no new items"
    click.echo(f"Imported {summary}. Run 'traitprint vault show' to review.")


# --- export ---


_EXPORT_FORMATS = ["synthpanel-persona"]


@cli.command(name="export")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(_EXPORT_FORMATS, case_sensitive=False),
    required=True,
    help="Export target format.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write to file instead of stdout.",
)
@click.option(
    "--pack-name",
    default=None,
    help="Override the pack name (synthpanel-persona only).",
)
@click.pass_context
def export_cmd(
    ctx: click.Context,
    fmt: str,
    output: str | None,
    pack_name: str | None,
) -> None:
    """Export the vault in a format consumable by other tools.

    ``--format synthpanel-persona`` emits a SynthPanel persona pack
    (YAML) that can be fed directly to ``synthpanel panel run
    --personas <file>``.
    """
    store = _get_store(ctx)
    if not store.exists():
        raise click.ClickException(
            f"No vault found at {store.directory}. Run 'traitprint init' first."
        )

    vault_obj = store.load()

    if fmt == "synthpanel-persona":
        import yaml

        from traitprint.exporters.synthpanel import vault_to_synthpanel_pack

        pack = vault_to_synthpanel_pack(vault_obj, pack_name=pack_name)
        payload = yaml.safe_dump(pack, sort_keys=False, default_flow_style=False)
    else:  # pragma: no cover — click.Choice guards this
        raise click.ClickException(f"Unsupported format: {fmt}")

    if output:
        from pathlib import Path

        Path(output).write_text(payload, encoding="utf-8")
        click.echo(f"Wrote {fmt} export to {output}")
    else:
        click.echo(payload, nl=False)


# --- mcp-serve ---


@cli.command(name="mcp-serve")
@click.pass_context
def mcp_serve(ctx: click.Context) -> None:
    """Run the Traitprint MCP server over stdio.

    Exposes four tools (get_profile_summary, search_skills, find_story,
    get_philosophy) with response schemas that mirror the cloud MCP
    server so agents can swap local ↔ cloud by changing a URL.
    """
    from traitprint.mcp_server import run_stdio

    store = _get_store(ctx)
    if not store.exists():
        raise click.ClickException(
            f"No vault found at {store.directory}. Run 'traitprint init' first."
        )
    run_stdio(store)


# ------------------------------------------------------------------
# Cloud sync: login / logout / push / pull
# ------------------------------------------------------------------


def _require_credentials(store: VaultStore) -> Credentials:
    from traitprint.credentials import CredentialsStore

    creds = CredentialsStore(store.directory).load()
    if creds is None or not creds.token:
        raise click.ClickException("Not logged in. Run 'traitprint login' first.")
    return creds


def _render_plan(plan: SyncPlan) -> str:
    return f"[{plan.direction}] {plan.reason}"


@cli.command(name="login")
@click.option("--email", "-e", prompt=True, help="Your Traitprint account email.")
@click.option(
    "--password",
    "-p",
    prompt=True,
    hide_input=True,
    help="Your Traitprint account password.",
)
@click.option(
    "--api-url",
    default=None,
    help="Override the cloud API URL (default: https://traitprint.com).",
)
@click.pass_context
def login_cmd(
    ctx: click.Context, email: str, password: str, api_url: str | None
) -> None:
    """Log in to Traitprint cloud and save a bearer token to .credentials."""
    from traitprint.cloud import AuthError, CloudClient, CloudError
    from traitprint.credentials import DEFAULT_API_URL, CredentialsStore

    store = _get_store(ctx)
    if not store.exists():
        raise click.ClickException(
            f"No vault found at {store.directory}. Run 'traitprint init' first."
        )

    with CloudClient(api_url or DEFAULT_API_URL) as client:
        try:
            creds = client.login(email, password)
        except AuthError as exc:
            raise click.ClickException(str(exc)) from exc
        except CloudError as exc:
            raise click.ClickException(str(exc)) from exc

    CredentialsStore(store.directory).save(creds)
    click.echo(f"Logged in as {creds.email}.")


@cli.command(name="logout")
@click.pass_context
def logout_cmd(ctx: click.Context) -> None:
    """Remove saved cloud credentials from the vault directory."""
    from traitprint.credentials import CredentialsStore

    store = _get_store(ctx)
    removed = CredentialsStore(store.directory).delete()
    click.echo("Logged out." if removed else "No credentials to remove.")


@cli.command(name="push")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would happen without uploading.",
)
@click.pass_context
def push_cmd(ctx: click.Context, dry_run: bool) -> None:
    """Upload the local vault to Traitprint cloud (last-write-wins)."""
    from traitprint.cloud import AuthError, CloudClient, CloudError, ConflictError
    from traitprint.sync import do_push

    store = _get_store(ctx)
    if not store.exists():
        raise click.ClickException(
            f"No vault found at {store.directory}. Run 'traitprint init' first."
        )
    creds = _require_credentials(store)

    with CloudClient.from_credentials(creds) as client:
        try:
            plan, result = do_push(store, client, dry_run=dry_run)
        except ConflictError as exc:
            raise click.ClickException(str(exc)) from exc
        except AuthError as exc:
            raise click.ClickException(str(exc)) from exc
        except CloudError as exc:
            raise click.ClickException(str(exc)) from exc

    click.echo(_render_plan(plan))
    if dry_run:
        click.echo("Dry run: no data was uploaded.")
        return
    if plan.direction == "conflict":
        raise click.ClickException(plan.reason)
    if result is not None and result.accepted:
        click.echo("Push complete.")


@cli.command(name="pull")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show what would happen without writing to disk.",
)
@click.pass_context
def pull_cmd(ctx: click.Context, dry_run: bool) -> None:
    """Download the cloud vault to local disk (last-write-wins)."""
    from traitprint.cloud import AuthError, CloudClient, CloudError
    from traitprint.sync import do_pull

    store = _get_store(ctx)
    if not store.exists():
        # Allow pull even before 'init' only if a vault dir exists to hold
        # credentials. Simplest: require init first.
        raise click.ClickException(
            f"No vault found at {store.directory}. Run 'traitprint init' first."
        )
    creds = _require_credentials(store)

    with CloudClient.from_credentials(creds) as client:
        try:
            plan, _ = do_pull(store, client, dry_run=dry_run)
        except AuthError as exc:
            raise click.ClickException(str(exc)) from exc
        except CloudError as exc:
            raise click.ClickException(str(exc)) from exc

    click.echo(_render_plan(plan))
    if dry_run:
        click.echo("Dry run: no local changes were written.")
        return
    if plan.direction == "conflict":
        raise click.ClickException(plan.reason)
    if plan.direction == "pull":
        click.echo("Pull complete.")
