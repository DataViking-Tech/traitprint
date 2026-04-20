"""Resume import + BYOK skill mining.

Given a resume file (PDF, DOCX, TXT, MD), extract structured vault data
(profile, skills, experiences, education) via a BYOK LLM provider and return
it as a :class:`ResumeDraft`. The caller is responsible for user
confirmation and writing accepted items into the vault.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from traitprint.providers import LLMError, LLMProvider, LLMResponse
from traitprint.schema import (
    EducationSchema,
    ExperienceSchema,
    ProfileSchema,
    SkillSchema,
)

# Max characters of resume text we send to the LLM. Long enough for a dense
# multi-page CV; keeps costs predictable.
MAX_RESUME_CHARS = 30_000

# ---- Text extraction -------------------------------------------------------


class ResumeExtractionError(RuntimeError):
    """Raised when a resume file can't be read or parsed."""


def extract_resume_text(path: Path) -> str:
    """Extract plain text from a resume file.

    Supported formats: ``.pdf``, ``.docx``, ``.txt``, ``.md``. PDF and DOCX
    require optional deps (``pypdf``, ``python-docx``) — we raise a friendly
    message if they're missing.
    """
    if not path.is_file():
        raise ResumeExtractionError(f"Resume file not found: {path}")

    suffix = path.suffix.lower()

    if suffix in (".txt", ".md"):
        return path.read_text(encoding="utf-8", errors="replace")

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ResumeExtractionError(
                "Reading PDF resumes requires the 'pypdf' package. "
                "Install with: pip install traitprint[import]"
            ) from exc
        try:
            reader = PdfReader(str(path))
            pages = [page.extract_text() or "" for page in reader.pages]
            return "\n".join(pages)
        except Exception as exc:  # pragma: no cover - library errors
            raise ResumeExtractionError(f"Failed to parse PDF: {exc}") from exc

    if suffix == ".docx":
        try:
            import docx  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ResumeExtractionError(
                "Reading DOCX resumes requires the 'python-docx' package. "
                "Install with: pip install traitprint[import]"
            ) from exc
        try:
            document = docx.Document(str(path))
            return "\n".join(p.text for p in document.paragraphs)
        except Exception as exc:  # pragma: no cover - library errors
            raise ResumeExtractionError(f"Failed to parse DOCX: {exc}") from exc

    raise ResumeExtractionError(
        f"Unsupported resume format: {suffix}. "
        "Supported: .pdf, .docx, .txt, .md"
    )


# ---- LLM-driven extraction ------------------------------------------------

_SYSTEM_PROMPT = """You are a careful resume-parsing assistant.

Given the text of a resume, extract structured career data. Return ONLY a
JSON object with this exact shape — no prose, no markdown fences:

{
  "profile": {
    "display_name": "string",
    "headline": "string",
    "summary": "string",
    "location": "string",
    "contact_email": "string"
  },
  "skills": [
    {
      "name": "string",
      "category": "technical|soft|domain|tool",
      "proficiency": 1-10,
      "notes": "string"
    }
  ],
  "experiences": [
    {
      "title": "string",
      "company": "string",
      "start_date": "YYYY-MM",
      "end_date": "YYYY-MM or empty string if current",
      "description": "string",
      "accomplishments": ["string", ...]
    }
  ],
  "education": [
    {
      "institution": "string",
      "degree": "string",
      "field_of_study": "string",
      "start_date": "YYYY",
      "end_date": "YYYY or empty string",
      "description": "string"
    }
  ]
}

Rules:
- Use integers 1-10 for proficiency. Estimate conservatively: 5=competent,
  7=strong, 9=expert. Default to 6 if unsure.
- Omit fields you cannot infer — use empty strings, not null.
- Do not invent companies, dates, or skills. If something is not in the
  resume, leave it out rather than guess.
- Dates should be ISO-like (YYYY-MM for experiences, YYYY for education).
- Skills should be concrete (e.g. "Python", "Redshift", "Team Leadership"),
  not vague ("coding", "good with people").
"""


_USER_TEMPLATE = """\
Parse this resume and return the JSON object described in the system prompt.

<resume>
{text}
</resume>
"""


@dataclass
class ResumeDraft:
    """Structured resume extraction ready for user review."""

    profile: ProfileSchema
    skills: list[SkillSchema] = field(default_factory=list)
    experiences: list[ExperienceSchema] = field(default_factory=list)
    education: list[EducationSchema] = field(default_factory=list)
    raw_response: str = ""
    usage: LLMResponse | None = None

    def summary_lines(self) -> list[str]:
        """Human-readable one-per-section summary for CLI confirmation."""
        lines = []
        name = self.profile.display_name or "(no name extracted)"
        lines.append(f"Profile: {name} — {self.profile.headline or 'no headline'}")
        lines.append(f"Skills: {len(self.skills)}")
        for s in self.skills[:10]:
            lines.append(f"  - {s.name} ({s.proficiency}/10, {s.category})")
        if len(self.skills) > 10:
            lines.append(f"  ... and {len(self.skills) - 10} more")
        lines.append(f"Experiences: {len(self.experiences)}")
        for e in self.experiences:
            dates = e.start_date + (f"–{e.end_date}" if e.end_date else "–present")
            lines.append(f"  - {e.title} @ {e.company} ({dates})")
        lines.append(f"Education: {len(self.education)}")
        for ed in self.education:
            lines.append(
                f"  - {ed.degree} {ed.field_of_study} @ {ed.institution}".strip()
            )
        return lines


def _strip_code_fence(text: str) -> str:
    """Strip a leading ```json fence if the model returned one anyway."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop first line (``` or ```json) and trailing ```.
        stripped = re.sub(r"^```[a-zA-Z]*\n", "", stripped)
        stripped = re.sub(r"\n```\s*$", "", stripped)
    return stripped


def parse_llm_response(raw: str) -> dict[str, Any]:
    """Parse the LLM's JSON response into a plain dict.

    Raises :class:`LLMError` if the content is not a JSON object. Accepts
    responses wrapped in a fenced block as a fallback.
    """
    if not raw.strip():
        raise LLMError("LLM returned empty response.")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = None
    if result is None:
        # Fallback 1: strip fences.
        stripped = _strip_code_fence(raw)
        try:
            result = json.loads(stripped)
        except json.JSONDecodeError:
            # Fallback 2: grab the first {...} balanced block.
            match = re.search(r"\{.*\}", stripped, re.DOTALL)
            if not match:
                raise LLMError(
                    f"LLM response was not valid JSON. "
                    f"First 200 chars: {raw[:200]}"
                ) from None
            try:
                result = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise LLMError(
                    f"LLM response was not valid JSON: {exc}. "
                    f"First 200 chars: {raw[:200]}"
                ) from exc
    if not isinstance(result, dict):
        raise LLMError(
            f"LLM response was not a JSON object. First 200 chars: {raw[:200]}"
        )
    return result


def _clamp_proficiency(value: object) -> int:
    """Coerce a LLM-provided proficiency into the 1-10 range."""
    if not isinstance(value, (int, str, float)):
        return 6
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 6
    return max(1, min(10, n))


def draft_from_dict(
    data: dict[str, Any],
    *,
    usage: LLMResponse | None = None,
    raw_response: str = "",
) -> ResumeDraft:
    """Convert a parsed dict into a :class:`ResumeDraft`.

    Unknown fields are silently dropped; missing fields get sensible
    defaults. We never raise on missing keys — the user will see what got
    extracted and can decide.
    """
    profile_raw = data.get("profile") or {}
    profile = ProfileSchema(
        display_name=str(profile_raw.get("display_name") or ""),
        headline=str(profile_raw.get("headline") or ""),
        summary=str(profile_raw.get("summary") or ""),
        location=str(profile_raw.get("location") or ""),
        contact_email=str(profile_raw.get("contact_email") or ""),
    )

    skills: list[SkillSchema] = []
    for item in data.get("skills") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        skills.append(
            SkillSchema(
                name=name,
                proficiency=_clamp_proficiency(item.get("proficiency")),
                category=str(item.get("category") or "technical"),
                notes=str(item.get("notes") or ""),
                source="mined",
            )
        )

    experiences: list[ExperienceSchema] = []
    for item in data.get("experiences") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        accomplishments_raw = item.get("accomplishments") or []
        if not isinstance(accomplishments_raw, list):
            accomplishments_raw = []
        experiences.append(
            ExperienceSchema(
                title=title,
                company=str(item.get("company") or ""),
                start_date=str(item.get("start_date") or ""),
                end_date=str(item.get("end_date") or ""),
                description=str(item.get("description") or ""),
                accomplishments=[str(a) for a in accomplishments_raw if a],
                source="imported",
            )
        )

    education: list[EducationSchema] = []
    for item in data.get("education") or []:
        if not isinstance(item, dict):
            continue
        institution = str(item.get("institution") or "").strip()
        if not institution:
            continue
        education.append(
            EducationSchema(
                institution=institution,
                degree=str(item.get("degree") or ""),
                field_of_study=str(item.get("field_of_study") or ""),
                start_date=str(item.get("start_date") or ""),
                end_date=str(item.get("end_date") or ""),
                description=str(item.get("description") or ""),
            )
        )

    return ResumeDraft(
        profile=profile,
        skills=skills,
        experiences=experiences,
        education=education,
        raw_response=raw_response,
        usage=usage,
    )


def resume_to_draft(
    path: Path,
    provider: LLMProvider,
    *,
    max_chars: int = MAX_RESUME_CHARS,
) -> ResumeDraft:
    """End-to-end: file on disk → LLM extraction → :class:`ResumeDraft`."""
    text = extract_resume_text(path)
    if not text.strip():
        raise ResumeExtractionError(
            f"Extracted zero text from {path}. Is the file empty or image-only?"
        )
    truncated = text[:max_chars]
    prompt = _USER_TEMPLATE.format(text=truncated)

    response = provider.complete(system=_SYSTEM_PROMPT, user=prompt)
    parsed = parse_llm_response(response.content)
    return draft_from_dict(parsed, usage=response, raw_response=response.content)
