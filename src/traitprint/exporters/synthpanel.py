"""Export a Traitprint vault as a SynthPanel persona pack.

SynthPanel consumes YAML persona packs shaped like::

    personas:
      - name: Sarah Chen
        age: 34
        occupation: Product Manager
        background: >
          Works at a mid-size SaaS company...
        personality_traits:
          - analytical
          - pragmatic

The only field SynthPanel requires per persona is ``name``.
``personality_traits`` must be a list of strings (or a comma-separated
string) when present. This mirrors ``validate_persona_pack`` in
synthpanel's ``synth_panel/mcp/data.py``.

A Traitprint vault represents a single identity, so a vault export
produces a pack containing exactly one persona.
"""

from __future__ import annotations

from typing import Any

from traitprint.schema import VaultSchema

# Schema version emitted by this exporter. Consumers can branch on this
# to stay forward-compatible when Traitprint adds new fields.
SYNTHPANEL_EXPORT_VERSION = 1

# Top skill count included in the persona background summary.
_TOP_SKILLS = 5


def vault_to_synthpanel_persona(vault: VaultSchema) -> dict[str, Any]:
    """Return a single SynthPanel persona dict derived from ``vault``.

    Required SynthPanel field: ``name``. When the vault profile has no
    display name, falls back to ``"Anonymous"`` so the output is always
    valid against SynthPanel's pack validator.
    """
    profile = vault.profile
    name = profile.display_name.strip() or "Anonymous"

    occupation = profile.headline.strip()
    if not occupation and vault.experiences:
        most_recent = vault.experiences[-1]
        if most_recent.company:
            occupation = f"{most_recent.title} at {most_recent.company}"
        else:
            occupation = most_recent.title

    persona: dict[str, Any] = {"name": name}
    if occupation:
        persona["occupation"] = occupation
    if profile.location.strip():
        persona["location"] = profile.location.strip()

    background = _build_background(vault)
    if background:
        persona["background"] = background

    traits = _derive_personality_traits(vault)
    if traits:
        persona["personality_traits"] = traits

    skills = _top_skills(vault)
    if skills:
        persona["skills"] = skills

    education = _summarize_education(vault)
    if education:
        persona["education"] = education

    return persona


def vault_to_synthpanel_pack(
    vault: VaultSchema, pack_name: str | None = None
) -> dict[str, Any]:
    """Return a SynthPanel pack (``{name, personas}``) for ``vault``.

    ``pack_name`` defaults to the profile display name (or ``"traitprint
    vault"`` when unset). The pack always contains a single persona.
    """
    persona = vault_to_synthpanel_persona(vault)
    name = pack_name or vault.profile.display_name.strip() or "traitprint vault"
    return {
        "name": name,
        "source": "traitprint",
        "traitprint_schema_version": vault.schema_version,
        "export_version": SYNTHPANEL_EXPORT_VERSION,
        "personas": [persona],
    }


def _build_background(vault: VaultSchema) -> str:
    """Compose a human-readable background paragraph.

    Combines the profile summary with up to two recent experience
    descriptions. Returns an empty string when no source material is
    available.
    """
    parts: list[str] = []
    summary = vault.profile.summary.strip()
    if summary:
        parts.append(summary)

    for exp in reversed(vault.experiences[-2:]):
        exp_line = exp.title
        if exp.company:
            exp_line = f"{exp_line} at {exp.company}"
        dates = _format_dates(exp.start_date, exp.end_date)
        if dates:
            exp_line = f"{exp_line} ({dates})"
        if exp.description.strip():
            exp_line = f"{exp_line}: {exp.description.strip()}"
        parts.append(exp_line)

    return "\n\n".join(parts)


def _format_dates(start: str, end: str) -> str:
    if not start and not end:
        return ""
    if start and end:
        return f"{start}–{end}"
    if start:
        return f"{start}–present"
    return end


def _derive_personality_traits(vault: VaultSchema) -> list[str]:
    """Build a lowercase trait list from philosophies.

    Uses each philosophy's category (human readable, e.g.
    ``"decision making"``) and, when present, its title — trimmed and
    deduplicated in insertion order.
    """
    seen: dict[str, None] = {}
    for phil in vault.philosophies:
        category = phil.category.value.replace("-", " ").strip().lower()
        if category:
            seen.setdefault(category, None)
        title = phil.title.strip().lower()
        if title:
            seen.setdefault(title, None)
    return list(seen.keys())


def _top_skills(vault: VaultSchema) -> list[dict[str, Any]]:
    """Return the top-N skills as lightweight dicts for persona context."""
    ranked = sorted(vault.skills, key=lambda s: (-s.proficiency, s.name.lower()))
    out: list[dict[str, Any]] = []
    for skill in ranked[:_TOP_SKILLS]:
        entry: dict[str, Any] = {
            "name": skill.name,
            "proficiency": skill.proficiency,
        }
        if skill.category:
            entry["category"] = skill.category
        out.append(entry)
    return out


def _summarize_education(vault: VaultSchema) -> list[str]:
    """Return compact education strings like ``"BS Computer Science, MIT"``."""
    out: list[str] = []
    for ed in vault.education:
        bits: list[str] = []
        if ed.degree:
            bits.append(ed.degree)
        if ed.field_of_study:
            bits.append(ed.field_of_study)
        head = " ".join(bits).strip()
        if ed.institution and head:
            out.append(f"{head}, {ed.institution}")
        elif ed.institution:
            out.append(ed.institution)
        elif head:
            out.append(head)
    return out
