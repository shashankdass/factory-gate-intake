"""Resume scanning — a PDF or photographed CV becomes structured JSON.

The output is split by sensitivity before it is ever persisted:

* ``name`` / ``phone`` / ``email`` are **PII** → encrypted at rest (see
  ``crypto.py``) and only ever indexed through keyed blind digests.
* ``place`` / ``stream`` / ``category`` / ``years_of_experience`` /
  ``qualification`` / ``skills`` are **non-PII** → plaintext columns in
  ``candidate_profiles`` / ``skills`` / ``candidate_skills``, fully indexable for
  fast multi-attribute fuzzy filtering.

Providers (``RESUME_PARSER_PROVIDER``):
  ``claude``  Anthropic vision → schema-constrained JSON (best quality)
  ``gemini``  Google Gemini vision
  ``ocr``     OCR text + heuristics — no LLM key required (default)
  ``mock``    canned values, no network (test suite)

Every provider degrades to ``ResumeExtraction.empty()`` plus a note rather than
raising, so a bad scan never blocks worker onboarding — the contractor simply
fills the fields in by hand.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field

from django.conf import settings

from . import ocr

logger = logging.getLogger(__name__)

# Controlled vocabularies. Kept small and industrial — these drive the
# contractor's filter dropdowns as well as the model's output.
STREAMS = ["Mechanical", "Civil", "Electrical", "Electronics", "Chemical",
           "Instrumentation", "Automobile", "General"]
CATEGORIES = ["Helper", "Technician", "Operator", "Supervisor", "Engineer", "Manager"]
QUALIFICATIONS = ["Below 10th", "10th", "12th", "ITI", "Diploma", "Bachelors",
                  "Masters", "Other"]

MAX_SKILLS = 15


@dataclass
class ResumeExtraction:
    """One candidate parsed from one resume (which may span several pages)."""

    name: str | None = None
    phone: str | None = None
    email: str | None = None
    place: str | None = None
    stream: str | None = None
    category: str | None = None
    years_of_experience: int | None = None
    qualification: str | None = None
    skills: list[str] = field(default_factory=list)
    provider: str = "none"
    note: str | None = None

    @classmethod
    def empty(cls, provider: str, note: str | None = None) -> "ResumeExtraction":
        return cls(provider=provider, note=note)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "phone": self.phone,
            "email": self.email,
            "place": self.place,
            "stream": self.stream,
            "category": self.category,
            "years_of_experience": self.years_of_experience,
            "qualification": self.qualification,
            "skills": self.skills,
            "provider": self.provider,
            "note": self.note,
        }


# ---------------------------------------------------------------------------
# Normalisation — the model is instructed, but never trusted, to stay in-vocab
# ---------------------------------------------------------------------------
def _closest(value: str | None, options: list[str]) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    for option in options:
        if option.lower() == cleaned.lower():
            return option
    for option in options:
        if option.lower() in cleaned.lower() or cleaned.lower() in option.lower():
            return option
    return cleaned[:80] or None


def _clean_phone(value: str | None) -> str | None:
    if not value:
        return None
    digits = re.sub(r"\D", "", value)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return digits[-10:] if len(digits) >= 10 else (digits or None)


def _clean_email(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", value)
    return match.group(0).lower() if match else None


def _clean_years(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        years = int(float(str(value).strip().split("-")[0]))
    except (TypeError, ValueError):
        return None
    return max(0, min(60, years))


def _clean_skills(values) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    for raw in values:
        skill = str(raw or "").strip()
        if not skill or len(skill.split()) > 3:
            continue
        skill = skill.title()
        if skill.lower() not in {s.lower() for s in out}:
            out.append(skill)
        if len(out) >= MAX_SKILLS:
            break
    return out


def normalise(payload: dict, provider: str, note: str | None = None) -> ResumeExtraction:
    return ResumeExtraction(
        name=(payload.get("name") or "").strip()[:150] or None,
        phone=_clean_phone(payload.get("phone")),
        email=_clean_email(payload.get("email")),
        place=(payload.get("place") or "").strip()[:120] or None,
        stream=_closest(payload.get("stream"), STREAMS),
        category=_closest(payload.get("category"), CATEGORIES),
        years_of_experience=_clean_years(payload.get("years_of_experience")),
        qualification=_closest(payload.get("qualification"), QUALIFICATIONS),
        skills=_clean_skills(payload.get("skills")),
        provider=provider,
        note=note,
    )


# ---------------------------------------------------------------------------
# Prompt + schema shared by the vision providers
# ---------------------------------------------------------------------------
SYSTEM_INSTRUCTION = (
    "You are a precise resume data-extraction engine for an industrial staffing "
    "desk in India. You read PDFs and photographs of printed or handwritten CVs, "
    "including low-light, skewed and partially blurred captures. "
    "You return data only — never commentary, never markdown."
)

PROMPT = f"""Extract the candidate's details from this resume.

The pages belong to ONE resume for ONE person, in order. Read every page before
answering and merge them into a single record. Never emit one record per page.

Rules:
1. Use ONLY what is visible in the document. Never invent a value, infer one
   from a name, or fill from general knowledge.
2. If a field is genuinely absent or unreadable use null (empty array for
   skills). A missing value is always better than a guessed one.
3. `place`: the current or preferred CITY only — drop state and country.
4. `stream`: derive from the qualification or job history ("Diploma in
   Mechanical Engg" -> "Mechanical"). Choose from: {", ".join(STREAMS)}.
5. `category`: the seniority band, NOT the trade. "ITI Welder with 4 yrs" ->
   category "Technician", skills ["Welder"]. Choose from: {", ".join(CATEGORIES)}.
6. `years_of_experience`: total professional years as a number. For a range take
   the lower bound; from employment dates, sum and round to the nearest year.
   Fresher / no experience -> 0.
7. `qualification`: the highest completed one only, from: {", ".join(QUALIFICATIONS)}.
8. `skills`: short noun phrases, max 3 words each, max {MAX_SKILLS} entries.
   Include trades, machines, certifications and software. Exclude soft skills
   and the qualification itself.
9. Do not translate; keep proper nouns as printed."""

RESUME_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": ["string", "null"], "description": "Full name as printed"},
        "phone": {"type": ["string", "null"], "description": "Primary mobile, digits only"},
        "email": {"type": ["string", "null"], "description": "Primary email, lowercase"},
        "place": {"type": ["string", "null"], "description": "City"},
        "stream": {"type": ["string", "null"], "enum": STREAMS + [None]},
        "category": {"type": ["string", "null"], "enum": CATEGORIES + [None]},
        "years_of_experience": {"type": ["integer", "null"], "minimum": 0},
        "qualification": {"type": ["string", "null"], "enum": QUALIFICATIONS + [None]},
        "skills": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "phone", "email", "place", "stream", "category",
                 "years_of_experience", "qualification", "skills"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Provider: Claude vision
# ---------------------------------------------------------------------------
def _claude_content_block(data: bytes, content_type: str) -> dict:
    """A PDF becomes a document block; anything else an image block."""
    encoded = base64.standard_b64encode(data).decode("ascii")
    if (content_type or "").lower() == "application/pdf":
        return {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": encoded},
        }
    media_type = (content_type or "image/jpeg").lower()
    if media_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        media_type = "image/jpeg"
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": encoded},
    }


def _parse_with_claude(pages: list[tuple[bytes, str]]) -> ResumeExtraction:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY or None)

    content = [_claude_content_block(data, content_type) for data, content_type in pages]
    content.append({"type": "text", "text": PROMPT})

    response = client.messages.create(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=2048,
        system=SYSTEM_INSTRUCTION,
        # Extraction is a bounded, mechanical task — low effort keeps it fast and
        # cheap; the schema does the shaping, not the reasoning depth.
        output_config={
            "effort": "low",
            "format": {"type": "json_schema", "schema": RESUME_JSON_SCHEMA},
        },
        messages=[{"role": "user", "content": content}],
    )

    # A safety decline returns HTTP 200 — check before reading content.
    if response.stop_reason == "refusal":
        return ResumeExtraction.empty(
            "claude", "The model declined to read this document — enter the details manually."
        )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text.strip():
        return ResumeExtraction.empty("claude", "Empty response — enter the details manually.")
    return normalise(json.loads(text), "claude")


# ---------------------------------------------------------------------------
# Provider: Gemini vision
# ---------------------------------------------------------------------------
def _parse_with_gemini(pages: list[tuple[bytes, str]]) -> ResumeExtraction:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    parts = [types.Part.from_bytes(data=data, mime_type=content_type or "image/jpeg")
             for data, content_type in pages]
    parts.append(types.Part.from_text(text=PROMPT))

    response = client.models.generate_content(
        model=settings.GEMINI_MODEL,
        contents=parts,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )
    raw = (response.text or "").strip()
    raw = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw)
    return normalise(json.loads(raw), "gemini")


# ---------------------------------------------------------------------------
# Provider: OCR text + heuristics (no LLM key required)
# ---------------------------------------------------------------------------
_QUALIFICATION_PATTERNS = [
    (r"\bM\.?\s?TECH|\bMASTERS?\b|\bMBA\b|\bM\.?\s?SC\b", "Masters"),
    (r"\bB\.?\s?TECH|\bB\.?\s?E\b|\bBACHELOR|\bB\.?\s?SC\b|\bDEGREE\b", "Bachelors"),
    (r"\bDIPLOMA\b", "Diploma"),
    (r"\bITI\b|INDUSTRIAL TRAINING", "ITI"),
    (r"\b12TH\b|\bHSC\b|INTERMEDIATE", "12th"),
    (r"\b10TH\b|\bSSC\b|MATRIC", "10th"),
]

_CATEGORY_PATTERNS = [
    (r"\bMANAGER\b|\bHEAD\b", "Manager"),
    (r"\bENGINEER\b", "Engineer"),
    (r"\bSUPERVISOR\b|\bFOREMAN\b|IN[- ]?CHARGE", "Supervisor"),
    (r"\bOPERATOR\b", "Operator"),
    (r"\bTECHNICIAN\b|\bFITTER\b|\bWELDER\b|\bELECTRICIAN\b", "Technician"),
    (r"\bHELPER\b|\bLABOU?R\b", "Helper"),
]

_SKILL_VOCAB = [
    "Welder", "Fitter", "Electrician", "Mason", "Carpenter", "Plumber", "Painter",
    "Rigger", "Machinist", "Turner", "Lineman", "Wireman", "Mechanic", "Helper",
    "Scaffolder", "Grinder", "Crane Operator", "Forklift", "Lathe", "Milling",
    "AutoCAD", "Welding", "Fabrication", "Plumbing", "Wiring", "Shuttering",
]


def _parse_with_ocr(pages: list[tuple[bytes, str]]) -> ResumeExtraction:
    chunks, provider, err = [], "ocr", None
    for data, content_type in pages:
        text, provider, page_err = ocr.extract_text(data, "resume", content_type)
        err = err or page_err
        if text:
            chunks.append(text)

    joined = "\n".join(chunks)
    if not joined.strip():
        return ResumeExtraction.empty(
            provider, err or "No text detected — enter the details manually."
        )

    up = joined.upper()
    qualification = next((q for pattern, q in _QUALIFICATION_PATTERNS
                          if re.search(pattern, up)), None)
    category = next((c for pattern, c in _CATEGORY_PATTERNS if re.search(pattern, up)), None)
    stream = next((s for s in STREAMS if s.upper() in up), None)
    years = None
    years_match = re.search(r"(\d{1,2})\+?\s*(?:YEARS?|YRS?)\b", up)
    if years_match:
        years = int(years_match.group(1))

    # First plausible all-letters line that isn't a section heading.
    name = None
    for line in joined.splitlines():
        candidate = line.strip()
        if re.fullmatch(r"[A-Za-z .]{4,40}", candidate) and not re.search(
            r"RESUME|CURRICULUM|VITAE|OBJECTIVE|EXPERIENCE|EDUCATION|SKILL|"
            r"ADDRESS|CONTACT|PERSONAL|DECLARATION|PROFILE|MOBILE|EMAIL",
            candidate.upper(),
        ):
            name = candidate.title()
            break

    place_match = re.search(r"(?:CITY|PLACE|LOCATION|ADDRESS)\s*[:\-]\s*([A-Za-z ]{3,40})", up)
    # Prefer a valid Indian mobile; fall back to any bare 10-digit run.
    phone_match = (re.search(r"\b(?:\+?91[\s-]?)?[6-9]\d{9}\b", joined)
                   or re.search(r"\b\d{10}\b", joined))

    return normalise(
        {
            "name": name,
            "phone": phone_match.group(0) if phone_match else None,
            "email": _clean_email(joined),
            "place": place_match.group(1).title().strip() if place_match else None,
            "stream": stream,
            "category": category,
            "years_of_experience": years,
            "qualification": qualification,
            "skills": [s for s in _SKILL_VOCAB if s.upper() in up],
        },
        provider,
        None,
    )


# ---------------------------------------------------------------------------
# Provider: mock
# ---------------------------------------------------------------------------
def _parse_mock(_pages) -> ResumeExtraction:
    return normalise(
        {
            "name": "Ravi Kumar",
            "phone": "9876543210",
            "email": "ravi.kumar@example.com",
            "place": "Pune",
            "stream": "Mechanical",
            "category": "Technician",
            "years_of_experience": 6,
            "qualification": "ITI",
            "skills": ["Welder", "Fitter", "Fabrication"],
        },
        "mock",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
_PROVIDERS = {
    "claude": _parse_with_claude,
    "gemini": _parse_with_gemini,
    "ocr": _parse_with_ocr,
    "mock": _parse_mock,
}


def parse_resume(pages: list[tuple[bytes, str]]) -> ResumeExtraction:
    """Parse one resume from an ordered list of ``(bytes, content_type)`` pages."""
    if not pages:
        return ResumeExtraction.empty("none", "No resume file was supplied.")

    provider = settings.RESUME_PARSER_PROVIDER
    handler = _PROVIDERS.get(provider)
    if handler is None:
        return ResumeExtraction.empty(
            provider, f"Unknown RESUME_PARSER_PROVIDER '{provider}'."
        )

    try:
        return handler(pages)
    except Exception as exc:  # noqa: BLE001 — a bad scan never blocks onboarding
        logger.exception("Resume parsing failed via %s", provider)
        return ResumeExtraction.empty(
            provider, f"Could not read the resume ({exc}). Enter the details manually."
        )
