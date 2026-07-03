"""Export a Traitprint vault as a career working-directory bundle.

The bundle is a small user-layer file tree an agent-CLI job-search tool
can consume directly::

    cv.md                          # ATS-style resume markdown
    interview-prep/story-bank.md   # one STAR block per story
    config/profile.yml             # candidate/narrative/target_roles

The layout is compatible with career-ops-style working directories
(nominative reference; the bundle itself is brand-neutral and the token
is ``career-bundle``). Derivation is deterministic — no LLM, no network.

``profile.yml`` intentionally carries *commented* placeholder keys
(phone, links, compensation) for the consumer's own onboarding to fill:
``yaml.safe_dump`` cannot emit comments, so the file is string-templated;
tests round-trip-parse it to keep it valid YAML.
"""

from __future__ import annotations

import json

from traitprint.export import _export_markdown, _sort_skills
from traitprint.schema import (
    LensSchema,
    SalienceLevel,
    SkillSchema,
    StorySchema,
    VaultSchema,
)

# Schema version stamped into config/profile.yml. Consumers can branch on
# this to stay forward-compatible when Traitprint adds new fields.
CAREER_BUNDLE_EXPORT_VERSION = 1


def _yaml_str(value: str) -> str:
    """A JSON string literal — valid YAML, safe for any content."""
    return json.dumps(value, ensure_ascii=False)


# ------------------------------------------------------------------
# cv.md — ATS-style resume (recruiter-assertable content only)
# ------------------------------------------------------------------


def _lens_skills(vault: VaultSchema, lens: LensSchema | None) -> list[SkillSchema]:
    """Skills for the CV: suppressed dropped, core first, each group
    sorted by proficiency then name (the canonical export order)."""
    if lens is None:
        return _sort_skills(vault.skills)
    core = [
        s for s in vault.skills if lens.salience_for(s.id) == SalienceLevel.CORE
    ]
    supporting = [
        s
        for s in vault.skills
        if lens.salience_for(s.id) == SalienceLevel.SUPPORTING
    ]
    return _sort_skills(core) + _sort_skills(supporting)


def _cv_markdown(vault: VaultSchema, lens: LensSchema | None) -> str:
    """ATS-style cv.md: the canonical markdown export with header renames,
    lens projection, and the Stories/Philosophy sections dropped — only
    recruiter-assertable content belongs in a CV."""
    projected = vault.model_copy(deep=True)
    if lens is not None:
        if lens.headline_override:
            projected.profile.headline = lens.headline_override
        if lens.bio_override:
            projected.profile.summary = lens.bio_override
        signature = [
            exp
            for ref in lens.signature_experience_ids
            for exp in projected.experiences
            if exp.id == ref
        ]
        signature_ids = {exp.id for exp in signature}
        projected.experiences = signature + [
            exp for exp in projected.experiences if exp.id not in signature_ids
        ]

    skills = _lens_skills(vault, lens)
    # Render only header/Summary/Experience/Education via the canonical
    # exporter; the Skills section is appended manually so lens ordering
    # (core first) survives — _export_markdown re-sorts by proficiency.
    projected.skills = []
    projected.stories = []
    projected.philosophies = []
    base = _export_markdown(projected).rstrip()
    base = base.replace("\n## Summary\n", "\n## Professional Summary\n", 1)
    base = base.replace("\n## Experience\n", "\n## Work Experience\n", 1)

    lines = [base]
    if skills:
        lines.append("")
        lines.append("## Skills")
        for skill in skills:
            lines.append(
                f"- **{skill.name}** — {skill.proficiency}/5"
                + (f" ({skill.category})" if skill.category else "")
            )
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------
# interview-prep/story-bank.md — one STAR block per story
# ------------------------------------------------------------------


def _story_block(story: StorySchema, vault: VaultSchema) -> list[str]:
    theme = story.theme_tags[0] if story.theme_tags else ""
    heading = f"### [{theme}] {story.title}" if theme else f"### {story.title}"
    lines = [heading, ""]
    for label, value in (
        ("Situation", story.situation),
        ("Task", story.task),
        ("Action", story.action),
        ("Result", story.result),
    ):
        if value:
            lines.append(f"**{label}:** {value}")
    if story.lesson:
        lines.append(f"**Reflection:** {story.lesson}")

    if story.experience_id:
        exp = next(
            (e for e in vault.experiences if e.id == story.experience_id), None
        )
        if exp is not None:
            source = f"{exp.title} — {exp.company}" if exp.company else exp.title
            lines.append(f"**Source:** {source}")

    # Tags = theme_tags + resolved skill names (canonical vocabulary;
    # dangling skill refs are skipped, mirroring the audit's warning rule).
    skill_names = {str(s.id): s.name for s in vault.skills}
    tags = list(story.theme_tags) + [
        skill_names[str(ref)] for ref in story.skill_ids if str(ref) in skill_names
    ]
    if tags:
        lines.append(f"**Best for questions about:** {', '.join(tags)}")
    return lines


def _story_bank_markdown(vault: VaultSchema) -> str:
    lines = [
        "# Story bank",
        "",
        "One STAR block per Traitprint story. Source: traitprint "
        f"(export_version: {CAREER_BUNDLE_EXPORT_VERSION}).",
    ]
    for story in vault.stories:
        lines.append("")
        lines.extend(_story_block(story, vault))
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------
# config/profile.yml — string-templated (comments are load-bearing)
# ------------------------------------------------------------------


def _archetype_entries(vault: VaultSchema) -> list[tuple[str, str]]:
    """(archetype, fit) pairs: default lens's archetypes are primary,
    every other lens's are secondary; first occurrence wins."""
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    ordered = sorted(vault.lenses, key=lambda lens: not lens.is_default)
    for lens in ordered:
        fit = "primary" if lens.is_default else "secondary"
        for archetype in lens.target_archetypes:
            if archetype not in seen:
                seen.add(archetype)
                entries.append((archetype, fit))
    return entries


def _profile_yaml(vault: VaultSchema) -> str:
    p = vault.profile
    lines = [
        "# Generated by 'traitprint vault export -f career-bundle'.",
        f"# source: traitprint · export_version: {CAREER_BUNDLE_EXPORT_VERSION}",
        "# Commented keys are placeholders for your own onboarding to fill —",
        "# Traitprint only emits facts that exist in the vault.",
        "",
        "candidate:",
        f"  name: {_yaml_str(p.display_name)}",
        f"  email: {_yaml_str(p.contact_email)}",
        f"  location: {_yaml_str(p.location)}",
        '  # phone: ""',
        "",
        "narrative:",
        f"  headline: {_yaml_str(p.headline)}",
        f"  summary: {_yaml_str(p.summary)}",
        "",
    ]
    archetypes = _archetype_entries(vault)
    if archetypes:
        lines.append("target_roles:")
        lines.append("  archetypes:")
        for name, fit in archetypes:
            lines.append(f"    - name: {_yaml_str(name)}")
            lines.append(f"      fit: {fit}")
    else:
        lines.append("# target_roles:")
        lines.append("#   archetypes:")
        lines.append('#     - name: ""')
        lines.append("#       fit: primary")
    lines.extend(
        [
            "",
            "# links:",
            '#   linkedin: ""',
            '#   github: ""',
            '#   portfolio: ""',
            "# compensation:",
            "#   minimum:",
            "#   target:",
            '#   currency: "USD"',
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------
# Bundle assembly
# ------------------------------------------------------------------


def vault_to_career_bundle(
    vault: VaultSchema, lens: LensSchema | None = None
) -> dict[str, str]:
    """Return the bundle as ``{relative_path: content}``.

    ``lens`` projects cv.md only (headline/bio overrides, signature
    experiences first, core skills first, suppressed skills dropped) —
    the story bank and profile.yml always render the full vault.
    """
    return {
        "cv.md": _cv_markdown(vault, lens),
        "interview-prep/story-bank.md": _story_bank_markdown(vault),
        "config/profile.yml": _profile_yaml(vault),
    }
