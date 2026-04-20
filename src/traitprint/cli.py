"""Click CLI entrypoint for traitprint."""

from __future__ import annotations

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
            click.echo(
                f"{s.name:<25} {s.proficiency:>4}  {s.category:<15} {s.id}"
            )
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
                f"{ed.institution:<30} {ed.degree:<20} "
                f"{ed.field_of_study:<20} {ed.id}"
            )


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
@click.option(
    "--interactive", "-i", is_flag=True, default=True, help="Guided prompts."
)
@click.pass_context
def vault_add_experience(ctx: click.Context, interactive: bool) -> None:
    """Add a work experience to your vault (interactive)."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    title = click.prompt("Job title")
    company = click.prompt("Company")
    start_date = click.prompt("Start date (YYYY-MM)", default="")
    end_date = click.prompt("End date (YYYY-MM, blank for current)", default="")
    description = click.prompt("Description", default="")
    raw_acc = click.prompt(
        "Accomplishments (comma-separated, or blank)", default=""
    )
    accomplishments = (
        [a.strip() for a in raw_acc.split(",") if a.strip()] if raw_acc else []
    )

    exp = store.add_experience(
        title=title,
        company=company,
        start_date=start_date,
        end_date=end_date if end_date else None,
        description=description,
        accomplishments=accomplishments,
    )
    click.echo(f"Added experience: {exp.title} at {exp.company} [{exp.id}]")


# --- vault add-story (interactive, STAR format) ---


@vault.command(name="add-story")
@click.option(
    "--interactive", "-i", is_flag=True, default=True, help="Guided STAR prompts."
)
@click.pass_context
def vault_add_story(ctx: click.Context, interactive: bool) -> None:
    """Add a STAR-format story to your vault (interactive)."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    click.echo("Enter your story in STAR format:")
    title = click.prompt("Title")
    situation = click.prompt("Situation")
    task = click.prompt("Task")
    action = click.prompt("Action")
    result = click.prompt("Result")
    raw_skills = click.prompt(
        "Skill IDs (comma-separated UUIDs, or blank)", default=""
    )
    skill_ids: list[UUID] = []
    if raw_skills:
        for sid in raw_skills.split(","):
            sid = sid.strip()
            if sid:
                skill_ids.append(UUID(sid))
    raw_exp = click.prompt("Experience ID (UUID, or blank)", default="")
    experience_id = UUID(raw_exp) if raw_exp else None

    story = store.add_story(
        title=title,
        situation=situation,
        task=task,
        action=action,
        result=result,
        skill_ids=skill_ids,
        experience_id=experience_id,
    )
    click.echo(f"Added story: {story.title} [{story.id}]")


# --- vault add-philosophy (interactive) ---

_PHILOSOPHY_CATEGORIES = [c.value for c in PhilosophyCategory]


@vault.command(name="add-philosophy")
@click.option(
    "--interactive", "-i", is_flag=True, default=True, help="Guided prompts."
)
@click.pass_context
def vault_add_philosophy(ctx: click.Context, interactive: bool) -> None:
    """Add a work philosophy to your vault (interactive)."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    title = click.prompt("Philosophy title")
    description = click.prompt("Description")
    click.echo(f"Categories: {', '.join(_PHILOSOPHY_CATEGORIES)}")
    category = click.prompt(
        "Category",
        type=click.Choice(_PHILOSOPHY_CATEGORIES, case_sensitive=False),
    )
    raw_evidence = click.prompt(
        "Evidence story IDs (comma-separated UUIDs, or blank)", default=""
    )
    evidence_ids: list[UUID] = []
    if raw_evidence:
        for eid in raw_evidence.split(","):
            eid = eid.strip()
            if eid:
                evidence_ids.append(UUID(eid))

    philosophy = store.add_philosophy(
        title=title,
        description=description,
        category=category,
        evidence_story_ids=evidence_ids,
    )
    click.echo(f"Added philosophy: {philosophy.title} [{philosophy.id}]")


# --- vault add-education (interactive) ---


@vault.command(name="add-education")
@click.option(
    "--interactive", "-i", is_flag=True, default=True, help="Guided prompts."
)
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
    click.echo(
        f"Added education: {edu.degree} at {edu.institution} [{edu.id}]"
    )


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
            f"No vault found at {store.directory}. "
            "Run 'traitprint init' first."
        )
    run_stdio(store)
