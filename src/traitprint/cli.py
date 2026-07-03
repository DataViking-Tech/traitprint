"""Click CLI entrypoint for traitprint."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any
from uuid import UUID

import click
from pydantic import ValidationError

from traitprint import __version__
from traitprint.git_ops import commit, head_sha, init_repo, rev_sha
from traitprint.git_ops import diff as git_diff
from traitprint.git_ops import log as git_log
from traitprint.git_ops import rollback as git_rollback
from traitprint.proposals import (
    PROPOSAL_KINDS,
    LoadedProposal,
    ProposalFileIssue,
)
from traitprint.schema import PhilosophyCategory, ProfileLink, VaultSchema
from traitprint.taxonomy import find_exact, suggest_matches
from traitprint.vault import DuplicateSkillError, VaultStore

if TYPE_CHECKING:
    from traitprint.credentials import Credentials
    from traitprint.gitsync import GitSyncClient, IngestReport
    from traitprint.sync import SyncPlan


if TYPE_CHECKING:
    # ParamType only became generic (subscriptable) in click 8.4; pyproject
    # supports click>=8.0, where a runtime subscript raises TypeError on
    # import and bricks the CLI. Subscript for the type checker only.
    _UUIDParamBase = click.ParamType[UUID]
else:
    _UUIDParamBase = click.ParamType


class UUIDParamType(_UUIDParamBase):
    """Click param type for UUID flags.

    Produces the same actionable style as batch mode ("invalid UUID 'x'")
    instead of a raw ValueError traceback, with click's usage-error
    exit code 2.
    """

    name = "uuid"

    def convert(
        self,
        value: Any,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> UUID:
        if isinstance(value, UUID):
            return value
        try:
            return UUID(str(value).strip())
        except ValueError:
            self.fail(
                f"invalid UUID {str(value)!r} — copy real UUIDs from "
                "'traitprint vault list' output",
                param,
                ctx,
            )


UUID_PARAM = UUIDParamType()


def _get_store(ctx: click.Context) -> VaultStore:
    """Retrieve the VaultStore from the Click context."""
    path: str | None = ctx.obj.get("path") if ctx.obj else None
    return VaultStore(path)


def _read_json_items(source: IO[str]) -> list[dict[str, Any]]:
    """Parse a JSON array-of-objects from an open text stream.

    Raises click.ClickException if the payload is not a JSON array of objects.
    """
    try:
        data = json.loads(source.read())
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise click.ClickException(f"Expected a JSON array, got {type(data).__name__}")
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise click.ClickException(
                f"Item {i}: expected an object, got {type(item).__name__}"
            )
    return data


def _validation_problems(exc: ValidationError) -> list[str]:
    """Normalize a pydantic error into ``field: message`` lines.

    Batch output must never leak raw pydantic error dumps or
    errors.pydantic.dev URLs; this keeps the ``[err] <name>: field:
    message`` style intact.
    """
    problems: list[str] = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(part) for part in err.get("loc", ())) or "value"
        msg = str(err.get("msg", "invalid value"))
        msg = msg.removeprefix("Value error, ")
        problems.append(f"{loc}: {msg}")
    return problems or ["invalid value"]


def _parse_uuid_list(raw: object, field: str, item_index: int) -> list[UUID]:
    """Parse a list-of-UUID field from a JSON item. Returns [] for missing/None."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be a list of UUID strings")
    out: list[UUID] = []
    for v in raw:
        if not isinstance(v, str):
            raise ValueError(f"{field}: expected UUID string, got {type(v).__name__}")
        try:
            out.append(UUID(v))
        except ValueError as exc:
            raise ValueError(f"{field}: invalid UUID {v!r}: {exc}") from exc
    return out


@click.group()
@click.version_option(version=__version__, prog_name="traitprint")
@click.option(
    "--vault-dir",
    "--path",
    "path",
    type=click.Path(),
    default=None,
    help=(
        "Vault directory. Overrides $TRAITPRINT_VAULT_DIR and "
        "any .traitprint/ found by walking up from the current directory. "
        "Falls back to ~/.traitprint."
    ),
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
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="Show full vault contents including all fields, timestamps, and git metadata.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the full vault (every entity, all fields) as JSON.",
)
@click.pass_context
def vault_show(ctx: click.Context, verbose: bool, as_json: bool) -> None:
    """Pretty-print a summary of vault contents (JSON with --json)."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return
    v = store.load()
    if as_json:
        click.echo(json.dumps(v.model_dump(mode="json"), indent=2))
        return
    if not verbose:
        _render_vault_summary(v)
        return

    _render_vault_verbose(store, v)


def _render_vault_summary(v: VaultSchema) -> None:
    """Render a concise summary of the vault — git-status style."""
    from traitprint.audit import compute_phase, freshness_findings

    # Profile header
    p = v.profile
    if p.display_name or p.headline:
        header = p.display_name or ""
        if p.headline:
            header = f"{header} — {p.headline}" if header else p.headline
        click.echo(header)
    if p.location:
        click.echo(f"Location: {p.location}")
    if p.display_name or p.headline or p.location:
        click.echo("")

    # Phase + top staleness flags (see 'traitprint doctor' for the full view)
    phase = compute_phase(v)
    click.echo(f"Phase: {phase.phase} — {phase.reason}")
    stale = freshness_findings(v)
    for f in stale[:2]:
        click.echo(f"  ! {f.message}")
    if len(stale) > 2:
        click.echo(f"  ... {len(stale) - 2} more — run 'traitprint doctor'.")
    click.echo("")

    # Skills: top 5 by proficiency
    click.echo(f"Skills ({len(v.skills)}):")
    top_skills = sorted(v.skills, key=lambda s: (-s.proficiency, s.name.lower()))[:5]
    for s in top_skills:
        click.echo(f"  - {s.name} ({s.proficiency}/5)")
    if len(v.skills) > 5:
        click.echo(f"  ... {len(v.skills) - 5} more")

    # Experiences: title @ company, dates
    click.echo("")
    click.echo(f"Experiences ({len(v.experiences)}):")
    for e in v.experiences:
        header = e.title
        if e.company:
            header += f" @ {e.company}"
        dates = f"{e.start_date or '?'} — {e.end_date or 'present'}"
        click.echo(f"  - {header} ({dates})")

    # Stories: titles
    click.echo("")
    click.echo(f"Stories ({len(v.stories)}):")
    for st in v.stories:
        click.echo(f"  - {st.title}")

    # Philosophies: grouped by category
    click.echo("")
    if v.philosophies:
        counts: dict[str, int] = {}
        for ph in v.philosophies:
            cat = ph.category or "uncategorized"
            counts[cat] = counts.get(cat, 0) + 1
        parts = ", ".join(f"{cat} ({n})" for cat, n in sorted(counts.items()))
        click.echo(f"Philosophies ({len(v.philosophies)}): {parts}")
    else:
        click.echo("Philosophies (0)")

    # Education: degree/field @ institution
    click.echo("")
    click.echo(f"Education ({len(v.education)}):")
    for ed in v.education:
        bits = " ".join(x for x in (ed.degree, ed.field_of_study) if x)
        header = f"{bits} @ {ed.institution}" if bits else ed.institution
        click.echo(f"  - {header}")

    click.echo("")
    click.echo("Run 'traitprint vault show --verbose' for full details.")


def _render_vault_verbose(store: VaultStore, v: VaultSchema) -> None:
    """Render a full, human-readable dump of the vault."""
    click.echo(f"Vault: {store.directory}")
    click.echo(f"Schema version: {v.schema_version}")
    click.echo(f"Updated at: {v.updated_at.isoformat()}")

    # Profile
    p = v.profile
    click.echo("")
    click.echo("Profile:")
    click.echo(f"  Display name:  {p.display_name or '-'}")
    click.echo(f"  Headline:      {p.headline or '-'}")
    click.echo(f"  Summary:       {p.summary or '-'}")
    click.echo(f"  Location:      {p.location or '-'}")
    click.echo(f"  Contact email: {p.contact_email or '-'}")

    # Skills
    click.echo("")
    click.echo(f"Skills ({len(v.skills)}):")
    for s in v.skills:
        click.echo(f"  - {s.name}  [id={s.id}]")
        click.echo(f"      category:    {s.category or '-'}")
        click.echo(f"      proficiency: {s.proficiency}/5")
        click.echo(f"      source:      {s.source}")
        click.echo(f"      taxonomy_id: {s.taxonomy_id or '-'}")
        if s.notes:
            click.echo(f"      notes:       {s.notes}")
        click.echo(f"      created_at:  {s.created_at.isoformat()}")
        click.echo(f"      updated_at:  {s.updated_at.isoformat()}")

    # Experiences
    click.echo("")
    click.echo(f"Experiences ({len(v.experiences)}):")
    for e in v.experiences:
        header = f"{e.title}"
        if e.company:
            header += f" @ {e.company}"
        click.echo(f"  - {header}  [id={e.id}]")
        dates = f"{e.start_date or '?'} — {e.end_date or 'present'}"
        click.echo(f"      dates:       {dates}")
        if e.description:
            click.echo(f"      description: {e.description}")
        if e.accomplishments:
            click.echo("      accomplishments:")
            for a in e.accomplishments:
                click.echo(f"        - {a}")
        if e.skill_ids:
            click.echo(f"      skill_ids:   {', '.join(str(x) for x in e.skill_ids)}")
        click.echo(f"      source:      {e.source}")
        click.echo(f"      created_at:  {e.created_at.isoformat()}")
        click.echo(f"      updated_at:  {e.updated_at.isoformat()}")

    # Stories
    click.echo("")
    click.echo(f"Stories ({len(v.stories)}):")
    for st in v.stories:
        click.echo(f"  - {st.title}  [id={st.id}]")
        if st.situation:
            click.echo(f"      situation: {st.situation}")
        if st.task:
            click.echo(f"      task:      {st.task}")
        if st.action:
            click.echo(f"      action:    {st.action}")
        if st.result:
            click.echo(f"      result:    {st.result}")
        if st.lesson:
            click.echo(f"      lesson:    {st.lesson}")
        if st.outcome:
            click.echo(f"      outcome:   {st.outcome}")
        if st.theme_tags:
            click.echo(f"      theme_tags: {', '.join(st.theme_tags)}")
        if st.skill_ids:
            click.echo(f"      skill_ids: {', '.join(str(x) for x in st.skill_ids)}")
        if st.experience_id:
            click.echo(f"      experience_id: {st.experience_id}")
        click.echo(f"      source:      {st.source}")
        click.echo(f"      created_at:  {st.created_at.isoformat()}")
        click.echo(f"      updated_at:  {st.updated_at.isoformat()}")

    # Philosophies
    click.echo("")
    click.echo(f"Philosophies ({len(v.philosophies)}):")
    for ph in v.philosophies:
        click.echo(f"  - {ph.title}  [id={ph.id}]")
        click.echo(f"      category:    {ph.category or '-'}")
        if ph.description:
            click.echo(f"      description: {ph.description}")
        if ph.evidence_story_ids:
            ids = ", ".join(str(x) for x in ph.evidence_story_ids)
            click.echo(f"      evidence_story_ids: {ids}")
        click.echo(f"      source:      {ph.source}")
        click.echo(f"      created_at:  {ph.created_at.isoformat()}")
        click.echo(f"      updated_at:  {ph.updated_at.isoformat()}")

    # Education
    click.echo("")
    click.echo(f"Education ({len(v.education)}):")
    for ed in v.education:
        header = ed.institution
        if ed.degree or ed.field_of_study:
            bits = " ".join(x for x in (ed.degree, ed.field_of_study) if x)
            header = f"{bits} @ {ed.institution}" if bits else ed.institution
        click.echo(f"  - {header}  [id={ed.id}]")
        dates = f"{ed.start_date or '?'} — {ed.end_date or 'present'}"
        click.echo(f"      dates:       {dates}")
        if ed.description:
            click.echo(f"      description: {ed.description}")

    # Git metadata
    click.echo("")
    click.echo("Git:")
    sha = head_sha(store.directory)
    click.echo(f"  HEAD: {sha or '(no commits)'}")
    recent = git_log(store.directory, n=5)
    if recent:
        click.echo("  Recent commits:")
        for line in recent:
            click.echo(f"    {line}")


# --- vault list ---


_LIST_ITEM_TYPE = {
    "skills": "skill",
    "experiences": "experience",
    "stories": "story",
    "philosophies": "philosophy",
    "education": "education",
}


@vault.command(name="list")
@click.argument(
    "section",
    type=click.Choice(
        ["skills", "experiences", "stories", "philosophies", "education"],
        case_sensitive=False,
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a JSON array of {id, type, name|title} objects.",
)
@click.pass_context
def vault_list(ctx: click.Context, section: str, as_json: bool) -> None:
    """List items in a vault section (table format, or JSON with --json)."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return
    v = store.load()
    items = getattr(v, section)

    if as_json:
        rows: list[dict[str, str]] = []
        for item in items:
            row = {"id": str(item.id), "type": _LIST_ITEM_TYPE[section]}
            if section == "skills":
                row["name"] = item.name
            elif section == "education":
                row["title"] = item.institution
            else:
                row["title"] = item.title
            rows.append(row)
        click.echo(json.dumps(rows, indent=2))
        return

    if not items:
        click.echo(f"No {section} found.")
        return

    if section == "skills":
        click.echo(f"{'Name':<25} {'Prof':>4}  {'Category':<15} {'ID'}")
        click.echo("-" * 80)
        for s in items:
            click.echo(f"{s.name:<25} {s.proficiency:>4}  {s.category:<15} {s.id}")
    elif section == "experiences":
        click.echo(f"{'Title':<30} {'Company':<20} {'Dates':<15} {'Skills':>6}  {'ID'}")
        click.echo("-" * 80)
        for e in items:
            dates = f"{e.start_date}-{e.end_date}" if e.end_date else e.start_date
            click.echo(
                f"{e.title:<30} {e.company:<20} {dates:<15} "
                f"{len(e.skill_ids):>6}  {e.id}"
            )
    elif section == "stories":
        click.echo(f"{'Title':<40} {'Skills':>6}  {'ID'}")
        click.echo("-" * 80)
        for s in items:
            click.echo(f"{s.title:<40} {len(s.skill_ids):>6}  {s.id}")
    elif section == "philosophies":
        click.echo(f"{'Title':<35} {'Category':<20} {'ID'}")
        click.echo("-" * 80)
        for p in items:
            click.echo(f"{p.title:<35} {p.category:<20} {p.id}")
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
@click.option("--phone", default=None, help="Contact phone number.")
@click.option("--url", default=None, help="Personal website / portfolio URL.")
@click.option(
    "--link",
    "links",
    multiple=True,
    help=(
        "Profile link as NETWORK=URL (e.g. linkedin=https://linkedin.com/in/x). "
        "Repeatable; passing any --link replaces the whole list. "
        "A single --link '' clears it."
    ),
)
@click.pass_context
def vault_set_profile(
    ctx: click.Context,
    display_name: str | None,
    headline: str | None,
    summary: str | None,
    location: str | None,
    contact_email: str | None,
    phone: str | None,
    url: str | None,
    links: tuple[str, ...],
) -> None:
    """Set profile fields on the vault.

    Only fields you pass are updated; omitted fields keep their current
    values. Pass an empty string to clear a field.
    """
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    if (
        all(
            v is None
            for v in (
                display_name,
                headline,
                summary,
                location,
                contact_email,
                phone,
                url,
            )
        )
        and not links
    ):
        click.echo(
            "No fields provided. Pass at least one of "
            "--name, --headline, --summary, --location, --email, "
            "--phone, --url, --link."
        )
        ctx.exit(1)
        return

    profiles: list[ProfileLink] | None = None
    if links:
        profiles = []
        if links != ("",):
            for raw in links:
                network, sep, link_url = raw.partition("=")
                if not sep or not network.strip() or not link_url.strip():
                    click.echo(
                        f"Invalid --link {raw!r}: expected NETWORK=URL "
                        "(e.g. github=https://github.com/x)."
                    )
                    ctx.exit(1)
                    return
                profiles.append(
                    ProfileLink(network=network.strip(), url=link_url.strip())
                )

    profile = store.set_profile(
        display_name=display_name,
        headline=headline,
        summary=summary,
        location=location,
        contact_email=contact_email,
        phone=phone,
        url=url,
        profiles=profiles,
    )
    click.echo("Updated profile:")
    click.echo(f"  display_name:  {profile.display_name}")
    click.echo(f"  headline:      {profile.headline}")
    click.echo(f"  summary:       {profile.summary}")
    click.echo(f"  location:      {profile.location}")
    click.echo(f"  contact_email: {profile.contact_email}")
    click.echo(f"  phone:         {profile.phone}")
    click.echo(f"  url:           {profile.url}")
    if profile.profiles:
        click.echo("  links:")
        for link in profile.profiles:
            click.echo(f"    {link.network}: {link.url or link.username}")


# --- vault add-skill ---


@vault.command(name="add-skill")
@click.argument("name", required=False)
@click.option(
    "--proficiency",
    "-p",
    type=click.IntRange(1, 5),
    default=None,
    help="Proficiency level (1-5): 1 familiar, 2 working, 3 proficient, "
    "4 expert, 5 authority.",
)
@click.option(
    "--category",
    "-c",
    default=None,
    help="Skill category — optional (e.g. technical, soft, domain, tool). "
    "Defaults to the taxonomy's category on a match, else empty.",
)
@click.option("--notes", "-n", default=None, help="Optional notes about the skill.")
@click.option(
    "--force-category",
    is_flag=True,
    default=False,
    help="Keep --category even when it disagrees with the taxonomy match.",
)
@click.option(
    "--from-json",
    "from_json",
    type=click.File("r"),
    default=None,
    help="Batch mode: load skills from a JSON file (or '-' for stdin). "
    "Expects a JSON array of {name, proficiency, category, notes?} objects.",
)
@click.pass_context
def vault_add_skill(
    ctx: click.Context,
    name: str | None,
    proficiency: int | None,
    category: str | None,
    notes: str | None,
    force_category: bool,
    from_json: IO[str] | None,
) -> None:
    """Add a skill to your vault (single or batch via --from-json)."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    if from_json is not None:
        if name is not None or proficiency is not None or category is not None:
            click.echo(
                "--from-json cannot be combined with NAME, --proficiency, "
                "or --category."
            )
            ctx.exit(2)
            return
        items = _read_json_items(from_json)
        errors = _batch_add_skills(store, items)
        if errors:
            ctx.exit(1)
        return

    if name is None or proficiency is None:
        click.echo(
            "NAME and --proficiency are required "
            "(or use --from-json for batch input). --category is optional."
        )
        ctx.exit(2)
        return

    # Taxonomy integration. --category is optional: when omitted, the
    # taxonomy's category is used on a match, else it stays empty.
    taxonomy_id = None
    effective_category = category or ""
    exact = find_exact(name)
    if exact:
        taxonomy_id = exact.id
        click.echo(f"Matched taxonomy: {exact.name} ({exact.category})")
        if category is None:
            effective_category = exact.category
        elif category != exact.category:
            if force_category:
                click.echo(
                    f"Keeping --category {category!r} "
                    f"(taxonomy suggests {exact.category!r}); "
                    "--force-category in effect."
                )
            else:
                click.echo(
                    f"Overriding --category {category!r} with "
                    f"taxonomy category {exact.category!r}. "
                    "Pass --force-category to keep your value."
                )
                effective_category = exact.category
    else:
        suggestions = suggest_matches(name)
        if suggestions:
            names = ", ".join(s.name for s in suggestions)
            click.echo(f"Did you mean: {names}?")
            click.echo("Adding as custom skill (no taxonomy match).")

    try:
        skill = store.add_skill(
            name=name,
            proficiency=proficiency,
            category=effective_category,
            notes=notes,
            taxonomy_id=taxonomy_id,
        )
    except DuplicateSkillError as exc:
        click.echo(
            f"Skill already exists: {exc.name!r} ({exc.existing_id}). "
            "Remove it first with 'vault remove <id>' to replace."
        )
        ctx.exit(1)
        return
    click.echo(f"Added skill: {skill.name} ({skill.proficiency}/5) [{skill.id}]")


def _report_item_problems(label: str, problems: list[str]) -> None:
    """Print one ``[err] <label>: field: message`` line per violation."""
    for problem in problems:
        click.echo(f"[err] {label}: {problem}")


def _batch_add_skills(store: VaultStore, items: list[dict[str, Any]]) -> int:
    """Add skills from parsed JSON items.

    Prints per-item lines (ok/dup/err) and a summary line. ALL violations
    for an item are reported in one pass (missing fields and range errors
    together). Returns the number of items that failed (duplicates count
    as errors).
    """
    added = 0
    errors = 0
    for i, item in enumerate(items):
        name = item.get("name")
        label = name if isinstance(name, str) and name.strip() else f"item {i}"

        problems: list[str] = []
        if "name" not in item:
            problems.append("name: missing required field")
        elif not isinstance(name, str) or not name.strip():
            problems.append("name: must be a non-empty string")
        proficiency = item.get("proficiency")
        if "proficiency" not in item:
            problems.append("proficiency: missing required field")
        elif not isinstance(proficiency, int) or isinstance(proficiency, bool):
            problems.append("proficiency: must be an integer 1-5")
        elif not 1 <= proficiency <= 5:
            problems.append("proficiency: must be between 1 and 5")
        category = item.get("category", "")
        if category is None:
            category = ""
        if not isinstance(category, str):
            problems.append("category: must be a string")
        notes = item.get("notes")
        if notes is not None and not isinstance(notes, str):
            problems.append("notes: must be a string")
        if problems:
            _report_item_problems(label, problems)
            errors += 1
            continue

        assert isinstance(name, str) and isinstance(proficiency, int)
        taxonomy_id = None
        exact = find_exact(name)
        if exact:
            taxonomy_id = exact.id
            # category is optional; fall back to the taxonomy's category.
            if not category:
                category = exact.category
        try:
            skill = store.add_skill(
                name=name,
                proficiency=proficiency,
                category=category,
                notes=notes if notes else None,
                taxonomy_id=taxonomy_id,
            )
        except DuplicateSkillError as exc:
            click.echo(f"[dup] {name}: already exists ({exc.existing_id})")
            errors += 1
            continue
        except ValidationError as exc:
            _report_item_problems(name, _validation_problems(exc))
            errors += 1
            continue
        except (ValueError, TypeError) as exc:
            click.echo(f"[err] {name}: {exc}")
            errors += 1
            continue
        click.echo(f"[ok] {skill.name} ({skill.proficiency}/5) [{skill.id}]")
        added += 1
    click.echo(f"Summary: added {added}, errors {errors}")
    return errors


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
@click.option(
    "--skill-id",
    "skill_ids_opt",
    multiple=True,
    type=UUID_PARAM,
    help="Skill UUID exercised in this role (repeatable).",
)
@click.option(
    "--skill-link",
    "skill_links_opt",
    multiple=True,
    help="Per-skill proficiency annotation as '<skill-uuid>:<1-5>' "
    "(repeatable; contract 1.2). The skill-uuid must also be a --skill-id.",
)
@click.option("--interactive", "-i", is_flag=True, default=True, help="Guided prompts.")
@click.option(
    "--from-json",
    "from_json",
    type=click.File("r"),
    default=None,
    help="Batch mode: load experiences from a JSON file (or '-' for stdin). "
    "Expects a JSON array of {title, company?, start_date?, end_date?, "
    "description?, accomplishments?, skill_ids?} objects.",
)
@click.pass_context
def vault_add_experience(
    ctx: click.Context,
    title: str | None,
    company: str | None,
    start_date: str | None,
    end_date: str | None,
    description: str | None,
    accomplishments: tuple[str, ...],
    skill_ids_opt: tuple[UUID, ...],
    skill_links_opt: tuple[str, ...],
    interactive: bool,
    from_json: IO[str] | None,
) -> None:
    """Add a work experience to your vault.

    Only --title is required; all other fields are optional and default
    to empty. Omitting --title falls back to interactive prompts for
    every field. --skill-id links the skills exercised in this role.
    """
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    if from_json is not None:
        if title is not None:
            click.echo("--from-json cannot be combined with --title.")
            ctx.exit(2)
            return
        if skill_ids_opt or skill_links_opt:
            click.echo(
                "--from-json cannot be combined with --skill-id/--skill-link; "
                "put skill_ids/skill_links on each batch item instead."
            )
            ctx.exit(2)
            return
        items = _read_json_items(from_json)
        errors = _batch_add_experiences(store, items)
        if errors:
            ctx.exit(1)
        return

    non_interactive = title is not None
    if non_interactive:
        company = company if company is not None else ""
        start_date = start_date if start_date is not None else ""
        end_date_val = end_date if end_date else None
        description = description if description is not None else ""
        accomplishment_list = [a for a in accomplishments if a]
        skill_ids = list(skill_ids_opt)
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
        if skill_ids_opt:
            skill_ids = list(skill_ids_opt)
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

    skill_links = _parse_skill_link_opts(ctx, skill_links_opt)

    assert title is not None
    exp = store.add_experience(
        title=title,
        company=company or "",
        start_date=start_date or "",
        end_date=end_date_val,
        description=description or "",
        accomplishments=accomplishment_list,
        skill_ids=skill_ids,
        skill_links=skill_links,
    )
    click.echo(f"Added experience: {exp.title} at {exp.company} [{exp.id}]")


def _parse_skill_link_opts(
    ctx: click.Context, raw: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Parse ``--skill-link '<uuid>:<1-5>'`` options into payload dicts.

    Proficiency is optional (a bare UUID yields an unset proficiency).
    Raises :class:`click.BadParameter` on a malformed UUID or out-of-range
    proficiency (Click exits 2).
    """
    links: list[dict[str, Any]] = []
    for spec in raw:
        sid, sep, prof = spec.partition(":")
        try:
            skill_id = UUID(sid.strip())
        except ValueError as exc:
            raise click.BadParameter(
                f"invalid skill UUID {sid!r}", ctx=ctx, param_hint="--skill-link"
            ) from exc
        entry: dict[str, Any] = {"skill_id": skill_id}
        if sep and prof.strip():
            try:
                proficiency = int(prof.strip())
            except ValueError as exc:
                raise click.BadParameter(
                    f"proficiency must be an integer, got {prof!r}",
                    ctx=ctx,
                    param_hint="--skill-link",
                ) from exc
            if not 1 <= proficiency <= 5:
                raise click.BadParameter(
                    f"proficiency must be 1-5, got {proficiency}",
                    ctx=ctx,
                    param_hint="--skill-link",
                )
            entry["proficiency"] = proficiency
        links.append(entry)
    return links


def _batch_add_experiences(store: VaultStore, items: list[dict[str, Any]]) -> int:
    """Add experiences from parsed JSON items. Returns number of errors.

    ALL violations for an item are reported in one pass.
    """
    added = 0
    errors = 0
    for i, item in enumerate(items):
        title = item.get("title")
        label = title if isinstance(title, str) and title.strip() else f"item {i}"

        problems: list[str] = []
        if "title" not in item:
            problems.append("title: missing required field")
        elif not isinstance(title, str) or not title.strip():
            problems.append("title: must be a non-empty string")
        accomplishments_raw = item.get("accomplishments") or []
        if not isinstance(accomplishments_raw, list) or not all(
            isinstance(a, str) for a in accomplishments_raw
        ):
            problems.append("accomplishments: must be a list of strings")
            accomplishments_raw = []
        skill_ids: list[UUID] = []
        try:
            skill_ids = _parse_uuid_list(item.get("skill_ids"), "skill_ids", i)
        except ValueError as exc:
            problems.append(str(exc))
        if problems:
            _report_item_problems(label, problems)
            errors += 1
            continue

        assert isinstance(title, str)
        company = item.get("company", "")
        start_date = item.get("start_date", "")
        end_date = item.get("end_date") or None
        description = item.get("description", "")
        try:
            exp = store.add_experience(
                title=title,
                company=str(company),
                start_date=str(start_date),
                end_date=str(end_date) if end_date else None,
                description=str(description),
                accomplishments=list(accomplishments_raw),
                skill_ids=skill_ids,
                skill_links=item.get("skill_links"),
            )
        except ValidationError as exc:
            _report_item_problems(title, _validation_problems(exc))
            errors += 1
            continue
        except (ValueError, TypeError) as exc:
            click.echo(f"[err] {title}: {exc}")
            errors += 1
            continue
        click.echo(f"[ok] {exp.title} at {exp.company} [{exp.id}]")
        added += 1
    click.echo(f"Summary: added {added}, errors {errors}")
    return errors


# --- vault add-story (interactive, STAR format) ---


@vault.command(name="add-story")
@click.option("--title", default=None, help="Story title.")
@click.option("--situation", default=None, help="STAR: situation.")
@click.option("--task", default=None, help="STAR: task.")
@click.option("--action", default=None, help="STAR: action.")
@click.option("--result", default=None, help="STAR: result.")
@click.option(
    "--lesson",
    default=None,
    help="Lesson learned (rendered as an optional '## Lesson' section).",
)
@click.option(
    "--outcome",
    type=click.Choice(["win", "failure", "learning"], case_sensitive=False),
    default=None,
    help="Outcome classification (win, failure, or learning).",
)
@click.option(
    "--theme-tag",
    "theme_tags_opt",
    multiple=True,
    help="Theme tag, e.g. incident-response (repeatable).",
)
@click.option(
    "--skill-id",
    "skill_ids_opt",
    multiple=True,
    type=UUID_PARAM,
    help="Skill UUID (repeatable).",
)
@click.option(
    "--experience-id",
    default=None,
    type=UUID_PARAM,
    help="Experience UUID this story belongs to.",
)
@click.option(
    "--interactive", "-i", is_flag=True, default=True, help="Guided STAR prompts."
)
@click.option(
    "--from-json",
    "from_json",
    type=click.File("r"),
    default=None,
    help="Batch mode: load stories from a JSON file (or '-' for stdin). "
    "Expects a JSON array of {title, situation?, task?, action?, result?, "
    "lesson?, outcome?, theme_tags?, skill_ids?, experience_id?} objects.",
)
@click.pass_context
def vault_add_story(
    ctx: click.Context,
    title: str | None,
    situation: str | None,
    task: str | None,
    action: str | None,
    result: str | None,
    lesson: str | None,
    outcome: str | None,
    theme_tags_opt: tuple[str, ...],
    skill_ids_opt: tuple[UUID, ...],
    experience_id: UUID | None,
    interactive: bool,
    from_json: IO[str] | None,
) -> None:
    """Add a STAR-format story to your vault.

    Pass --title and STAR fields for non-interactive use (--lesson,
    --outcome, and --theme-tag are optional extras). Any missing field
    will be prompted for when --title is omitted.
    """
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    if from_json is not None:
        if title is not None:
            click.echo("--from-json cannot be combined with --title.")
            ctx.exit(2)
            return
        items = _read_json_items(from_json)
        errors = _batch_add_stories(store, items)
        if errors:
            ctx.exit(1)
        return

    non_interactive = title is not None
    if non_interactive:
        situation = situation if situation is not None else ""
        task = task if task is not None else ""
        action = action if action is not None else ""
        result = result if result is not None else ""
        skill_ids = list(skill_ids_opt)
        experience_uuid = experience_id
    else:
        click.echo("Enter your story in STAR format:")
        title = click.prompt("Title")
        situation = click.prompt("Situation")
        task = click.prompt("Task")
        action = click.prompt("Action")
        result = click.prompt("Result")
        if skill_ids_opt:
            skill_ids = list(skill_ids_opt)
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
            experience_uuid = experience_id
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
        lesson=lesson or "",
        outcome=(outcome or "").lower(),
        theme_tags=[t for t in theme_tags_opt if t and t.strip()],
        skill_ids=skill_ids,
        experience_id=experience_uuid,
    )
    click.echo(f"Added story: {story.title} [{story.id}]")


_STORY_OUTCOME_VALUES = ("win", "failure", "learning")


def _batch_add_stories(store: VaultStore, items: list[dict[str, Any]]) -> int:
    """Add stories from parsed JSON items. Returns number of errors.

    ALL violations for an item are reported in one pass.
    """
    added = 0
    errors = 0
    for i, item in enumerate(items):
        title = item.get("title")
        label = title if isinstance(title, str) and title.strip() else f"item {i}"

        problems: list[str] = []
        if "title" not in item:
            problems.append("title: missing required field")
        elif not isinstance(title, str) or not title.strip():
            problems.append("title: must be a non-empty string")
        skill_ids: list[UUID] = []
        try:
            skill_ids = _parse_uuid_list(item.get("skill_ids"), "skill_ids", i)
        except ValueError as exc:
            problems.append(str(exc))
        experience_uuid: UUID | None = None
        experience_id_raw = item.get("experience_id")
        if experience_id_raw:
            if not isinstance(experience_id_raw, str):
                problems.append("experience_id: must be a UUID string")
            else:
                try:
                    experience_uuid = UUID(experience_id_raw)
                except ValueError:
                    problems.append(
                        f"experience_id: invalid UUID {experience_id_raw!r}"
                    )
        outcome = item.get("outcome", "") or ""
        if not isinstance(outcome, str):
            problems.append("outcome: must be a string")
        elif outcome and outcome not in _STORY_OUTCOME_VALUES:
            problems.append(
                f"outcome: must be one of {', '.join(_STORY_OUTCOME_VALUES)}; "
                f"got {outcome!r}"
            )
        theme_tags_raw = item.get("theme_tags") or []
        if not isinstance(theme_tags_raw, list) or not all(
            isinstance(t, str) for t in theme_tags_raw
        ):
            problems.append("theme_tags: must be a list of strings")
            theme_tags_raw = []
        if problems:
            _report_item_problems(label, problems)
            errors += 1
            continue

        assert isinstance(title, str) and isinstance(outcome, str)
        try:
            story = store.add_story(
                title=title,
                situation=str(item.get("situation", "") or ""),
                task=str(item.get("task", "") or ""),
                action=str(item.get("action", "") or ""),
                result=str(item.get("result", "") or ""),
                lesson=str(item.get("lesson", "") or ""),
                outcome=outcome,
                theme_tags=list(theme_tags_raw),
                skill_ids=skill_ids,
                experience_id=experience_uuid,
            )
        except ValidationError as exc:
            _report_item_problems(title, _validation_problems(exc))
            errors += 1
            continue
        except (ValueError, TypeError) as exc:
            click.echo(f"[err] {title}: {exc}")
            errors += 1
            continue
        click.echo(f"[ok] {story.title} [{story.id}]")
        added += 1
    click.echo(f"Summary: added {added}, errors {errors}")
    return errors


# --- vault add-philosophy (interactive) ---

_PHILOSOPHY_CATEGORIES = [c.value for c in PhilosophyCategory]


@vault.command(name="add-philosophy")
@click.option("--title", default=None, help="Philosophy title.")
@click.option("--description", default=None, help="Description of the philosophy.")
@click.option(
    "--category",
    default=None,
    type=click.Choice(_PHILOSOPHY_CATEGORIES, case_sensitive=False),
    help="Philosophy category (optional).",
)
@click.option(
    "--evidence-id",
    "evidence_ids_opt",
    multiple=True,
    type=UUID_PARAM,
    help="Evidence story UUID (repeatable).",
)
@click.option("--interactive", "-i", is_flag=True, default=True, help="Guided prompts.")
@click.option(
    "--from-json",
    "from_json",
    type=click.File("r"),
    default=None,
    help="Batch mode: load philosophies from a JSON file (or '-' for stdin). "
    "Expects a JSON array of {title, description?, category?, evidence_story_ids?} "
    "objects.",
)
@click.pass_context
def vault_add_philosophy(
    ctx: click.Context,
    title: str | None,
    description: str | None,
    category: str | None,
    evidence_ids_opt: tuple[UUID, ...],
    interactive: bool,
    from_json: IO[str] | None,
) -> None:
    """Add a work philosophy to your vault.

    Pass --title and --description for non-interactive use; --category
    is optional.
    """
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    if from_json is not None:
        if title is not None:
            click.echo("--from-json cannot be combined with --title.")
            ctx.exit(2)
            return
        items = _read_json_items(from_json)
        errors = _batch_add_philosophies(store, items)
        if errors:
            ctx.exit(1)
        return

    non_interactive = title is not None
    if non_interactive:
        description = description if description is not None else ""
        evidence_ids = list(evidence_ids_opt)
    else:
        title = click.prompt("Philosophy title")
        description = click.prompt("Description")
        click.echo(f"Categories: {', '.join(_PHILOSOPHY_CATEGORIES)} (or blank)")
        category = click.prompt(
            "Category",
            default="",
            show_default=False,
            type=click.Choice([*_PHILOSOPHY_CATEGORIES, ""], case_sensitive=False),
        )
        if evidence_ids_opt:
            evidence_ids = list(evidence_ids_opt)
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
    philosophy = store.add_philosophy(
        title=title,
        description=description or "",
        category=category or "",
        evidence_story_ids=evidence_ids,
    )
    click.echo(f"Added philosophy: {philosophy.title} [{philosophy.id}]")


def _batch_add_philosophies(store: VaultStore, items: list[dict[str, Any]]) -> int:
    """Add philosophies from parsed JSON items. Returns number of errors.

    ALL violations for an item are reported in one pass.
    """
    added = 0
    errors = 0
    for i, item in enumerate(items):
        title = item.get("title")
        label = title if isinstance(title, str) and title.strip() else f"item {i}"

        problems: list[str] = []
        if "title" not in item:
            problems.append("title: missing required field")
        elif not isinstance(title, str) or not title.strip():
            problems.append("title: must be a non-empty string")
        category = item.get("category", "") or ""
        if not isinstance(category, str):
            problems.append("category: must be a string")
        elif category and category not in _PHILOSOPHY_CATEGORIES:
            problems.append(
                f"category: must be one of {', '.join(_PHILOSOPHY_CATEGORIES)}; "
                f"got {category!r}"
            )
        evidence_ids: list[UUID] = []
        try:
            evidence_ids = _parse_uuid_list(
                item.get("evidence_story_ids"), "evidence_story_ids", i
            )
        except ValueError as exc:
            problems.append(str(exc))
        if problems:
            _report_item_problems(label, problems)
            errors += 1
            continue

        assert isinstance(title, str) and isinstance(category, str)
        try:
            philosophy = store.add_philosophy(
                title=title,
                description=str(item.get("description", "") or ""),
                category=category,
                evidence_story_ids=evidence_ids,
            )
        except ValidationError as exc:
            _report_item_problems(title, _validation_problems(exc))
            errors += 1
            continue
        except (ValueError, TypeError) as exc:
            click.echo(f"[err] {title}: {exc}")
            errors += 1
            continue
        click.echo(f"[ok] {philosophy.title} [{philosophy.id}]")
        added += 1
    click.echo(f"Summary: added {added}, errors {errors}")
    return errors


# --- vault add-education ---


@vault.command(name="add-education")
@click.option("--institution", default=None, help="Institution name.")
@click.option("--degree", default=None, help="Degree (e.g. Bachelor, Master, PhD).")
@click.option("--field", "field_of_study", default=None, help="Field of study.")
@click.option("--start-date", default=None, help="Start date (YYYY).")
@click.option(
    "--end-date", default=None, help="End date (YYYY); blank/omitted means current."
)
@click.option("--description", default=None, help="Description of the entry.")
@click.option("--interactive", "-i", is_flag=True, default=True, help="Guided prompts.")
@click.option(
    "--from-json",
    "from_json",
    type=click.File("r"),
    default=None,
    help="Batch mode: load education entries from a JSON file (or '-' for "
    "stdin). Expects a JSON array of {institution, degree?, field_of_study?, "
    "start_date?, end_date?, description?} objects.",
)
@click.pass_context
def vault_add_education(
    ctx: click.Context,
    institution: str | None,
    degree: str | None,
    field_of_study: str | None,
    start_date: str | None,
    end_date: str | None,
    description: str | None,
    interactive: bool,
    from_json: IO[str] | None,
) -> None:
    """Add an education entry to your vault.

    Pass --institution (and optional fields) for non-interactive use.
    Without --institution, falls back to interactive prompts.
    """
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    if from_json is not None:
        if institution is not None:
            click.echo("--from-json cannot be combined with --institution.")
            ctx.exit(2)
            return
        items = _read_json_items(from_json)
        errors = _batch_add_education(store, items)
        if errors:
            ctx.exit(1)
        return

    non_interactive = institution is not None
    if non_interactive:
        degree_val = degree if degree is not None else ""
        field_val = field_of_study if field_of_study is not None else ""
        start_val = start_date if start_date is not None else ""
        end_val = end_date if end_date else None
        desc_val = description if description is not None else ""
    else:
        institution = click.prompt("Institution")
        degree_val = click.prompt("Degree (e.g. Bachelor, Master, PhD)", default="")
        field_val = click.prompt("Field of study", default="")
        start_val = click.prompt("Start date (YYYY)", default="")
        end_prompt = click.prompt("End date (YYYY, blank for current)", default="")
        end_val = end_prompt if end_prompt else None
        desc_val = click.prompt("Description", default="")

    assert institution is not None
    edu = store.add_education(
        institution=institution,
        degree=degree_val,
        field_of_study=field_val,
        start_date=start_val,
        end_date=end_val,
        description=desc_val,
    )
    click.echo(f"Added education: {edu.degree} at {edu.institution} [{edu.id}]")


def _batch_add_education(store: VaultStore, items: list[dict[str, Any]]) -> int:
    """Add education entries from parsed JSON items. Returns number of errors.

    ALL violations for an item are reported in one pass.
    """
    added = 0
    errors = 0
    string_fields = (
        "degree",
        "field_of_study",
        "start_date",
        "end_date",
        "description",
    )
    for i, item in enumerate(items):
        institution = item.get("institution")
        label = (
            institution
            if isinstance(institution, str) and institution.strip()
            else f"item {i}"
        )

        problems: list[str] = []
        if "institution" not in item:
            problems.append("institution: missing required field")
        elif not isinstance(institution, str) or not institution.strip():
            problems.append("institution: must be a non-empty string")
        for field_name in string_fields:
            value = item.get(field_name)
            if value is not None and not isinstance(value, str):
                problems.append(f"{field_name}: must be a string")
        if problems:
            _report_item_problems(label, problems)
            errors += 1
            continue

        assert isinstance(institution, str)
        end_date = item.get("end_date") or None
        try:
            edu = store.add_education(
                institution=institution,
                degree=str(item.get("degree", "") or ""),
                field_of_study=str(item.get("field_of_study", "") or ""),
                start_date=str(item.get("start_date", "") or ""),
                end_date=str(end_date) if end_date else None,
                description=str(item.get("description", "") or ""),
            )
        except ValidationError as exc:
            _report_item_problems(institution, _validation_problems(exc))
            errors += 1
            continue
        except (ValueError, TypeError) as exc:
            click.echo(f"[err] {institution}: {exc}")
            errors += 1
            continue
        click.echo(f"[ok] {edu.institution} [{edu.id}]")
        added += 1
    click.echo(f"Summary: added {added}, errors {errors}")
    return errors


# --- vault remove ---


@vault.command(name="remove")
@click.argument("item_id", type=UUID_PARAM)
@click.option(
    "--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt."
)
@click.pass_context
def vault_remove(ctx: click.Context, item_id: UUID, yes: bool) -> None:
    """Remove an item from the vault by UUID."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    uid = item_id

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


_EXPORT_FORMAT_CHOICES = [
    "json",
    "markdown",
    "jsonresume",
    "json-resume",
    "synthpanel-persona",
    "career-bundle",
]


def _normalize_export_format(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    """Normalize ``json-resume`` to ``jsonresume`` and lowercase the choice.

    Click's ``Choice`` validator runs before this callback, so any spelling
    listed in ``_EXPORT_FORMAT_CHOICES`` reaches us. The exporter only
    knows about the canonical names.
    """
    if value is None:
        return value
    v = value.lower()
    if v == "json-resume":
        return "jsonresume"
    return v


@vault.command(name="export")
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(_EXPORT_FORMAT_CHOICES, case_sensitive=False),
    default="json",
    show_default=True,
    callback=_normalize_export_format,
    help="Export format. ``json-resume`` is accepted as a synonym for ``jsonresume``.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Write to file instead of stdout.",
)
@click.option(
    "--pack-name",
    default=None,
    help="Override the pack name (synthpanel-persona only).",
)
@click.option(
    "--lens",
    "lens_ref",
    default=None,
    help=(
        "Project cv.md through a positioning lens, by slug or id "
        "(career-bundle only)."
    ),
)
@click.option(
    "--zip",
    "as_zip",
    is_flag=True,
    default=False,
    help=(
        "Write the bundle as a single .zip archive instead of a directory "
        "(career-bundle only; -o names the archive)."
    ),
)
@click.pass_context
def vault_export(
    ctx: click.Context,
    fmt: str,
    output: str | None,
    pack_name: str | None,
    lens_ref: str | None,
    as_zip: bool,
) -> None:
    """Export the vault as JSON, Markdown, JSON Resume, a SynthPanel
    persona, or a multi-file career bundle (``-f career-bundle``,
    requires ``-o DIR`` or ``--zip``)."""
    from traitprint.export import BUNDLE_FORMATS, export_vault

    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return
    v = store.load()

    if fmt in BUNDLE_FORMATS:
        _export_bundle(ctx, v, fmt, output, lens_ref, as_zip)
        return
    if lens_ref is not None or as_zip:
        click.echo("--lens and --zip are only supported with -f career-bundle.")
        ctx.exit(2)
        return

    rendered = export_vault(v, fmt, pack_name=pack_name)
    if output:
        from pathlib import Path

        Path(output).write_text(rendered, encoding="utf-8")
        click.echo(f"Wrote {fmt} export to {output}")
    else:
        click.echo(rendered, nl=False)


def _export_bundle(
    ctx: click.Context,
    v: VaultSchema,
    fmt: str,
    output: str | None,
    lens_ref: str | None,
    as_zip: bool,
) -> None:
    """Write a multi-file bundle to a directory or a .zip archive."""
    import zipfile
    from pathlib import Path

    from traitprint.export import export_vault_bundle

    if not output:
        click.echo(
            f"-f {fmt} writes multiple files — pass -o DIR "
            "(or -o FILE.zip with --zip)."
        )
        ctx.exit(2)
        return

    lens = None
    if lens_ref is not None:
        ref = lens_ref.strip().lower()
        lens = next(
            (
                lns
                for lns in v.lenses
                if lns.slug == ref or str(lns.id) == ref or lns.id.hex[:8] == ref
            ),
            None,
        )
        if lens is None:
            available = ", ".join(lns.slug for lns in v.lenses) or "none"
            click.echo(f"No lens matches {lens_ref!r}. Available: {available}.")
            ctx.exit(1)
            return

    bundle = export_vault_bundle(v, fmt, lens=lens)

    out = Path(output)
    if as_zip or out.suffix == ".zip":
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel, content in bundle.items():
                zf.writestr(rel, content)
        click.echo(f"Wrote {fmt} bundle ({len(bundle)} files) to {out}")
        return

    for rel, content in bundle.items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        click.echo(f"  {rel}")
    click.echo(f"Wrote {fmt} bundle ({len(bundle)} files) to {out}/")


# --- vault import-story-bank ---


@vault.command(name="import-story-bank")
@click.argument("directory", type=click.Path(exists=True, file_okay=False))
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Parse and report what would be staged without writing proposals.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the plan/result as JSON.",
)
@click.pass_context
def vault_import_story_bank(
    ctx: click.Context, directory: str, dry_run: bool, as_json: bool
) -> None:
    """Import a job-search working directory as pending proposals.

    Detects the config/profile.yml + interview-prep/*.md layout by shape
    and stages one add_story proposal per STAR block plus one
    update_profile proposal — nothing is written to the vault until you
    approve ('traitprint proposals list'). A cv.md is not parsed here:
    run 'traitprint vault import-resume <dir>/cv.md --propose' for it.
    """
    from pathlib import Path

    from traitprint.importers.story_bank import IMPORT_SOURCE, detect_and_plan
    from traitprint.proposals import ProposalStore, ProposalValidationError

    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return
    vault_doc = store.load()

    try:
        plan = detect_and_plan(Path(directory), vault_doc)
    except ValueError as exc:
        click.echo(str(exc))
        ctx.exit(1)
        return

    if as_json and dry_run:
        click.echo(
            json.dumps(
                {
                    "stories": [payload for _, payload in plan.stories],
                    "profile": plan.profile_payload,
                    "has_cv": plan.has_cv,
                },
                indent=2,
            )
        )
        return

    staged = 0
    errors = 0
    results: list[dict[str, object]] = []
    proposals_store = ProposalStore(store.directory)

    for parsed, payload in plan.stories:
        if dry_run:
            click.echo(f"[plan] add_story: {parsed.title} (from {parsed.origin})")
            continue
        try:
            lp = proposals_store.create(
                "add_story",
                payload,
                rationale=f"Imported from {parsed.origin or 'story bank'}",
                source=IMPORT_SOURCE,
            )
            staged += 1
            results.append({"kind": "add_story", "id": str(lp.proposal.id)})
            click.echo(f"[ok] add_story: {parsed.title} [{lp.proposal.id.hex[:8]}]")
        except ProposalValidationError as exc:
            errors += 1
            click.echo(f"[err] add_story: {parsed.title}: {exc}")

    if plan.profile_payload is not None:
        if dry_run:
            keys = ", ".join(sorted(plan.profile_payload["basics"]))
            click.echo(f"[plan] update_profile: {keys}")
        else:
            try:
                lp = proposals_store.create(
                    "update_profile",
                    plan.profile_payload,
                    rationale=plan.profile_rationale,
                    source=IMPORT_SOURCE,
                )
                staged += 1
                results.append(
                    {"kind": "update_profile", "id": str(lp.proposal.id)}
                )
                click.echo(f"[ok] update_profile [{lp.proposal.id.hex[:8]}]")
            except ProposalValidationError as exc:
                errors += 1
                click.echo(f"[err] update_profile: {exc}")

    if plan.has_cv:
        click.echo(
            "Found cv.md — import it with "
            f"'traitprint vault import-resume {directory}/cv.md --propose'."
        )

    if dry_run:
        click.echo(
            f"Dry run: {len(plan.stories)} story proposal(s) and "
            f"{1 if plan.profile_payload else 0} profile proposal(s) "
            "would be staged."
        )
        return

    if as_json:
        click.echo(json.dumps({"staged": results, "errors": errors}, indent=2))
    else:
        click.echo(
            f"Summary: staged {staged}, errors {errors}. Review with "
            "'traitprint proposals list'."
        )
    if errors:
        ctx.exit(1)


# --- vault history ---


@vault.command(name="history")
@click.option(
    "--count", "-n", default=10, help="Number of entries to show.", show_default=True
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a JSON array of {sha, message} objects.",
)
@click.pass_context
def vault_history(ctx: click.Context, count: int, as_json: bool) -> None:
    """Show git history for the whole vault file tree."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    entries = git_log(store.directory, n=count)
    if as_json:
        rows = []
        for entry in entries:
            sha, _, message = entry.partition(" ")
            rows.append({"sha": sha, "message": message})
        click.echo(json.dumps(rows, indent=2))
        return
    if not entries:
        click.echo("No history found.")
        return
    for entry in entries:
        click.echo(entry)


# --- vault diff ---


@vault.command(name="diff")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a {from_sha, to_sha, diff_text} JSON object.",
)
@click.pass_context
def vault_diff_cmd(ctx: click.Context, as_json: bool) -> None:
    """Show changes across the vault file tree since the previous commit."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    changes = git_diff(store.directory)
    if as_json:
        payload = {
            "from_sha": rev_sha(store.directory, "HEAD~1"),
            "to_sha": rev_sha(store.directory, "HEAD"),
            "diff_text": changes,
        }
        click.echo(json.dumps(payload, indent=2))
        return
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
    """Roll back the whole vault file tree to the previous commit."""
    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    if not yes and not click.confirm("Roll back vault to previous state?"):
        click.echo("Cancelled.")
        return

    git_rollback(store.directory)
    click.echo("Vault rolled back to previous state.")


# --- vault migrate ---


@vault.command(name="migrate")
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Show the planned v1 file list and proficiency remaps without writing.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the plan/result as JSON.",
)
@click.pass_context
def vault_migrate(ctx: click.Context, dry_run: bool, as_json: bool) -> None:
    """Migrate a legacy v0 vault.json to the v1 file tree.

    Remaps skill proficiency from the old 1-10 scale to 1-5 (ceil(x/2)),
    writes the v1 tree (traitprint.json, profile.json, skills.json,
    education.json, experiences/, stories/, philosophies/), removes
    vault.json, and records everything as a single git commit
    "Migrate vault to schema v1". Idempotent: a vault that is already
    v1 is a no-op.
    """
    from traitprint.vault_io import build_tree, remap_proficiency

    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return
    if store.is_v1():
        if as_json:
            click.echo(json.dumps({"status": "already-v1", "migrated": False}))
        else:
            click.echo("Vault is already schema v1 — nothing to migrate.")
        return

    # Read the raw v0 file so the remap report shows original values.
    try:
        raw = json.loads(store.vault_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.ClickException(
            f"Invalid JSON in {store.vault_path}: {exc}"
        ) from exc
    remaps: list[dict[str, Any]] = []
    raw_skills = raw.get("skills") or [] if isinstance(raw, dict) else []
    for skill in raw_skills:
        if isinstance(skill, dict) and isinstance(skill.get("proficiency"), int):
            old = skill["proficiency"]
            remaps.append(
                {
                    "id": str(skill.get("id", "")),
                    "name": str(skill.get("name", "")),
                    "from": old,
                    "to": remap_proficiency(old),
                }
            )

    vault_obj = store.load()  # v0 reader — proficiency already remapped in memory
    vault_obj.schema_version = 1
    files = sorted(build_tree(vault_obj, store.directory))

    if dry_run:
        if as_json:
            payload = {
                "status": "planned",
                "migrated": False,
                "files": files,
                "proficiency_remaps": remaps,
            }
            click.echo(json.dumps(payload, indent=2))
            return
        click.echo("Planned v1 files:")
        for f in files:
            click.echo(f"  {f}")
        if remaps:
            click.echo("Proficiency remaps (1-10 -> 1-5):")
            for r in remaps:
                click.echo(f"  {r['name']}: {r['from']} -> {r['to']}")
        click.echo("Dry run — vault not modified.")
        return

    store.save(vault_obj)  # writes the v1 tree and removes vault.json
    # commit() initializes the repo if missing — migrate promises a single
    # "Migrate vault to schema v1" commit even for a vault in a plain dir.
    committed = commit(store.directory, "Migrate vault to schema v1")
    if not committed:
        click.echo(
            "WARNING: the migration was written but could NOT be recorded "
            "as a git commit. Fix the git error above, then commit inside "
            "the vault directory to restore history/rollback.",
            err=True,
        )

    if as_json:
        payload = {
            "status": "migrated",
            "migrated": True,
            "files": files,
            "proficiency_remaps": remaps,
        }
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo(f"Migrated vault to schema v1 ({len(files)} files).")
    if remaps:
        noun = "proficiency" if len(remaps) == 1 else "proficiencies"
        click.echo(
            f"Remapped {len(remaps)} skill {noun} from 1-10 to 1-5 (ceil(x/2))."
        )


# --- vault audit ---


_SEVERITY_TAG = {"critical": "[crit] ", "major": "[major]", "minor": "[minor]"}


@vault.command(name="audit")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the full report as JSON (findings, story_scores, tensions, summary).",
)
@click.option(
    "--severity",
    type=click.Choice(["critical", "major", "minor"], case_sensitive=False),
    default="minor",
    show_default=True,
    help="Minimum severity to report (minor shows everything).",
)
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Exit non-zero when any critical- or major-level finding remains.",
)
@click.pass_context
def vault_audit(
    ctx: click.Context, as_json: bool, severity: str, strict: bool
) -> None:
    """Audit the vault for narrative coherence.

    Scores each STAR story (Polished/Strong/Solid/Draft), flags unsupported
    skill claims, philosophies without evidence, broken stories, dangling
    references, and roles with no story, and detects contradictions between
    stories. Philosophy tensions are surfaced as nuance, not problems.
    Read-only: it never modifies the vault.
    """
    from traitprint.audit import audit_vault, severity_rank, summarize
    from traitprint.proposals import ProposalStore
    from traitprint.tensions import format_tension_insight

    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    loaded_proposals, proposal_issues = ProposalStore(store.directory).load_all()
    pending_count = sum(
        1 for lp in loaded_proposals if lp.proposal.status == "pending"
    )
    report = audit_vault(
        store.load(),
        pending_proposals=pending_count,
        proposal_issues=[f"{i.file}: {i.problem}" for i in proposal_issues],
    )
    threshold = severity_rank(severity)  # type: ignore[arg-type]
    shown = [f for f in report.findings if severity_rank(f.severity) >= threshold]
    summary = summarize(shown)

    if as_json:
        payload = report.to_dict()
        payload["findings"] = [f.to_dict() for f in shown]
        payload["summary"] = summary
        click.echo(json.dumps(payload, indent=2))
        if strict and (summary["critical"] or summary["major"]):
            ctx.exit(1)
        return

    if shown:
        for f in shown:
            click.echo(f"{_SEVERITY_TAG[f.severity]} {f.section}: {f.message}")
        click.echo("")
    else:
        click.echo("No coherence issues found at this severity. ✨")
        click.echo("")

    if report.story_scores:
        click.echo("Story coherence:")
        for s in report.story_scores:
            click.echo(f"  {s.label:<9} ({s.overall:.0%}) {s.title}")
        if report.overall_coherence is not None:
            click.echo(f"  Overall: {report.overall_coherence:.0%}")
        click.echo("")

    if report.tensions:
        click.echo("Philosophy tensions (nuance, not problems):")
        for t in report.tensions:
            click.echo(f"  ~ {format_tension_insight(t)}")
        click.echo("")

    click.echo(
        f"Summary: {summary['critical']} critical, "
        f"{summary['major']} major, {summary['minor']} minor."
    )

    if strict and (summary["critical"] or summary["major"]):
        ctx.exit(1)


# --- doctor (vault phase + freshness) ---


_PHASE_NEXT_STEP = {
    "first-run": (
        "Bootstrap the vault: 'traitprint vault import-resume <file>' or "
        "the traitprint-fill-vault skill."
    ),
    "growing": (
        "Round it out with the traitprint-fill-vault and "
        "traitprint-mine-story-gaps skills."
    ),
    "established": (
        "Keep it fresh opportunistically with the traitprint-capture-story "
        "skill."
    ),
    "stale": (
        "Refresh current roles with traitprint-fill-vault and capture "
        "recent wins with traitprint-capture-story."
    ),
}


@cli.command(name="doctor")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit {phase, stale_days, findings, next} as JSON.",
)
@click.option(
    "--stale-days",
    type=click.IntRange(min=1),
    default=None,
    help="Days without a touch before content counts as stale (default 90).",
)
@click.pass_context
def doctor(ctx: click.Context, as_json: bool, stale_days: int | None) -> None:
    """Vault phase detection + freshness audit.

    Classifies the vault (first-run | growing | established | stale) from
    deterministic date math and reports what has gone stale — each finding
    names the Agent Skill that fixes it. Read-only; for the full coherence
    audit use 'traitprint vault audit'.
    """
    from traitprint.audit import (
        STALE_DAYS_DEFAULT,
        compute_phase,
        freshness_findings,
    )

    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        return

    days = stale_days if stale_days is not None else STALE_DAYS_DEFAULT
    vault = store.load()
    phase = compute_phase(vault, stale_days=days)
    findings = freshness_findings(vault, stale_days=days)
    next_step = _PHASE_NEXT_STEP[phase.phase]

    if as_json:
        click.echo(
            json.dumps(
                {
                    "phase": phase.to_dict(),
                    "stale_days": days,
                    "findings": [f.to_dict() for f in findings],
                    "next": next_step,
                },
                indent=2,
            )
        )
        return

    click.echo(f"Vault phase: {phase.phase}")
    click.echo(f"  {phase.reason}")
    click.echo(
        f"  skills {phase.skills} · experiences {phase.experiences} · "
        f"stories {phase.stories}"
    )
    click.echo("")
    if findings:
        click.echo("Freshness:")
        for f in findings:
            click.echo(f"  [{f.severity}] {f.section}: {f.message}")
            if f.fix_skill:
                click.echo(f"          fix: {f.fix_skill}")
    else:
        click.echo(f"No staleness detected (threshold {days} days). ✨")
    click.echo("")
    click.echo(f"Next: {next_step}")


# --- vault extract-text ---


@vault.command(name="extract-text")
@click.argument(
    "path",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=str),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help='Emit a {"file", "format", "chars", "text"} JSON object.',
)
@click.pass_context
def vault_extract_text(ctx: click.Context, path: str, as_json: bool) -> None:
    """Extract plain text from a document (PDF, DOCX, TXT, MD) to stdout.

    The deterministic half of resume import: no LLM, no vault writes.
    PDF needs pypdf and DOCX needs python-docx — both ship with
    pip install 'traitprint[import]'.
    """
    from pathlib import Path as _Path

    from traitprint.mining import ResumeExtractionError, extract_resume_text

    file_path = _Path(path)
    try:
        text = extract_resume_text(file_path)
    except ResumeExtractionError as exc:
        raise click.ClickException(str(exc)) from exc
    if not text.strip():
        raise click.ClickException(
            f"Extracted zero text from {path}. Is the file empty or image-only?"
        )

    if as_json:
        payload = {
            "file": path,
            "format": file_path.suffix.lower().lstrip("."),
            "chars": len(text),
            "text": text,
        }
        click.echo(json.dumps(payload, indent=2))
        return
    click.echo(text)


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
@click.option(
    "--assist/--no-assist",
    "assist",
    default=None,
    help="Force (--assist) or suppress (--no-assist) agent-assist mode. "
    "Default: assist kicks in automatically when no LLM provider is "
    "configured; with --no-assist and no provider the command errors.",
)
@click.option(
    "--propose",
    is_flag=True,
    default=False,
    help="Stage extracted items as pending proposals (proposals/*.json) "
    "instead of writing them to the vault (D9 staged path). Review with "
    "'traitprint proposals list', apply with 'traitprint proposals "
    "approve'. In agent-assist mode the write-back instructions switch "
    "to 'traitprint proposals add' commands.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Agent-assist mode only: emit the assist payload as structured "
    'JSON ({"mode": "agent-assist", "contract": ..., "text": ..., '
    '"write_back": ...}).',
)
@click.pass_context
def vault_import_resume(
    ctx: click.Context,
    path: str,
    provider: str | None,
    model: str | None,
    yes: bool,
    dry_run: bool,
    assist: bool | None,
    propose: bool,
    as_json: bool,
) -> None:
    """Import a resume via a BYOK LLM provider — or your wrapping agent.

    Extracts profile, skills, experiences, and education from PDF, DOCX,
    TXT, or MD. Shows a summary and asks for confirmation before writing
    to the vault.

    Provider resolution (D11): explicit --provider flag, then a configured
    BYOK key (env or .credentials), then agent-assist mode — the command
    prints the extracted text plus the extraction contract for the wrapping
    agent to complete and write back through the validated batch commands.
    Headless runs (no agent) still need a BYOK key; pass --no-assist to get
    an actionable error instead of the assist payload.
    """
    from pathlib import Path as _Path

    from traitprint.mining import (
        MAX_RESUME_CHARS,
        ResumeExtractionError,
        build_assist_payload,
        extract_resume_text,
        render_assist_payload,
        resume_to_draft,
    )
    from traitprint.providers import (
        NO_PROVIDER_HINT,
        LLMError,
        ProviderNotConfigured,
        detect_provider,
        has_provider_signal,
    )

    store = _get_store(ctx)
    if not store.exists():
        click.echo("No vault found. Run 'traitprint init' first.")
        ctx.exit(1)
        return

    if assist is True and provider is not None:
        click.echo("--assist cannot be combined with --provider.")
        ctx.exit(2)
        return

    # D11 resolution: explicit flag → configured BYOK key → ambient agent
    # (assist) → actionable error.
    llm = None
    if assist is not True and (provider is not None or has_provider_signal()):
        try:
            llm = detect_provider(preferred=provider, model=model)
        except ProviderNotConfigured as exc:
            raise click.ClickException(str(exc)) from exc

    if llm is None:
        if assist is False:
            raise click.ClickException(
                f"No LLM provider configured. {NO_PROVIDER_HINT} "
                "(Or drop --no-assist to let a wrapping agent do the "
                "extraction.)"
            )
        # Agent-assist mode: emit text + contract for the wrapping agent.
        try:
            text = extract_resume_text(_Path(path))
        except ResumeExtractionError as exc:
            raise click.ClickException(f"Resume extraction failed: {exc}") from exc
        if not text.strip():
            raise click.ClickException(
                f"Extracted zero text from {path}. "
                "Is the file empty or image-only?"
            )
        truncated = text[:MAX_RESUME_CHARS]
        # Thread the RESOLVED vault directory into the payload so the
        # write-back commands pin the same vault this invocation targeted
        # (--vault-dir / $TRAITPRINT_VAULT_DIR / discovery), wherever the
        # wrapping agent happens to run them from.
        if as_json:
            click.echo(
                json.dumps(
                    build_assist_payload(
                        truncated, path, store.directory, propose=propose
                    ),
                    indent=2,
                )
            )
        else:
            click.echo(
                render_assist_payload(
                    truncated, path, store.directory, propose=propose
                )
            )
        return

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
        prompt = (
            "Stage these items as proposals for review?"
            if propose
            else "Import these items into your vault?"
        )
        if not click.confirm(prompt, default=True):
            click.echo("Cancelled — vault unchanged.")
            return

    if propose:
        from traitprint.mining import draft_to_proposals
        from traitprint.proposals import ProposalStore

        pairs = draft_to_proposals(draft)
        if not pairs:
            click.echo("Nothing extracted — no proposals staged.")
            return
        pstore = ProposalStore(store.directory)
        file_name = _Path(path).name
        for kind, payload in pairs:
            pstore.create(
                kind,
                payload,
                rationale=f"Extracted from resume {file_name}",
                source="import-resume",
            )
        commit(
            store.directory,
            f"Propose resume import: {file_name} ({len(pairs)} proposals)",
        )
        click.echo(
            f"Staged {len(pairs)} proposals — the vault is unchanged. "
            "Review with 'traitprint proposals list', then "
            "'traitprint proposals approve --all' (or per-id approve/reject)."
        )
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


# ------------------------------------------------------------------
# proposals command group — review staged writes (contract rule 7)
# ------------------------------------------------------------------


def _require_vault(ctx: click.Context) -> VaultStore:
    """Get the vault store, erroring (exit 1) when no vault exists."""
    store = _get_store(ctx)
    if not store.exists():
        raise click.ClickException(
            f"No vault found at {store.directory}. Run 'traitprint init' first."
        )
    return store


def _fmt_diff_value(value: Any, *, max_len: int = 60) -> str:
    """Render a diff value for one-line display."""
    if value is None or value == "":
        return "—"
    if isinstance(value, list):
        text = ", ".join(str(v) for v in value)
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = " ".join(text.split())  # collapse newlines/runs of whitespace
    if len(text) > max_len:
        text = text[: max_len - 1] + "…"
    return text


def _proposal_dict(lp: LoadedProposal) -> dict[str, Any]:
    """JSON row for a loaded proposal: the document plus its filename."""
    doc = lp.proposal.model_dump(mode="json")
    doc["file"] = lp.path.name
    return doc


def _echo_proposal_issues(issues: list[ProposalFileIssue]) -> None:
    """Print unreadable-proposal-file findings to stderr (never crash)."""
    for issue in issues:
        click.echo(f"[warn] proposals/{issue.file}: {issue.problem}", err=True)


def _find_proposal(store: VaultStore, ident: str) -> LoadedProposal:
    from traitprint.proposals import ProposalLookupError, ProposalStore

    try:
        return ProposalStore(store.directory).find(ident)
    except ProposalLookupError as exc:
        raise click.ClickException(str(exc)) from exc


@cli.group()
def proposals() -> None:
    """Review staged writes awaiting approval (proposals/*.json).

    Proposals are how agents on any surface (hosted MCP, web app, or a
    local agent via 'proposals add') stage changes without mutating the
    vault. Approval applies the payload and deletes the proposal file in
    the same git commit; rejection keeps the file with status=rejected.
    """


@proposals.command(name="list")
@click.option(
    "--status",
    type=click.Choice(
        ["pending", "approved", "rejected", "withdrawn"], case_sensitive=False
    ),
    default=None,
    help="Only show proposals with this status.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a JSON array of full proposal documents (plus 'file').",
)
@click.pass_context
def proposals_list(ctx: click.Context, status: str | None, as_json: bool) -> None:
    """List proposals (table format, or JSON with --json)."""
    from traitprint.proposals import ProposalStore

    store = _require_vault(ctx)
    loaded, issues = ProposalStore(store.directory).load_all()
    if status:
        loaded = [lp for lp in loaded if lp.proposal.status == status.lower()]

    _echo_proposal_issues(issues)
    if as_json:
        click.echo(json.dumps([_proposal_dict(lp) for lp in loaded], indent=2))
        return

    if not loaded:
        suffix = f" with status {status!r}" if status else ""
        click.echo(f"No proposals found{suffix}.")
        return

    click.echo(f"{'ID':<8} {'Kind':<18} {'Status':<9} {'Created':<10} Summary")
    click.echo("-" * 80)
    for lp in loaded:
        p = lp.proposal
        click.echo(
            f"{p.id.hex[:8]:<8} {p.kind:<18} {p.status:<9} "
            f"{p.created_at.date().isoformat():<10} {p.summary()}"
        )
    click.echo("")
    click.echo(
        "Use 'traitprint proposals show <id>' (8-char id or full UUID) "
        "to review one."
    )


@proposals.command(name="show")
@click.argument("ident", metavar="ID")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit a {proposal, file, diff} JSON object.",
)
@click.pass_context
def proposals_show(ctx: click.Context, ident: str, as_json: bool) -> None:
    """Show one proposal: payload, rationale, and a current→proposed diff."""
    from traitprint.proposals import proposal_diff

    store = _require_vault(ctx)
    lp = _find_proposal(store, ident)
    p = lp.proposal
    diff_rows = proposal_diff(store.load(), p)

    if as_json:
        click.echo(
            json.dumps(
                {
                    "proposal": p.model_dump(mode="json"),
                    "file": lp.path.name,
                    "diff": diff_rows,
                },
                indent=2,
            )
        )
        return

    click.echo(f"Proposal {p.id}")
    click.echo(f"  file:        proposals/{lp.path.name}")
    click.echo(f"  kind:        {p.kind}")
    click.echo(f"  status:      {p.status}")
    if p.target_id is not None:
        click.echo(f"  target_id:   {p.target_id}")
    click.echo(f"  source:      {p.source or '-'}")
    click.echo(f"  created_at:  {p.created_at.isoformat()}")
    if p.resolved_at is not None:
        click.echo(f"  resolved_at: {p.resolved_at.isoformat()}")
    click.echo(f"  rationale:   {p.rationale or '-'}")

    click.echo("")
    click.echo("Payload:")
    for line in json.dumps(p.payload, indent=2, ensure_ascii=False).splitlines():
        click.echo(f"  {line}")

    click.echo("")
    if p.kind.startswith("update_"):
        click.echo("Diff (current → proposed):")
    else:
        click.echo("Diff (new entity):")
    if not diff_rows:
        click.echo("  (nothing to apply)")
    for row in diff_rows:
        if p.kind.startswith("update_"):
            click.echo(
                f"  {row['field']}: {_fmt_diff_value(row['current'])} "
                f"→ {_fmt_diff_value(row['proposed'])}"
            )
        else:
            click.echo(f"  {row['field']}: {_fmt_diff_value(row['proposed'])}")


def _approve_one(store: VaultStore, lp: LoadedProposal, *, yes: bool) -> None:
    """Approve a single proposal: apply + delete the file, one commit."""
    from traitprint.proposals import ProposalApplyError, apply_proposal

    p = lp.proposal
    if p.status != "pending":
        raise click.ClickException(
            f"Proposal {p.id} is {p.status}, not pending — nothing to approve."
        )

    if not yes:
        click.echo(f"{p.kind}: {p.summary()}")
        if p.rationale:
            click.echo(f"  rationale: {p.rationale}")
        if not click.confirm("Approve this proposal?"):
            click.echo("Cancelled.")
            return

    vault_obj = store.load()
    try:
        description = apply_proposal(vault_obj, p)
    except ProposalApplyError as exc:
        raise click.ClickException(f"Cannot apply proposal {p.id}: {exc}") from exc

    # Contract rule 7: apply the payload and delete the proposal file in
    # the SAME commit.
    store.save(vault_obj)
    lp.path.unlink()
    commit(store.directory, f"Approve proposal: {p.kind} {p.summary()}".rstrip())
    click.echo(f"Approved {p.id}: {description}")


@proposals.command(name="approve")
@click.argument("ident", metavar="[ID]", required=False)
@click.option(
    "--all",
    "approve_all",
    is_flag=True,
    default=False,
    help="Approve every pending proposal in one batch commit (D9 approve-all).",
)
@click.option(
    "--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt."
)
@click.pass_context
def proposals_approve(
    ctx: click.Context, ident: str | None, approve_all: bool, yes: bool
) -> None:
    """Approve a proposal: apply it to the vault, delete the file, commit.

    The payload is validated against the entity schema before anything is
    written (Layer 0 — hard reject); update_* proposals whose target no
    longer exists fail with an actionable error. '--all' applies every
    pending proposal and records ONE batch commit ('Approve N proposals');
    failed items are reported per-line and left pending.
    """
    from traitprint.proposals import ProposalApplyError, ProposalStore, apply_proposal

    store = _require_vault(ctx)

    if approve_all:
        if ident is not None:
            click.echo("--all cannot be combined with a proposal ID.")
            ctx.exit(2)
            return
        pending = ProposalStore(store.directory).pending()
        if not pending:
            click.echo("No pending proposals.")
            return
        if not yes and not click.confirm(
            f"Approve all {len(pending)} pending proposals?"
        ):
            click.echo("Cancelled.")
            return

        vault_obj = store.load()
        applied: list[LoadedProposal] = []
        errors = 0
        for lp in pending:
            p = lp.proposal
            label = f"{p.kind} {p.summary()}".rstrip()
            try:
                description = apply_proposal(vault_obj, p)
            except ProposalApplyError as exc:
                click.echo(f"[err] {label}: {exc}")
                errors += 1
                continue
            click.echo(f"[ok] {label}: {description}")
            applied.append(lp)
        if applied:
            store.save(vault_obj)
            for lp in applied:
                lp.path.unlink()
            noun = "proposal" if len(applied) == 1 else "proposals"
            commit(store.directory, f"Approve {len(applied)} {noun}")
        click.echo(f"Summary: approved {len(applied)}, errors {errors}")
        if errors:
            ctx.exit(1)
        return

    if ident is None:
        click.echo("Pass a proposal ID, or --all to approve every pending one.")
        ctx.exit(2)
        return
    _approve_one(store, _find_proposal(store, ident), yes=yes)


@proposals.command(name="reject")
@click.argument("ident", metavar="ID")
@click.option(
    "--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt."
)
@click.pass_context
def proposals_reject(ctx: click.Context, ident: str, yes: bool) -> None:
    """Reject a proposal: set status=rejected + resolved_at, keep the file."""
    from datetime import datetime, timezone

    from traitprint.proposals import ProposalStore

    store = _require_vault(ctx)
    lp = _find_proposal(store, ident)
    p = lp.proposal
    if p.status != "pending":
        raise click.ClickException(
            f"Proposal {p.id} is {p.status}, not pending — nothing to reject."
        )

    if not yes:
        click.echo(f"{p.kind}: {p.summary()}")
        if not click.confirm("Reject this proposal?"):
            click.echo("Cancelled.")
            return

    p.status = "rejected"
    p.resolved_at = datetime.now(timezone.utc)
    ProposalStore(store.directory).save(p, path=lp.path)
    commit(store.directory, f"Reject proposal: {p.kind} {p.summary()}".rstrip())
    click.echo(f"Rejected {p.id} (file kept: proposals/{lp.path.name}).")


@proposals.command(name="add")
@click.option(
    "--kind",
    "-k",
    required=True,
    type=click.Choice(list(PROPOSAL_KINDS), case_sensitive=False),
    help="Proposal kind (add_* / update_* / update_profile).",
)
@click.option(
    "--target-id",
    type=UUID_PARAM,
    default=None,
    help="UUID of the entity being changed (required for update_* kinds).",
)
@click.option(
    "--rationale",
    "-r",
    default="",
    help="Why this change is right; shown at review time.",
)
@click.option(
    "--source",
    default="cli",
    show_default=True,
    help="Proposer identity (e.g. 'cli', 'import-resume', an agent name).",
)
@click.option(
    "--payload-json",
    "payload_json",
    type=click.File("r"),
    required=True,
    help="Payload as a JSON object, from a file or '-' for stdin: the full "
    "entity for add_*, only the changed fields for update_*. Narrative "
    "text travels in payload.body for experiences/stories/philosophies.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the created proposal document (plus 'file') as JSON.",
)
@click.pass_context
def proposals_add(
    ctx: click.Context,
    kind: str,
    target_id: UUID | None,
    rationale: str,
    source: str,
    payload_json: IO[str],
    as_json: bool,
) -> None:
    """Stage a new pending proposal (the local 'propose' write path).

    Validates the kind/target rules and the payload keys against the
    vault v1 proposal contract — the same checks the hosted MCP server's
    vault_propose tool runs — then writes proposals/<kind>-<id8>.json and
    commits. Nothing touches the vault until the proposal is approved.
    """
    from traitprint.proposals import ProposalStore, ProposalValidationError

    store = _require_vault(ctx)
    try:
        data = json.loads(payload_json.read())
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON payload: {exc}") from exc
    if not isinstance(data, dict):
        raise click.ClickException(
            f"Payload must be a JSON object, got {type(data).__name__}"
        )

    try:
        lp = ProposalStore(store.directory).create(
            kind.lower(),
            data,
            target_id=target_id,
            rationale=rationale,
            source=source,
        )
    except ProposalValidationError as exc:
        for problem in str(exc).split("; "):
            click.echo(f"[err] proposal: {problem}")
        ctx.exit(1)
        return

    p = lp.proposal
    commit(store.directory, f"Add proposal: {p.kind} {p.summary()}".rstrip())
    if as_json:
        click.echo(json.dumps(_proposal_dict(lp), indent=2))
        return
    click.echo(
        f"Created pending proposal {p.kind} [{p.id}] "
        f"→ proposals/{lp.path.name}"
    )
    click.echo(
        "Review with 'traitprint proposals show' / approve with "
        "'traitprint proposals approve'."
    )


@proposals.command(name="validate")
@click.argument("paths", nargs=-1, type=click.Path(exists=True))
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit results as JSON: {valid, checked, results: [...]}.",
)
@click.pass_context
def proposals_validate(
    ctx: click.Context, paths: tuple[str, ...], as_json: bool
) -> None:
    """Validate proposal JSON files against the contract (read-only).

    Runs the exact checks 'proposals add', the review queue, and the
    hosted vault_propose enforce — the $defs/proposal document shape
    plus the kind/target/payload-key rules — without writing anything.
    No vault is required: point it at files any external tool produced
    before staging them. A directory argument validates every *.json
    inside it. Exit 0 when every document is valid, 1 otherwise.
    """
    from pathlib import Path as _Path

    from traitprint.proposals import validate_proposal_document

    files: list[_Path] = []
    for raw in paths:
        path = _Path(raw)
        if path.is_dir():
            files.extend(sorted(path.glob("*.json")))
        else:
            files.append(path)
    if not files:
        raise click.UsageError(
            "pass at least one proposal .json file, or a directory "
            "containing proposals/*.json files"
        )

    results: list[dict[str, Any]] = []
    for file in files:
        try:
            data: Any = json.loads(file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            problems = [f"invalid JSON: {exc}"]
        else:
            problems = validate_proposal_document(data)
        results.append(
            {"file": str(file), "valid": not problems, "problems": problems}
        )

    invalid = sum(1 for row in results if not row["valid"])
    if as_json:
        doc = {"valid": invalid == 0, "checked": len(results), "results": results}
        click.echo(json.dumps(doc, indent=2))
    else:
        for row in results:
            if row["valid"]:
                click.echo(f"[ok] {row['file']}")
            else:
                for problem in row["problems"]:
                    click.echo(f"[err] {row['file']}: {problem}")
        click.echo(f"Summary: {len(results) - invalid} valid, {invalid} invalid")
    if invalid:
        ctx.exit(1)


@proposals.command(name="contract")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the contract as a JSON document.",
)
def proposals_contract(as_json: bool) -> None:
    """Print the proposal contract: kinds, payload keys, statuses.

    A machine-readable statement of the validation that 'proposals
    add', the review queue, and the hosted vault_propose all enforce
    (vault v1, $defs/proposal). External tools that stage proposals
    from another language can vendor the --json document, or diff it
    against a live install to catch contract drift. Needs no vault;
    writes nothing.
    """
    from traitprint.proposals import proposal_contract

    doc = proposal_contract()
    if as_json:
        click.echo(json.dumps(doc, indent=2))
        return
    click.echo(f"Contract: {doc['contract']} {doc['definition']}")
    click.echo(f"Statuses: {', '.join(doc['statuses'])}")
    click.echo(
        "update_profile basics keys: " + ", ".join(doc["profile_basics_keys"])
    )
    click.echo("Kinds:")
    for kind in doc["kinds"]:
        target = (
            "required" if kind in doc["target_id_required_for"] else "forbidden"
        )
        click.echo(f"  {kind}  (target_id {target})")
        click.echo(
            "    allowed payload keys: " + ", ".join(doc["payload_keys"][kind])
        )
        required = doc["required_payload_keys"].get(kind, [])
        if required:
            click.echo("    required: " + ", ".join(required))


# --- export (top-level alias of ``vault export``) ---


@cli.command(name="export")
@click.option(
    "--format",
    "-f",
    "fmt",
    type=click.Choice(_EXPORT_FORMAT_CHOICES, case_sensitive=False),
    default="json",
    show_default=True,
    callback=_normalize_export_format,
    help="Export format. ``json-resume`` is accepted as a synonym for ``jsonresume``.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Write to file (or, for career-bundle, a directory) instead of stdout.",
)
@click.option(
    "--pack-name",
    default=None,
    help="Override the pack name (synthpanel-persona only).",
)
@click.option(
    "--lens",
    "lens_ref",
    default=None,
    help=(
        "Project cv.md through a positioning lens, by slug or id "
        "(career-bundle only)."
    ),
)
@click.option(
    "--zip",
    "as_zip",
    is_flag=True,
    default=False,
    help="Write the bundle as a single .zip archive (career-bundle only).",
)
@click.pass_context
def export_cmd(
    ctx: click.Context,
    fmt: str,
    output: str | None,
    pack_name: str | None,
    lens_ref: str | None,
    as_zip: bool,
) -> None:
    """Export the vault in a format consumable by other tools.

    Alias of ``traitprint vault export``. ``--format synthpanel-persona``
    emits a SynthPanel persona pack (YAML) that can be fed directly to
    ``synthpanel panel run --personas <file>``.
    """
    ctx.invoke(
        vault_export,
        fmt=fmt,
        output=output,
        pack_name=pack_name,
        lens_ref=lens_ref,
        as_zip=as_zip,
    )


# --- mcp-serve ---


@cli.command(name="mcp-serve")
@click.pass_context
def mcp_serve(ctx: click.Context) -> None:
    """Run the Traitprint MCP server over stdio.

    Exposes four tools (get_profile_summary, search_skills, find_story,
    get_philosophy) with response schemas that mirror the cloud MCP
    server so agents can swap local ↔ cloud by changing a URL, plus five
    local-only prompts (fill_vault, mine_story_gaps, discover_skills,
    draft_star_story, audit_coherence) adapted from the Cloud mining engine.
    """
    from traitprint.mcp_server import run_stdio

    store = _get_store(ctx)
    if not store.exists():
        raise click.ClickException(
            f"No vault found at {store.directory}. Run 'traitprint init' first."
        )
    run_stdio(store)


# ------------------------------------------------------------------
# agents: agent-runtime entrypoint scaffolding
# ------------------------------------------------------------------


@cli.group(name="agents")
def agents_group() -> None:
    """Agent-runtime entrypoints (wrappers, skills, MCP wiring)."""


@agents_group.command(name="init")
@click.argument("directory", type=click.Path(file_okay=False), default=".")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the scaffold report (files, MCP snippets, next steps) as JSON.",
)
@click.pass_context
def agents_init(ctx: click.Context, directory: str, as_json: bool) -> None:
    """Scaffold agent-CLI entrypoints in DIRECTORY (default: cwd).

    Writes thin wrapper files (CLAUDE.md, QWEN.md, .grok/GROK.md) that all
    delegate to one canonical AGENTS.md, copies the shipped Agent Skills to
    .agents/skills/ and .claude/skills/, and registers `traitprint
    mcp-serve` in project-scoped MCP configs (.mcp.json, opencode.json,
    .qwen/settings.json, .grok/settings.json). Codex CLI, OpenCode, and
    Kimi CLI read AGENTS.md natively; registrations that live in the home
    directory (Codex CLI, Kimi CLI) are printed as snippets — nothing
    outside DIRECTORY is ever touched, and existing files are never
    overwritten. Gemini CLI is not scaffolded: the published Gemini
    extension (gemini-extension.json) already covers it. Already using
    `npx skills add DataViking-Tech/traitprint`? This command mainly adds
    the MCP wiring and a Node-free (pip-only) bootstrap path.
    """
    from traitprint.agents_scaffold import (
        MCP_REGISTRATIONS,
        NATIVE_AGENTS_MD_RUNTIMES,
        SKILL_DESTINATIONS,
        ScaffoldError,
        scaffold,
    )

    store = _get_store(ctx)
    vault_exists = store.exists()
    try:
        report = scaffold(Path(directory))
    except ScaffoldError as exc:
        raise click.ClickException(str(exc)) from exc

    written_paths = {f.path for f in report.files if f.written}
    pending = [reg for reg in MCP_REGISTRATIONS if reg.path not in written_paths]

    next_steps: list[str] = []
    if not vault_exists:
        next_steps.append("Create your vault: traitprint init")
    if pending:
        labels = ", ".join(reg.label for reg in pending)
        next_steps.append(
            f"Register 'traitprint mcp-serve' with {labels} "
            "(see the MCP snippets)."
        )
    next_steps.append(
        f"Launch your agent CLI in {report.directory} and ask for the "
        "traitprint-fill-vault interview to build out your vault."
    )

    if as_json:
        payload = {
            "directory": report.directory,
            "written": report.written,
            "skipped": report.skipped,
            "mcp": [
                {
                    "runtime": reg.runtime,
                    "label": reg.label,
                    "path": reg.path,
                    "in_project": reg.in_project,
                    "written": reg.path in written_paths,
                    "snippet": reg.snippet,
                }
                for reg in MCP_REGISTRATIONS
            ],
            "next_steps": next_steps,
        }
        click.echo(json.dumps(payload, indent=2))
        return

    click.echo(f"Agent entrypoints scaffolded in {report.directory}")
    click.echo()
    for f in report.files:
        if f.kind == "skill":
            continue
        tag = "[ok]  " if f.written else "[skip]"
        suffix = "" if f.written else " (exists — kept)"
        click.echo(f"  {tag} {f.path} — {f.label}{suffix}")
    skill_files = [f for f in report.files if f.kind == "skill"]
    for dest in SKILL_DESTINATIONS:
        group = [f for f in skill_files if f.path.startswith(f"{dest}/")]
        wrote = sum(1 for f in group if f.written)
        kept = len(group) - wrote
        detail = f"{wrote} files" + (f", {kept} existing kept" if kept else "")
        tag = "[ok]  " if wrote else "[skip]"
        click.echo(f"  {tag} {dest}/ — Agent Skills ({detail})")

    click.echo()
    click.echo(
        f"{', '.join(NATIVE_AGENTS_MD_RUNTIMES)} read AGENTS.md natively — "
        "no wrapper file needed."
    )
    click.echo(
        "Gemini CLI is covered by the published extension "
        "(gemini-extension.json)."
    )

    if pending:
        click.echo()
        click.echo("MCP registration for 'traitprint mcp-serve':")
        for reg in pending:
            click.echo(f"  {reg.label} — add to {reg.path}:")
            for line in reg.snippet.rstrip("\n").splitlines():
                click.echo(f"    {line}")

    click.echo()
    click.echo("Next steps:")
    for i, step in enumerate(next_steps, start=1):
        click.echo(f"  {i}. {step}")


# ------------------------------------------------------------------
# Cloud sync: login / logout / push / pull
# ------------------------------------------------------------------


def _require_cloud_extras() -> None:
    """Verify the [cloud] extras are installed; raise a friendly error if not.

    The base ``pip install traitprint`` never imports ``httpx`` at module
    load — only ``cloud.py`` and ``providers/*`` import it, and neither
    path is reachable without the ``[cloud]`` or ``[import]`` extras.
    Cloud sync (login/logout/push/pull) lives behind the ``[cloud]`` extra.
    """
    try:
        import httpx  # noqa: F401
    except ImportError as exc:
        raise click.ClickException(
            "Cloud sync requires: pip install traitprint[cloud]"
        ) from exc


def _require_credentials(store: VaultStore) -> Credentials:
    from traitprint.credentials import (
        DEFAULT_API_URL,
        Credentials,
        CredentialsStore,
    )

    stored = CredentialsStore(store.directory).load()

    env_token = os.environ.get("TRAITPRINT_API_TOKEN")
    if env_token:
        # Env token wins over file token; reuse file's email/api_url metadata
        # when present so output stays informative.
        api_url = (
            (stored.api_url if stored else None)
            or os.environ.get("TRAITPRINT_API_URL")
            or DEFAULT_API_URL
        )
        email = stored.email if stored else ""
        return Credentials(api_url=api_url, email=email, token=env_token)

    if stored is None or not stored.token:
        raise click.ClickException(
            "Not logged in. Run 'traitprint login' or set TRAITPRINT_API_TOKEN."
        )
    return stored


def _render_plan(plan: SyncPlan) -> str:
    return f"[{plan.direction}] {plan.reason}"


def _pre_push_audit(
    ctx: click.Context, store: VaultStore, *, strict: bool, enforce: bool
) -> None:
    """Run the coherence audit before a push.

    Blocks (exit 1) on critical findings — broken stories, contradicting
    roles, anything you should never publish. When ``strict`` is set, major
    findings (warnings, e.g. dangling references) block too, matching
    ``traitprint vault audit --strict``. Non-blocking findings are
    summarized as an advisory line. When ``enforce`` is False (a dry run)
    nothing blocks; findings are advisory only.
    """
    from traitprint.audit import audit_vault, severity_rank, summarize

    findings = audit_vault(store.load()).findings
    if not findings:
        return

    block_floor = severity_rank("major") if strict else severity_rank("critical")
    blocking = [f for f in findings if severity_rank(f.severity) >= block_floor]

    if enforce and blocking:
        click.echo("Pre-push coherence audit failed:")
        for f in blocking:
            click.echo(f"{_SEVERITY_TAG[f.severity]} {f.section}: {f.message}")
        s = summarize(blocking)
        click.echo("")
        click.echo(
            f"Blocking: {s['critical']} critical, {s['major']} major. "
            "Fix these, or re-run with 'traitprint push --skip-audit'."
        )
        ctx.exit(1)

    # Nothing blocked — surface remaining findings as a one-line advisory.
    advisory = findings if not enforce else [f for f in findings if f not in blocking]
    counts = summarize(advisory)
    if counts["total"]:
        click.echo(
            f"Coherence note: {counts['critical']} critical, "
            f"{counts['major']} major, {counts['minor']} minor — "
            "run 'traitprint vault audit' to review."
        )


@cli.command(name="login")
@click.option(
    "--email",
    "-e",
    default=None,
    envvar="TRAITPRINT_EMAIL",
    help="Account email (or set TRAITPRINT_EMAIL).",
)
@click.option(
    "--password",
    "-p",
    default=None,
    envvar="TRAITPRINT_PASSWORD",
    help=(
        "Account password (or set TRAITPRINT_PASSWORD). "
        "Avoid passing on the command line — it leaks via shell history. "
        "Prefer --token for non-interactive use."
    ),
)
@click.option(
    "--token",
    "-t",
    default=None,
    envvar="TRAITPRINT_API_TOKEN",
    help=(
        "API token (or set TRAITPRINT_API_TOKEN). "
        "Skips email/password entirely — recommended for CI and agents."
    ),
)
@click.option(
    "--api-url",
    default=None,
    envvar="TRAITPRINT_API_URL",
    help="Override the cloud API URL (default: https://api.traitprint.com).",
)
@click.pass_context
def login_cmd(
    ctx: click.Context,
    email: str | None,
    password: str | None,
    token: str | None,
    api_url: str | None,
) -> None:
    """Log in to Traitprint cloud and save credentials to ``.credentials``.

    \b
    Auth precedence (highest first):
      1. --token / TRAITPRINT_API_TOKEN  (skips email/password)
      2. --password / TRAITPRINT_PASSWORD env var
      3. Interactive password prompt (TTY only)
    """
    _require_cloud_extras()
    from traitprint.cloud import AuthError, CloudClient, CloudError
    from traitprint.credentials import (
        DEFAULT_API_URL,
        Credentials,
        CredentialsStore,
    )

    store = _get_store(ctx)
    if not store.exists():
        raise click.ClickException(
            f"No vault found at {store.directory}. Run 'traitprint init' first."
        )

    final_api_url = api_url or DEFAULT_API_URL

    if token:
        creds = Credentials(api_url=final_api_url, email=email or "", token=token)
        CredentialsStore(store.directory).save(creds)
        ident = f" as {email}" if email else ""
        click.echo(f"Logged in with API token{ident}.")
        return

    if not email:
        if not sys.stdin.isatty():
            raise click.ClickException(
                "No email provided. In non-interactive shells, pass --email, "
                "set TRAITPRINT_EMAIL, or use --token / TRAITPRINT_API_TOKEN."
            )
        email = click.prompt("Email")

    if not password:
        if not sys.stdin.isatty():
            raise click.ClickException(
                "No password provided. In non-interactive shells, set "
                "TRAITPRINT_PASSWORD or — recommended — TRAITPRINT_API_TOKEN."
            )
        password = click.prompt("Password", hide_input=True)

    with CloudClient(final_api_url) as client:
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
    _require_cloud_extras()
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
@click.option(
    "--strict",
    is_flag=True,
    default=False,
    help="Run the pre-push audit in strict mode — warnings block too, "
    "not just errors.",
)
@click.option(
    "--skip-audit",
    is_flag=True,
    default=False,
    help="Skip the pre-push coherence audit entirely.",
)
@click.pass_context
def push_cmd(
    ctx: click.Context, dry_run: bool, strict: bool, skip_audit: bool
) -> None:
    """Upload the local vault to Traitprint cloud (legacy last-write-wins).

    Deprecated: prefer 'traitprint sync push' (git-native sync-v1, real
    merges instead of whole-vault last-write-wins). This transport stays
    for servers without the /vault-git endpoints.

    Before uploading, runs a coherence audit and blocks on error-level
    findings (broken stories, contradictions). Warnings such as dangling
    references never block by default; pass --strict to block on warnings
    too, or --skip-audit to bypass the check.
    """
    _require_cloud_extras()
    from traitprint.cloud import AuthError, CloudClient, CloudError, ConflictError
    from traitprint.sync import do_push

    store = _get_store(ctx)
    if not store.exists():
        raise click.ClickException(
            f"No vault found at {store.directory}. Run 'traitprint init' first."
        )
    creds = _require_credentials(store)

    if not skip_audit:
        _pre_push_audit(ctx, store, strict=strict, enforce=not dry_run)

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
    if plan.direction == "push":
        click.echo(
            "Uploading your full vault JSON to traitprint.com. "
            "What leaves your machine, what we store, and how to delete "
            "everything: https://github.com/DataViking-Tech/traitprint/"
            "blob/main/docs/privacy.md"
        )
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
    """Download the cloud vault to local disk (legacy last-write-wins).

    Deprecated: prefer 'traitprint sync pull' (git-native sync-v1, real
    merges instead of whole-vault last-write-wins). This transport stays
    for servers without the /vault-git endpoints.
    """
    _require_cloud_extras()
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


# ------------------------------------------------------------------
# sync command group: git-native sync-v1 (tp-an-020)
# ------------------------------------------------------------------


_SYNC_RELATION_NOTES = {
    "empty": "no commits anywhere",
    "first-push-pending": "run 'traitprint sync push' to upload",
    "in-sync": "local and server match",
    "ahead": "run 'traitprint sync push' to upload local commits",
    "behind": "run 'traitprint sync pull' to fetch server commits",
    "diverged": "run 'traitprint sync pull', resolve, then 'traitprint sync push'",
    "unknown": "server history not fetched yet — run 'traitprint sync pull'",
}


def _short(sha: str | None) -> str:
    return sha[:12] if sha else "(none)"


def _echo_ingest(ingest: IngestReport | None) -> None:
    """Render the server's ingest report (status + quarantined entities)."""
    if ingest is None or not ingest.status:
        return
    if ingest.status == "quarantined" and ingest.quarantined:
        click.echo(f"Ingest: quarantined ({len(ingest.quarantined)} entities)")
        for item in ingest.quarantined:
            file = item.get("file", "")
            entity = item.get("entity_id", "")
            reason = item.get("reason", "")
            click.echo(f"  - {file} ({entity}): {reason}")
    else:
        click.echo(f"Ingest: {ingest.status}")


def _ingest_status_value(ingest: IngestReport | None) -> str | None:
    return ingest.status if ingest is not None and ingest.status else None


def _echo_conflict_guidance(vault_dir: str, conflicts: list[str]) -> None:
    """Print the merge-conflict UX: files + the exact commands to finish."""
    click.echo(f"Merge conflicts in {len(conflicts)} file(s):")
    for name in conflicts:
        click.echo(f"  - {name}")
    click.echo("")
    click.echo(
        "Resolve the conflict markers (<<<<<<</=======/>>>>>>>) in each "
        "file, then run:"
    )
    click.echo(f"  git -C {vault_dir} add -A")
    click.echo(f'  git -C {vault_dir} commit -m "Merge remote vault changes"')
    click.echo("Then run 'traitprint sync push' to upload the merge.")
    click.echo(f"To abandon the merge instead: git -C {vault_dir} merge --abort")


@cli.group(name="sync")
def sync_group() -> None:
    """Git-native cloud sync (sync-v1: git bundles over HTTPS).

    Syncs the vault's git history with your hosted remote. Concurrent
    edits to different files merge cleanly; real conflicts surface as
    standard git conflicts in the affected files only. Replaces the
    legacy whole-vault 'push'/'pull'.
    """


def _sync_client(ctx: click.Context) -> tuple[VaultStore, GitSyncClient]:
    """Resolve the vault + an authenticated sync-v1 client (shared plumbing)."""
    _require_cloud_extras()
    from traitprint.gitsync import GitSyncClient

    store = _get_store(ctx)
    if not store.exists():
        raise click.ClickException(
            f"No vault found at {store.directory}. Run 'traitprint init' first."
        )
    creds = _require_credentials(store)
    return store, GitSyncClient.from_credentials(creds)


@sync_group.command(name="push")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit {pushed, head, server_head, ingest_status} as JSON.",
)
@click.pass_context
def sync_push_cmd(ctx: click.Context, as_json: bool) -> None:
    """Push local vault commits to the hosted remote (git bundle).

    Uncommitted hand edits are committed first. Sends a thin bundle
    against the last-known server head (full bundle on first push). If
    the server has commits you don't have (409), run
    'traitprint sync pull', resolve any conflicts, then push again.
    """
    from traitprint.gitsync import (
        GitSyncError,
        NonFastForwardError,
        SchemaViolationError,
        SyncAuthError,
        sync_push,
    )

    store, client = _sync_client(ctx)
    with client:
        try:
            outcome = sync_push(store.directory, client)
        except NonFastForwardError as exc:
            if as_json:
                payload: dict[str, Any] = {
                    "pushed": False,
                    "head": head_sha(store.directory, short=False) or None,
                    "server_head": exc.server_head,
                    "ingest_status": None,
                    "error": {
                        "code": "non_fast_forward",
                        "message": str(exc),
                        "hint": exc.hint,
                    },
                }
                click.echo(json.dumps(payload, indent=2))
            else:
                click.echo(str(exc))
                click.echo(f"Server head: {_short(exc.server_head)}")
                click.echo(exc.hint)
            ctx.exit(1)
        except SchemaViolationError as exc:
            if as_json:
                payload = {
                    "pushed": False,
                    "head": head_sha(store.directory, short=False) or None,
                    "server_head": None,
                    "ingest_status": None,
                    "error": {
                        "code": "schema_violation",
                        "message": str(exc),
                        "hint": exc.hint,
                    },
                    "violations": [v.as_dict() for v in exc.violations],
                }
                click.echo(json.dumps(payload, indent=2))
            else:
                click.echo(str(exc))
                for v in exc.violations:
                    location = f"{v.file} @ {v.pointer}" if v.pointer else v.file
                    click.echo(f"[err] {location}: {v.message}")
                    if v.hint:
                        click.echo(f"      hint: {v.hint}")
                if exc.hint:
                    click.echo(exc.hint)
            ctx.exit(1)
        except SyncAuthError as exc:
            raise click.ClickException(str(exc)) from exc
        except GitSyncError as exc:
            message = str(exc) + (f" {exc.hint}" if exc.hint else "")
            raise click.ClickException(message) from exc

    if as_json:
        click.echo(
            json.dumps(
                {
                    "pushed": outcome.pushed,
                    "head": outcome.head,
                    "server_head": outcome.server_head,
                    "ingest_status": _ingest_status_value(outcome.ingest),
                },
                indent=2,
            )
        )
        return
    if not outcome.pushed:
        click.echo(f"Already up to date with the server ({_short(outcome.head)}).")
        return
    kind = "full bundle" if outcome.full_bundle else "thin bundle"
    if outcome.retried_full:
        kind += ", retried after missing_prerequisites"
    click.echo(
        f"Pushed {outcome.commits} commit(s) to server main "
        f"({_short(outcome.server_head)}, {kind})."
    )
    _echo_ingest(outcome.ingest)


@sync_group.command(name="pull")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit {fetched, result, conflicts, head} as JSON.",
)
@click.pass_context
def sync_pull_cmd(ctx: click.Context, as_json: bool) -> None:
    """Fetch server commits and fast-forward or merge them locally.

    Applies the server's bundle with git fetch, then fast-forwards when
    possible or merges otherwise. On merge conflicts the merge is left
    in progress: resolve the listed files, commit, then
    'traitprint sync push' (exit code 1 until resolved).
    """
    from traitprint.gitsync import GitSyncError, SyncAuthError, sync_pull

    store, client = _sync_client(ctx)
    with client:
        try:
            outcome = sync_pull(store.directory, client)
        except SyncAuthError as exc:
            raise click.ClickException(str(exc)) from exc
        except GitSyncError as exc:
            message = str(exc) + (f" {exc.hint}" if exc.hint else "")
            raise click.ClickException(message) from exc

    if as_json:
        click.echo(
            json.dumps(
                {
                    "fetched": outcome.fetched,
                    "result": outcome.mode,
                    "conflicts": outcome.conflicts,
                    "head": outcome.head or None,
                },
                indent=2,
            )
        )
        if outcome.mode == "conflicts":
            ctx.exit(1)
        return
    if outcome.mode == "up_to_date":
        if outcome.fetched:
            click.echo(
                "Fetched server commits — already part of local history "
                f"({_short(outcome.head)})."
            )
        else:
            click.echo(f"Already up to date ({_short(outcome.head)}).")
        return
    if outcome.mode == "fast_forward":
        click.echo(f"Fast-forwarded to {_short(outcome.head)}.")
        return
    if outcome.mode == "merged":
        click.echo(
            f"Merged remote changes into the local vault "
            f"(new head {_short(outcome.head)}). "
            "Run 'traitprint sync push' to upload the merge."
        )
        return
    _echo_conflict_guidance(str(store.directory), outcome.conflicts)
    ctx.exit(1)


@sync_group.command(name="status")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit {local_head, server_head, ingest_status, quarantine_summary} as JSON.",
)
@click.pass_context
def sync_status_cmd(ctx: click.Context, as_json: bool) -> None:
    """Show local and server sync state (GET /vault-git/info)."""
    from traitprint.gitsync import GitSyncError, SyncAuthError, sync_status

    store, client = _sync_client(ctx)
    with client:
        try:
            outcome = sync_status(store.directory, client)
        except SyncAuthError as exc:
            raise click.ClickException(str(exc)) from exc
        except GitSyncError as exc:
            message = str(exc) + (f" {exc.hint}" if exc.hint else "")
            raise click.ClickException(message) from exc

    from traitprint.taxonomy import (
        load_taxonomy_lineage,
        load_taxonomy_version,
        taxonomy_update_advisory,
    )

    tax = outcome.server_taxonomy
    advisory = (
        taxonomy_update_advisory(tax.version, tax.lineage) if tax is not None else None
    )

    if as_json:
        click.echo(
            json.dumps(
                {
                    "local_head": outcome.local_head,
                    "server_head": outcome.server_head,
                    "ingest_status": _ingest_status_value(outcome.ingest),
                    "quarantine_summary": {
                        "count": len(outcome.ingest.quarantined),
                        "items": outcome.ingest.quarantined,
                    },
                    "relation": outcome.relation,
                    "taxonomy": {
                        "local_version": load_taxonomy_version(),
                        "local_lineage": load_taxonomy_lineage(),
                        "server_version": tax.version if tax else None,
                        "server_lineage": tax.lineage if tax else None,
                        "advisory": advisory,
                    },
                },
                indent=2,
            )
        )
        return

    click.echo(f"Local HEAD:  {_short(outcome.local_head)}")
    click.echo(f"Server HEAD: {_short(outcome.server_head)}")
    note = _SYNC_RELATION_NOTES.get(outcome.relation, "")
    click.echo(f"State: {outcome.relation}" + (f" — {note}" if note else ""))
    _echo_ingest(outcome.ingest)
    if advisory:
        click.echo(f"⚠ {advisory}")


@sync_group.command(name="taxonomy")
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit {refreshed, local_version, server_version, ...} as JSON.",
)
@click.pass_context
def sync_taxonomy_cmd(ctx: click.Context, as_json: bool) -> None:
    """Refresh the local skill taxonomy from the server (GET /vault-git/taxonomy).

    Downloads the server's canonical taxonomy artifact when it is a newer version
    of the SAME lineage and caches it locally, so the skill taxonomy refreshes
    without a pip upgrade. A no-op when already current; a server on a DIFFERENT
    lineage is reported but not adopted automatically.
    """
    from traitprint.gitsync import GitSyncError, SyncAuthError
    from traitprint.taxonomy import (
        load_taxonomy_lineage,
        load_taxonomy_version,
        write_taxonomy_cache,
    )

    store, client = _sync_client(ctx)
    local_version = load_taxonomy_version()
    local_lineage = load_taxonomy_lineage()
    with client:
        try:
            raw = client.download_taxonomy(local_version, local_lineage)
        except SyncAuthError as exc:
            raise click.ClickException(str(exc)) from exc
        except GitSyncError as exc:
            message = str(exc) + (f" {exc.hint}" if exc.hint else "")
            raise click.ClickException(message) from exc

    refreshed = False
    note: str | None = None
    server_version: int | None = None
    server_lineage: str | None = None
    if raw is None:
        note = "already up to date"
    else:
        server_version = int(raw.get("version", 0))
        server_lineage = str(raw.get("lineage", ""))
        if server_lineage != local_lineage:
            note = (
                f"server taxonomy lineage is {server_lineage!r}, local is "
                f"{local_lineage!r} — different taxonomies; not adopting automatically"
            )
        else:
            try:
                write_taxonomy_cache(raw)
            except ValueError as exc:
                raise click.ClickException(
                    f"Server returned an invalid taxonomy artifact: {exc}"
                ) from exc
            refreshed = True

    if as_json:
        click.echo(
            json.dumps(
                {
                    "refreshed": refreshed,
                    "local_version": local_version,
                    "local_lineage": local_lineage,
                    "server_version": server_version,
                    "server_lineage": server_lineage,
                    "note": note,
                },
                indent=2,
            )
        )
        return

    if refreshed:
        click.echo(f"✓ Taxonomy refreshed: v{local_version} → v{server_version}")
    elif raw is None:
        click.echo(f"Taxonomy already up to date ({local_lineage} v{local_version}).")
    else:
        click.echo(f"⚠ {note}")
