"""Export vault to interop formats: JSON, Markdown, JSON Resume, SynthPanel persona.

These exports are the portability backbone of Traitprint. JSON is a
lossless round-trip; the others are lossy projections tuned for a
specific consumer (humans, JSON Resume tooling, SynthPanel personas).
"""

from __future__ import annotations

import json
from typing import Literal

from traitprint.schema import (
    ExperienceSchema,
    SkillSchema,
    VaultSchema,
)

ExportFormat = Literal["json", "markdown", "jsonresume", "synthpanel-persona"]

SUPPORTED_FORMATS: tuple[str, ...] = (
    "json",
    "markdown",
    "jsonresume",
    "synthpanel-persona",
)


def export_vault(vault: VaultSchema, fmt: str, *, pack_name: str | None = None) -> str:
    """Render the vault in the requested format.

    ``pack_name`` is only consulted by ``synthpanel-persona``; other
    formats ignore it.
    """
    if fmt == "json":
        return _export_json(vault)
    if fmt == "markdown":
        return _export_markdown(vault)
    if fmt == "jsonresume":
        return _export_jsonresume(vault)
    if fmt == "synthpanel-persona":
        return _export_synthpanel_persona(vault, pack_name=pack_name)
    raise ValueError(
        f"Unknown export format: {fmt!r}. Supported: {', '.join(SUPPORTED_FORMATS)}"
    )


# ------------------------------------------------------------------
# JSON — lossless
# ------------------------------------------------------------------


def _export_json(vault: VaultSchema) -> str:
    payload = vault.model_dump(mode="json")
    return json.dumps(payload, indent=2, default=str, sort_keys=False)


# ------------------------------------------------------------------
# Markdown — human-readable resume
# ------------------------------------------------------------------


def _export_markdown(vault: VaultSchema) -> str:
    lines: list[str] = []
    profile = vault.profile
    title = profile.display_name or "Traitprint"
    lines.append(f"# {title}")
    if profile.headline:
        lines.append("")
        lines.append(f"_{profile.headline}_")
    meta_bits: list[str] = []
    if profile.location:
        meta_bits.append(profile.location)
    if profile.contact_email:
        meta_bits.append(profile.contact_email)
    if meta_bits:
        lines.append("")
        lines.append(" · ".join(meta_bits))
    if profile.summary:
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append(profile.summary)

    if vault.experiences:
        lines.append("")
        lines.append("## Experience")
        for exp in vault.experiences:
            lines.append("")
            header = f"### {exp.title}"
            if exp.company:
                header += f" — {exp.company}"
            lines.append(header)
            dates = _format_date_range(exp.start_date, exp.end_date)
            if dates:
                lines.append("")
                lines.append(f"_{dates}_")
            if exp.description:
                lines.append("")
                lines.append(exp.description)
            if exp.accomplishments:
                lines.append("")
                for acc in exp.accomplishments:
                    lines.append(f"- {acc}")

    if vault.education:
        lines.append("")
        lines.append("## Education")
        for edu in vault.education:
            lines.append("")
            header = f"### {edu.institution}"
            lines.append(header)
            degree_line = _compose(edu.degree, edu.field_of_study, sep=", ")
            if degree_line:
                lines.append("")
                lines.append(degree_line)
            dates = _format_date_range(edu.start_date, edu.end_date)
            if dates:
                lines.append("")
                lines.append(f"_{dates}_")
            if edu.description:
                lines.append("")
                lines.append(edu.description)

    if vault.skills:
        lines.append("")
        lines.append("## Skills")
        for skill in _sort_skills(vault.skills):
            lines.append(
                f"- **{skill.name}** — {skill.proficiency}/10"
                + (f" ({skill.category})" if skill.category else "")
            )

    if vault.stories:
        lines.append("")
        lines.append("## Stories")
        for story in vault.stories:
            lines.append("")
            lines.append(f"### {story.title}")
            for label, value in (
                ("Situation", story.situation),
                ("Task", story.task),
                ("Action", story.action),
                ("Result", story.result),
            ):
                if value:
                    lines.append("")
                    lines.append(f"**{label}.** {value}")

    if vault.philosophies:
        lines.append("")
        lines.append("## Philosophy")
        for phi in vault.philosophies:
            lines.append("")
            lines.append(f"### {phi.title} ({phi.category.value})")
            if phi.description:
                lines.append("")
                lines.append(phi.description)

    # Ensure trailing newline
    return "\n".join(lines).rstrip() + "\n"


def _format_date_range(start: str, end: str) -> str:
    if start and end:
        return f"{start} – {end}"
    if start:
        return f"{start} – Present"
    if end:
        return end
    return ""


def _compose(*parts: str, sep: str = " ") -> str:
    return sep.join(p for p in parts if p)


def _sort_skills(skills: list[SkillSchema]) -> list[SkillSchema]:
    return sorted(skills, key=lambda s: (-s.proficiency, s.name.lower()))


# ------------------------------------------------------------------
# JSON Resume — https://jsonresume.org/schema/
# ------------------------------------------------------------------


def _export_jsonresume(vault: VaultSchema) -> str:
    profile = vault.profile
    basics: dict[str, object] = {
        "name": profile.display_name,
        "label": profile.headline,
        "email": profile.contact_email,
        "summary": profile.summary,
        "location": {"address": profile.location} if profile.location else {},
        "profiles": [],
    }

    work = [_exp_to_jsonresume(e) for e in vault.experiences]
    education = [
        {
            "institution": edu.institution,
            "area": edu.field_of_study,
            "studyType": edu.degree,
            "startDate": _normalize_date(edu.start_date),
            "endDate": _normalize_date(edu.end_date),
            **({"summary": edu.description} if edu.description else {}),
        }
        for edu in vault.education
    ]
    skills = [
        {
            "name": s.name,
            **({"level": _proficiency_label(s.proficiency)} if s.proficiency else {}),
            **({"keywords": [s.category]} if s.category else {}),
        }
        for s in _sort_skills(vault.skills)
    ]

    payload: dict[str, object] = {
        "$schema": (
            "https://raw.githubusercontent.com/jsonresume/"
            "resume-schema/v1.0.0/schema.json"
        ),
        "basics": basics,
        "work": work,
        "education": education,
        "skills": skills,
    }

    # STAR stories don't map cleanly to JSON Resume; expose them as
    # "projects" so downstream tools can at least see the titles.
    if vault.stories:
        payload["projects"] = [
            {
                "name": story.title,
                "description": _story_summary(story.situation, story.task),
                "highlights": [h for h in (story.action, story.result) if h],
            }
            for story in vault.stories
        ]

    return json.dumps(payload, indent=2, default=str)


def _exp_to_jsonresume(exp: ExperienceSchema) -> dict[str, object]:
    entry: dict[str, object] = {
        "name": exp.company,
        "position": exp.title,
        "startDate": _normalize_date(exp.start_date),
        "endDate": _normalize_date(exp.end_date),
    }
    if exp.description:
        entry["summary"] = exp.description
    if exp.accomplishments:
        entry["highlights"] = list(exp.accomplishments)
    return entry


def _normalize_date(raw: str) -> str:
    """Normalize vault YYYY or YYYY-MM strings to JSON Resume format.

    JSON Resume accepts YYYY-MM-DD; we pad with -01 for missing pieces.
    Empty strings pass through as empty.
    """
    if not raw:
        return ""
    parts = raw.split("-")
    if len(parts) == 1 and len(parts[0]) == 4 and parts[0].isdigit():
        return f"{parts[0]}-01-01"
    if len(parts) == 2 and len(parts[0]) == 4:
        return f"{parts[0]}-{parts[1].zfill(2)}-01"
    return raw


def _proficiency_label(prof: int) -> str:
    if prof >= 9:
        return "Master"
    if prof >= 7:
        return "Advanced"
    if prof >= 4:
        return "Intermediate"
    return "Beginner"


def _story_summary(situation: str, task: str) -> str:
    return _compose(situation, task, sep=" ") or ""


# ------------------------------------------------------------------
# SynthPanel persona — YAML pack
# ------------------------------------------------------------------


def _export_synthpanel_persona(vault: VaultSchema, pack_name: str | None = None) -> str:
    """Project the vault into a SynthPanel persona pack (YAML).

    Delegates to :func:`traitprint.exporters.synthpanel.vault_to_synthpanel_pack`,
    which is the canonical pack format consumed by ``synthpanel panel
    run --personas``.
    """
    import yaml

    from traitprint.exporters.synthpanel import vault_to_synthpanel_pack

    pack = vault_to_synthpanel_pack(vault, pack_name=pack_name)
    return yaml.safe_dump(pack, sort_keys=False, default_flow_style=False)
