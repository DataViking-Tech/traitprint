"""Proposal store + apply logic for ``proposals/*.json`` staged writes.

A proposal is a staged write awaiting review (contract:
``docs/schema/vault-v1/`` ``$defs/proposal``). Remote agents (the hosted
MCP server's ``vault_propose``), the web app, and the local CLI
(``traitprint proposals add``) all stage the same shape; the local CLI
reviews them (``traitprint proposals list|show|approve|reject``).

Contract rules implemented here:

- Kinds, statuses, and per-kind allowed payload keys mirror the cloud
  ``vault_propose`` validation exactly, so a proposal staged on any
  surface reviews identically on any other.
- ``target_id`` is required for ``update_*`` kinds (except
  ``update_profile``, which always targets the singleton profile) and
  forbidden otherwise.
- Approval applies the payload to the vault and deletes the proposal
  file **in the same git commit** (contract rule 7); rejection keeps the
  file with ``status: rejected`` and a ``resolved_at`` timestamp.
- Dangling or invalid ``proposals/*.json`` files never crash a load —
  they surface as findings (Layer 1 spirit: referential problems are
  warnings, not parse errors).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError, field_validator

from traitprint.schema import (
    EducationSchema,
    ExperienceSchema,
    PhilosophySchema,
    ProfileSchema,
    SkillSchema,
    StorySchema,
    VaultSchema,
)
from traitprint.vault_io import parse_story_body

PROPOSALS_DIR = "proposals"

#: Proposal kinds, verbatim from the contract's $defs/proposal enum.
PROPOSAL_KINDS = (
    "add_skill",
    "update_skill",
    "add_experience",
    "update_experience",
    "add_story",
    "update_story",
    "add_philosophy",
    "update_philosophy",
    "add_education",
    "update_education",
    "update_profile",
)

PROPOSAL_STATUSES = ("pending", "approved", "rejected", "withdrawn")

# Allowed top-level payload keys per kind, straight from the contract's
# entity $defs. Markdown-backed entities (experience, story, philosophy)
# also accept ``body`` — the contract routes narrative text through
# payload.body. Mirrors the cloud MCP server's PROPOSAL_PAYLOAD_KEYS.
_SKILL_KEYS = (
    "id",
    "name",
    "taxonomy_id",
    "category",
    "proficiency",
    "source",
    "notes",
    "created_at",
    "updated_at",
)
_EXPERIENCE_KEYS = (
    "id",
    "title",
    "company",
    "start_date",
    "end_date",
    "accomplishments",
    "skill_ids",
    "skill_links",
    "source",
    "created_at",
    "updated_at",
    "body",
)
_STORY_KEYS = (
    "id",
    "title",
    "skill_ids",
    "experience_id",
    "outcome",
    "theme_tags",
    "source",
    "created_at",
    "updated_at",
    "body",
)
_PHILOSOPHY_KEYS = (
    "id",
    "title",
    "category",
    "evidence_story_ids",
    "source",
    "created_at",
    "updated_at",
    "body",
)
_EDUCATION_KEYS = (
    "id",
    "institution",
    "degree",
    "field_of_study",
    "start_date",
    "end_date",
    "description",
)

PROPOSAL_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "add_skill": _SKILL_KEYS,
    "update_skill": _SKILL_KEYS,
    "add_experience": _EXPERIENCE_KEYS,
    "update_experience": _EXPERIENCE_KEYS,
    "add_story": _STORY_KEYS,
    "update_story": _STORY_KEYS,
    "add_philosophy": _PHILOSOPHY_KEYS,
    "update_philosophy": _PHILOSOPHY_KEYS,
    "add_education": _EDUCATION_KEYS,
    "update_education": _EDUCATION_KEYS,
    "update_profile": ("basics",),
}

#: Minimum fields an add_* / update_profile payload must carry.
_REQUIRED_ADD_KEYS: dict[str, tuple[str, ...]] = {
    "add_skill": ("name", "proficiency"),
    "add_experience": ("title",),
    "add_story": ("title",),
    "add_philosophy": ("title",),
    "add_education": ("institution",),
    "update_profile": ("basics",),
}

#: profile.json ``basics`` keys (JSON Resume-compatible subset).
#: ``phone``, ``url`` and ``profiles`` are contract revision 1.3 (additive).
_PROFILE_BASICS_KEYS = (
    "name",
    "label",
    "summary",
    "email",
    "location",
    "phone",
    "url",
    "profiles",
)

#: basics key -> ProfileSchema field.
_BASICS_TO_PROFILE = {
    "name": "display_name",
    "label": "headline",
    "summary": "summary",
    "email": "contact_email",
    "location": "location",
    "phone": "phone",
    "url": "url",
    "profiles": "profiles",
}

#: keys of one ``basics.profiles[]`` entry (JSON Resume profile item).
_PROFILE_LINK_KEYS = ("network", "username", "url")


def _profile_links_problems(value: Any) -> list[str]:
    """Shape problems for a ``basics.profiles`` value (empty when valid)."""
    if not isinstance(value, list):
        return ["payload.basics.profiles: must be a JSON array"]
    problems: list[str] = []
    for i, entry in enumerate(value):
        if not isinstance(entry, dict):
            problems.append(f"payload.basics.profiles[{i}]: must be a JSON object")
            continue
        bad = [k for k in entry if k not in _PROFILE_LINK_KEYS]
        if bad:
            problems.append(
                f"payload.basics.profiles[{i}]: keys outside the profile "
                f"link shape: {', '.join(sorted(bad))}. Allowed keys: "
                f"{', '.join(_PROFILE_LINK_KEYS)}"
            )
        network = entry.get("network")
        if not isinstance(network, str) or not network.strip():
            problems.append(
                f"payload.basics.profiles[{i}].network: required non-empty string"
            )
        for key in ("username", "url"):
            if key in entry and not isinstance(entry[key], str):
                problems.append(
                    f"payload.basics.profiles[{i}].{key}: must be a string"
                )
    return problems

#: kind suffix -> vault section name.
_KIND_SECTION = {
    "skill": "skills",
    "experience": "experiences",
    "story": "stories",
    "philosophy": "philosophies",
    "education": "education",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def kind_entity(kind: str) -> str:
    """``add_skill`` -> ``skill``, ``update_profile`` -> ``profile``."""
    return kind.split("_", 1)[1]


def is_update_kind(kind: str) -> bool:
    """True for the update_* kinds that target an existing entity.

    ``update_profile`` is excluded: the profile is a singleton, so it
    never carries a ``target_id``.
    """
    return kind.startswith("update_") and kind != "update_profile"


class ProposalValidationError(ValueError):
    """Raised when proposal fields violate the contract."""


class ProposalApplyError(ValueError):
    """Raised when an approved proposal cannot be applied to the vault."""


class ProposalSchema(BaseModel):
    """One ``proposals/*.json`` document (contract ``$defs/proposal``)."""

    id: UUID = Field(default_factory=uuid4)
    kind: str
    target_id: UUID | None = None
    payload: dict[str, Any]
    rationale: str = ""
    source: str = ""
    status: str = "pending"
    created_at: datetime = Field(default_factory=_now)
    resolved_at: datetime | None = None

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, value: str) -> str:
        if value not in PROPOSAL_KINDS:
            raise ValueError(
                f"kind must be one of {', '.join(PROPOSAL_KINDS)}; got {value!r}"
            )
        return value

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str) -> str:
        if value not in PROPOSAL_STATUSES:
            raise ValueError(
                f"status must be one of {', '.join(PROPOSAL_STATUSES)}; "
                f"got {value!r}"
            )
        return value

    def summary(self, max_len: int = 48) -> str:
        """One-line human summary: the payload's headline field."""
        headline = (
            self.payload.get("name")
            or self.payload.get("title")
            or self.payload.get("institution")
            or ""
        )
        if self.kind == "update_profile":
            basics = self.payload.get("basics")
            if isinstance(basics, dict):
                headline = ", ".join(sorted(basics)) or ""
        text = str(headline)
        if len(text) > max_len:
            text = text[: max_len - 1] + "…"
        return text


def validate_proposal_fields(
    kind: str,
    target_id: UUID | str | None,
    payload: Any,
    rationale: Any = "",
) -> list[str]:
    """Contract validation for a new proposal — mirrors cloud vault_propose.

    Returns a list of problems (empty when valid). Checks: kind enum,
    payload is a non-empty object with allowed keys only, required add_*
    keys present and non-empty, target_id required for update_* kinds and
    forbidden otherwise.
    """
    problems: list[str] = []
    if kind not in PROPOSAL_KINDS:
        return [f"kind: must be one of {', '.join(PROPOSAL_KINDS)}; got {kind!r}"]

    if not isinstance(payload, dict):
        problems.append("payload: must be a JSON object")
        return problems
    if not payload:
        problems.append("payload: must not be empty")
        return problems

    allowed = PROPOSAL_PAYLOAD_KEYS[kind]
    unknown = [k for k in payload if k not in allowed]
    if unknown:
        problems.append(
            f"payload: keys outside the {kind} entity shape: "
            f"{', '.join(sorted(unknown))}. Allowed keys: {', '.join(allowed)}"
        )

    for key in _REQUIRED_ADD_KEYS.get(kind, ()):
        value = payload.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            problems.append(f"payload.{key}: required for {kind}")

    if kind == "update_profile":
        basics = payload.get("basics")
        if basics is not None and not isinstance(basics, dict):
            problems.append("payload.basics: must be a JSON object")
        elif isinstance(basics, dict):
            bad = [k for k in basics if k not in _PROFILE_BASICS_KEYS]
            if bad:
                problems.append(
                    "payload.basics: keys outside the profile basics shape: "
                    f"{', '.join(sorted(bad))}. Allowed keys: "
                    f"{', '.join(_PROFILE_BASICS_KEYS)}"
                )
            if "profiles" in basics:
                problems.extend(_profile_links_problems(basics["profiles"]))

    if is_update_kind(kind):
        if target_id is None:
            problems.append(
                f"target_id: required for {kind} "
                "(the UUID of the entity being changed)"
            )
    elif target_id is not None:
        problems.append(f"target_id: only valid for update_* kinds (kind={kind})")

    if rationale is not None and not isinstance(rationale, str):
        problems.append("rationale: must be a string")

    return problems


def _error_lines(exc: ValidationError) -> list[str]:
    """Normalize a pydantic error into ``field: message`` lines."""
    lines = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in err.get("loc", ())) or "value"
        msg = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        lines.append(f"{loc}: {msg}")
    return lines


def validate_proposal_document(data: Any) -> list[str]:
    """Contract-validate one full proposal document (parsed JSON).

    The same two checks :meth:`ProposalStore.load_all` runs on every
    ``proposals/*.json`` file before it enters the review queue: the
    ``$defs/proposal`` shape (:class:`ProposalSchema`) first, then the
    kind/target/payload-key rules (:func:`validate_proposal_fields`).
    Returns a list of problems — empty when the document would load and
    review cleanly. Read-only and vault-independent: external proposers
    (exporters, importers, plugins in any language) can pre-flight
    their emitted files with this before staging them.
    """
    if not isinstance(data, dict):
        return ["proposal must be a JSON object"]
    try:
        proposal = ProposalSchema.model_validate(data)
    except ValidationError as exc:
        return _error_lines(exc)
    return validate_proposal_fields(
        proposal.kind,
        str(proposal.target_id) if proposal.target_id else None,
        proposal.payload,
        proposal.rationale,
    )


def proposal_contract() -> dict[str, Any]:
    """Machine-readable statement of the proposal contract.

    One JSON-serializable document naming the kinds, statuses, per-kind
    allowed/required payload keys, profile ``basics`` keys, and the
    kinds that require a ``target_id`` — exactly the validation that
    ``proposals add``, the review queue, and the hosted
    ``vault_propose`` all enforce. External tools that mirror this
    validation in another language can vendor the document (or diff it
    against a live install via ``traitprint proposals contract
    --json``) to catch contract drift instead of re-reading this
    module.
    """
    return {
        "contract": "vault-v1",
        "definition": "$defs/proposal",
        "kinds": list(PROPOSAL_KINDS),
        "statuses": list(PROPOSAL_STATUSES),
        "payload_keys": {
            kind: list(keys) for kind, keys in PROPOSAL_PAYLOAD_KEYS.items()
        },
        "required_payload_keys": {
            kind: list(keys) for kind, keys in _REQUIRED_ADD_KEYS.items()
        },
        "profile_basics_keys": list(_PROFILE_BASICS_KEYS),
        "target_id_required_for": [
            kind for kind in PROPOSAL_KINDS if is_update_kind(kind)
        ],
    }


# ── store ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProposalFileIssue:
    """An unreadable/invalid proposals/*.json file — a finding, not a crash."""

    file: str
    problem: str


@dataclass(frozen=True)
class LoadedProposal:
    """A proposal together with the file it was read from."""

    proposal: ProposalSchema
    path: Path


class ProposalLookupError(ValueError):
    """Raised when an id does not resolve to exactly one proposal."""


class ProposalStore:
    """Reads and writes ``proposals/*.json`` inside a vault directory."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    @property
    def proposals_dir(self) -> Path:
        return self.directory / PROPOSALS_DIR

    def filename_for(self, proposal: ProposalSchema) -> str:
        """Slug filename: kind (kebab-case) + first 8 hex chars of the id."""
        return f"{proposal.kind.replace('_', '-')}-{proposal.id.hex[:8]}.json"

    def load_all(self) -> tuple[list[LoadedProposal], list[ProposalFileIssue]]:
        """Read every ``proposals/*.json``, sorted by created_at then id.

        Files that fail to parse or validate are returned as issues
        (findings), never raised — a corrupt proposal must not brick the
        review queue.
        """
        loaded: list[LoadedProposal] = []
        issues: list[ProposalFileIssue] = []
        dirpath = self.proposals_dir
        if not dirpath.is_dir():
            return loaded, issues
        for file in sorted(dirpath.glob("*.json")):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                issues.append(ProposalFileIssue(file.name, f"invalid JSON: {exc}"))
                continue
            if not isinstance(data, dict):
                issues.append(
                    ProposalFileIssue(file.name, "proposal must be a JSON object")
                )
                continue
            try:
                proposal = ProposalSchema.model_validate(data)
            except ValidationError as exc:
                first = exc.errors(include_url=False)[0]
                loc = ".".join(str(p) for p in first.get("loc", ())) or "value"
                msg = str(first.get("msg", "invalid value")).removeprefix(
                    "Value error, "
                )
                issues.append(ProposalFileIssue(file.name, f"{loc}: {msg}"))
                continue
            # Contract validation beyond shape (target rules, allowed
            # payload keys) — synced or hand-edited files must surface as
            # findings here, never crash later in approve (Codex P2 on #40).
            problems = validate_proposal_fields(
                proposal.kind,
                str(proposal.target_id) if proposal.target_id else None,
                proposal.payload,
                proposal.rationale,
            )
            if problems:
                issues.append(
                    ProposalFileIssue(file.name, "; ".join(problems))
                )
                continue
            loaded.append(LoadedProposal(proposal, file))
        loaded.sort(key=lambda lp: (lp.proposal.created_at, str(lp.proposal.id)))
        return loaded, issues

    def find(self, ident: str) -> LoadedProposal:
        """Resolve a proposal by full UUID or unambiguous hex prefix.

        Raises :class:`ProposalLookupError` when nothing (or more than
        one proposal) matches.
        """
        needle = ident.strip().lower().replace("-", "")
        if not needle:
            raise ProposalLookupError("empty proposal id")
        loaded, _ = self.load_all()
        matches = [lp for lp in loaded if lp.proposal.id.hex.startswith(needle)]
        if not matches:
            raise ProposalLookupError(
                f"no proposal matches {ident!r} — see 'traitprint proposals list'"
            )
        if len(matches) > 1:
            ids = ", ".join(str(lp.proposal.id) for lp in matches)
            raise ProposalLookupError(
                f"{ident!r} is ambiguous — matches: {ids}"
            )
        return matches[0]

    def save(self, proposal: ProposalSchema, path: Path | None = None) -> Path:
        """Write a proposal document; returns the file written.

        New proposals get a slug filename; pass ``path`` to rewrite an
        existing file in place (rejection keeps the original filename).
        """
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        target = path if path is not None else (
            self.proposals_dir / self.filename_for(proposal)
        )
        doc = proposal.model_dump(mode="json")
        target.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return target

    def create(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        target_id: UUID | None = None,
        rationale: str = "",
        source: str = "cli",
    ) -> LoadedProposal:
        """Validate and write a new pending proposal.

        Raises :class:`ProposalValidationError` listing every contract
        violation when the fields are invalid.
        """
        problems = validate_proposal_fields(kind, target_id, payload, rationale)
        if problems:
            raise ProposalValidationError("; ".join(problems))
        proposal = ProposalSchema(
            kind=kind,
            target_id=target_id,
            payload=payload,
            rationale=rationale,
            source=source,
        )
        path = self.save(proposal)
        return LoadedProposal(proposal, path)

    def pending(self) -> list[LoadedProposal]:
        loaded, _ = self.load_all()
        return [lp for lp in loaded if lp.proposal.status == "pending"]


# ── applying proposals to the vault ─────────────────────────────────


def _problems(exc: ValidationError) -> str:
    return "; ".join(_error_lines(exc)) or "invalid value"


def _find_item(items: list[Any], target_id: UUID) -> Any | None:
    for item in items:
        if getattr(item, "id", None) == target_id:
            return item
    return None


def _entity_fields(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Map a proposal payload onto entity model fields.

    ``body`` becomes ``description`` for experiences/philosophies and the
    STAR fields for stories (lenient parse — missing sections stay empty
    and surface as audit findings, matching ``vault add-story``).

    ``payload.id`` is honored for add_* kinds (a proposer may pre-link a
    bundle of proposals by UUID) and dropped for update_* kinds (the
    target's identity never changes).
    """
    skip = ("body",) if kind.startswith("add_") else ("body", "id")
    fields = {k: v for k, v in payload.items() if k not in skip}
    entity = kind_entity(kind)
    body = payload.get("body")
    if isinstance(body, str):
        if entity in ("experience", "philosophy"):
            fields["description"] = body
        elif entity == "story":
            star = parse_story_body(body)
            # Only carry sections the body actually provides, so a
            # partial update_story body never blanks untouched sections.
            for key, value in star.items():
                if value or not kind.startswith("update_"):
                    fields[key] = value
    return fields


def _apply_add(vault: VaultSchema, proposal: ProposalSchema) -> str:
    from traitprint.taxonomy import find_exact

    kind = proposal.kind
    entity = kind_entity(kind)
    section = _KIND_SECTION[entity]
    fields = _entity_fields(kind, proposal.payload)
    fields.setdefault("source", proposal.source or "proposal")

    try:
        if kind == "add_skill":
            name = str(fields.get("name", ""))
            normalized = name.strip().casefold()
            for existing in vault.skills:
                if existing.name.strip().casefold() == normalized:
                    raise ProposalApplyError(
                        f"skill already exists: {existing.name!r} "
                        f"({existing.id}) — reject this proposal or remove "
                        "the existing skill first"
                    )
            if "taxonomy_id" not in fields:
                exact = find_exact(name)
                if exact:
                    fields["taxonomy_id"] = exact.id
                    fields.setdefault("category", exact.category)
            item: Any = SkillSchema.model_validate(fields)
        elif kind == "add_experience":
            item = ExperienceSchema.model_validate(fields)
        elif kind == "add_story":
            item = StorySchema.model_validate(fields)
        elif kind == "add_philosophy":
            item = PhilosophySchema.model_validate(fields)
        else:  # add_education
            fields.pop("source", None)  # education has no source field
            item = EducationSchema.model_validate(fields)
    except ValidationError as exc:
        raise ProposalApplyError(_problems(exc)) from exc

    getattr(vault, section).append(item)
    label = getattr(item, "name", None) or getattr(item, "title", None) or getattr(
        item, "institution", ""
    )
    return f"Added {entity}: {label}"


def _apply_update(vault: VaultSchema, proposal: ProposalSchema) -> str:
    from traitprint.taxonomy import find_exact

    kind = proposal.kind
    entity = kind_entity(kind)
    section = _KIND_SECTION[entity]
    if proposal.target_id is None:
        # Contract validation at load/add time should prevent this; keep
        # an actionable error rather than an assert so a path that skips
        # validation can never crash the review queue.
        raise ProposalApplyError(
            f"{kind} proposal has no target_id — re-create it with "
            "--target-id or reject it."
        )
    current = _find_item(getattr(vault, section), proposal.target_id)
    if current is None:
        raise ProposalApplyError(
            f"target {entity} {proposal.target_id} does not exist in the "
            f"vault — the entity this proposal targets is gone. Reject the "
            "proposal, or re-stage it against a current id from "
            f"'traitprint vault list {section}'."
        )

    fields = _entity_fields(kind, proposal.payload)
    fields.pop("source", None)  # provenance of the entity is not updatable
    if (
        kind == "update_skill"
        and "name" in fields
        and "taxonomy_id" not in fields
    ):
        # A rename re-resolves the taxonomy deterministically (or clears
        # it) — never silently keeps the old taxonomy link.
        exact = find_exact(str(fields["name"]))
        fields["taxonomy_id"] = exact.id if exact else None

    updated = current.model_dump()
    updated.update(fields)
    if "updated_at" in updated:
        updated["updated_at"] = _now()

    model_cls = type(current)
    try:
        replacement = model_cls.model_validate(updated)
    except ValidationError as exc:
        raise ProposalApplyError(_problems(exc)) from exc

    items: list[Any] = getattr(vault, section)
    items[items.index(current)] = replacement
    label = getattr(replacement, "name", None) or getattr(
        replacement, "title", None
    ) or getattr(replacement, "institution", "")
    return f"Updated {entity}: {label}"


def _apply_profile(vault: VaultSchema, proposal: ProposalSchema) -> str:
    basics = proposal.payload.get("basics")
    if not isinstance(basics, dict) or not basics:
        raise ProposalApplyError("payload.basics must be a non-empty object")
    bad = [k for k in basics if k not in _PROFILE_BASICS_KEYS]
    if bad:
        raise ProposalApplyError(
            "payload.basics has keys outside the profile shape: "
            f"{', '.join(sorted(bad))}"
        )
    link_problems = (
        _profile_links_problems(basics["profiles"]) if "profiles" in basics else []
    )
    if link_problems:
        raise ProposalApplyError("; ".join(link_problems))
    current = vault.profile.model_dump()
    changed: list[str] = []
    for key, value in basics.items():
        field_name = _BASICS_TO_PROFILE[key]
        if key == "profiles":
            current[field_name] = [] if value is None else value
        else:
            current[field_name] = "" if value is None else str(value)
        changed.append(key)
    try:
        vault.profile = ProfileSchema.model_validate(current)
    except ValidationError as exc:
        raise ProposalApplyError(_problems(exc)) from exc
    return f"Updated profile: {', '.join(sorted(changed))}"


def apply_proposal(vault: VaultSchema, proposal: ProposalSchema) -> str:
    """Apply a proposal payload to the in-memory vault (Layer 0 hard).

    The payload is validated against the entity schema before anything
    mutates; :class:`ProposalApplyError` carries an actionable message
    (dangling target, schema violation, duplicate skill). The caller is
    responsible for persisting the vault and committing.
    """
    if proposal.kind == "update_profile":
        return _apply_profile(vault, proposal)
    if proposal.kind.startswith("add_"):
        return _apply_add(vault, proposal)
    return _apply_update(vault, proposal)


# ── review diff (current → proposed) ────────────────────────────────


def proposal_diff(
    vault: VaultSchema, proposal: ProposalSchema
) -> list[dict[str, Any]]:
    """Field-level rows for review: ``{field, current, proposed}``.

    ``current`` is ``None`` for add_* kinds (no existing entity) and for
    update_* targets that no longer exist (the approve path errors on
    those; the diff still renders so the user can see the payload).
    """
    rows: list[dict[str, Any]] = []

    if proposal.kind == "update_profile":
        basics = proposal.payload.get("basics")
        if isinstance(basics, dict):
            current_profile = vault.profile.model_dump()
            for key in _PROFILE_BASICS_KEYS:
                if key in basics:
                    rows.append(
                        {
                            "field": f"basics.{key}",
                            "current": current_profile.get(
                                _BASICS_TO_PROFILE[key]
                            ),
                            "proposed": basics[key],
                        }
                    )
        return rows

    entity = kind_entity(proposal.kind)
    section = _KIND_SECTION[entity]
    current: Any = None
    if is_update_kind(proposal.kind) and proposal.target_id is not None:
        current = _find_item(getattr(vault, section), proposal.target_id)

    fields = _entity_fields(proposal.kind, proposal.payload)
    for field_name in sorted(fields):
        rows.append(
            {
                "field": field_name,
                "current": (
                    getattr(current, field_name, None)
                    if current is not None
                    else None
                ),
                "proposed": fields[field_name],
            }
        )
    return rows


__all__ = [
    "PROPOSALS_DIR",
    "PROPOSAL_KINDS",
    "PROPOSAL_PAYLOAD_KEYS",
    "PROPOSAL_STATUSES",
    "LoadedProposal",
    "ProposalApplyError",
    "ProposalFileIssue",
    "ProposalLookupError",
    "ProposalSchema",
    "ProposalStore",
    "apply_proposal",
    "is_update_kind",
    "kind_entity",
    "proposal_contract",
    "proposal_diff",
    "validate_proposal_document",
    "validate_proposal_fields",
]
