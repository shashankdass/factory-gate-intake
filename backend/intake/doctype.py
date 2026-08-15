"""Document-type verification — is this actually an Aadhaar card, or a resume?

Every intake slot expects a specific document. Nothing stopped a resume being
attached to the Aadhaar slot, which then OCRs into nonsense and, worse, gets
verified and stored as identity evidence.

How it decides
--------------
Each document type has a set of **textual markers** (phrases that appear on that
document and rarely elsewhere) and, where one exists, a **structural identifier**
(a number with a known format). We score the OCR text against every type and
compare the score for the slot the user chose against the best-scoring
alternative.

The decision rule is deliberately asymmetric. There are two ways to fail:

1. **Positive evidence of a different document type** — the page scores higher
   as a resume than as the Aadhaar card the slot expects.

2. **A readable page missing the mark that defines the type.** Only applies to
   documents that have such a mark: an Aadhaar card always carries a 12-digit
   number or UIDAI markings, a cheque always names a bank. If OCR returned a
   wall of clean text and none of it appears, the document has been read and it
   is not what the slot expects. This is the one place absence counts, and it
   is gated on ``MIN_DEFINING_TEXT`` so it can only trigger when we know OCR
   worked. Free-form documents — medical, police, safety certificates — have no
   universal mark and are exempt.

Everything else is ``UNVERIFIED``, which does not block. A phone photo taken in
bad light, a scanned page with no text layer, an OCR provider that is down or
rate-limited all produce little or no text, and blocking those would make the
intake unusable in exactly the field conditions this product exists for. A
false rejection strands a worker at the gate; a false acceptance is caught by
the reviewer looking at the thumbnail.

Identifier checks (Aadhaar's Verhoeff checksum, PAN and IFSC formats) contribute
*positive* signal only. A failing checksum is surfaced as a warning rather than a
block, because seeded demo data and test fixtures legitimately use numbers that
were never issued.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Structural identifiers
# ---------------------------------------------------------------------------
AADHAAR_RE = re.compile(r"\b(\d{4}\s?\d{4}\s?\d{4})\b")
PAN_RE = re.compile(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b")
# 4-letter bank code, a reserved 0, then a 6-character branch code.
IFSC_RE = re.compile(r"\b([A-Z]{4}0[A-Z0-9]{6})\b")
# Bank account numbers run 9-18 digits; anything shorter collides with dates.
ACCOUNT_RE = re.compile(r"\b(\d{9,18})\b")
# An email address. No ID card, cheque or certificate carries one; a resume
# almost always does. Weak on its own, which is why it is scored as one
# pattern among the section headings rather than as defining evidence.
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)

# --- Verhoeff checksum, the scheme Aadhaar numbers use ---------------------
_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def aadhaar_checksum_valid(number: str | None) -> bool:
    """Verhoeff check over a 12-digit Aadhaar number."""
    digits = re.sub(r"\D", "", number or "")
    if len(digits) != 12:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        checksum = _D[checksum][_P[index % 8][int(digit)]]
    return checksum == 0


def pan_valid(value: str | None) -> bool:
    return bool(PAN_RE.fullmatch((value or "").strip().upper()))


def ifsc_valid(value: str | None) -> bool:
    return bool(IFSC_RE.fullmatch((value or "").strip().upper()))


def account_number_valid(value: str | None) -> bool:
    return bool(ACCOUNT_RE.fullmatch(re.sub(r"[\s-]", "", value or "")))


# ---------------------------------------------------------------------------
# Type signatures
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Signature:
    label: str
    # Phrases that appear on this document and rarely on the others.
    markers: tuple[str, ...]
    # Regexes whose presence is strong structural evidence.
    patterns: tuple[re.Pattern, ...] = ()
    # Phrases that all but rule the type out when present.
    negative: tuple[str, ...] = ()

    # --- Defining evidence -------------------------------------------------
    # Marks that *every* genuine instance of this document carries: the issuing
    # authority's name, or the identifier the document exists to convey. Only
    # set for documents that have such a thing — an Aadhaar card always bears a
    # 12-digit number, a medical certificate has no universal equivalent.
    #
    # When the page read cleanly and none of this appears, the document is not
    # this type. That is the one situation where absence *is* evidence: we know
    # OCR worked, because it returned plenty of text.
    defining: tuple[str, ...] = ()
    defining_patterns: tuple[re.Pattern, ...] = ()
    # What to tell the contractor was missing.
    missing_hint: str = ""

    def has_defining_evidence(self, lowered: str, upper: str) -> bool:
        if not self.defining and not self.defining_patterns:
            return True  # no universal mark to look for; rule does not apply
        return (
            any(mark in lowered for mark in self.defining)
            or any(p.search(upper) for p in self.defining_patterns)
        )


SIGNATURES: dict[str, Signature] = {
    "AADHAAR": Signature(
        label="Aadhaar card",
        markers=(
            "government of india", "unique identification", "uidai", "aadhaar",
            "aadhar", "आधार", "भारत सरकार", "year of birth", "vid ",
            "mera aadhaar", "meri pehchan",
        ),
        patterns=(AADHAAR_RE,),
        negative=("curriculum vitae", "permanent account number"),
        defining=(
            "aadhaar", "aadhar", "आधार", "uidai", "unique identification",
            "government of india", "भारत सरकार",
        ),
        defining_patterns=(AADHAAR_RE,),
        missing_hint="no Aadhaar number and no UIDAI or Government of India markings",
    ),
    "PAN": Signature(
        label="PAN card",
        markers=(
            "income tax department", "permanent account number", "govt. of india",
            "pan card", "father's name", "signature",
        ),
        patterns=(PAN_RE,),
        negative=("curriculum vitae",),
        defining=("permanent account number", "income tax department", "pan"),
        defining_patterns=(PAN_RE,),
        missing_hint="no PAN number and no Income Tax Department markings",
    ),
    "BANK": Signature(
        label="cancelled cheque or passbook",
        markers=(
            "ifsc", "micr", "a/c no", "account number", "account no", "bank",
            "branch", "passbook", "cheque", "payee", "savings account",
            "current account", "pay to", "or bearer",
        ),
        patterns=(IFSC_RE,),
        defining=(
            "ifsc", "micr", "a/c", "account", "bank", "passbook", "cheque",
            "branch",
        ),
        defining_patterns=(IFSC_RE,),
        missing_hint="no account number, IFSC or bank name",
    ),
    "MEDICAL": Signature(
        label="medical fitness report",
        markers=(
            "medical", "fitness", "physically fit", "examination", "vision",
            "blood group", "blood pressure", "physician", "dr.", "colour blind",
            "color blind", "vertigo", "fit for work",
        ),
    ),
    "POLICE": Signature(
        label="police verification certificate",
        markers=(
            "police", "verification certificate", "character", "antecedents",
            "commissioner", "police station", "no adverse", "criminal record",
            "superintendent of police",
        ),
    ),
    "SAFETY": Signature(
        label="safety training certificate",
        markers=(
            "safety", "training", "induction", "has successfully completed",
            "certificate of completion", "valid till", "valid up to", "ppe",
            "occupational health",
        ),
    ),
    "RESUME": Signature(
        label="resume",
        markers=(
            # Indian conventions
            "curriculum vitae", "career objective", "educational qualification",
            "declaration", "i hereby declare", "father's name",
            # Section headings any resume uses, wherever it was written. The
            # first list was India-only, which let an ordinary US template
            # through with a score of zero.
            "resume", "objective", "summary", "profile", "skills",
            "experience", "work experience", "professional experience",
            "employment", "employment history", "education", "qualifications",
            "certifications", "achievements", "projects", "references",
            "responsibilities", "linkedin.com/in", "willing to work",
            "years of experience", "proficient in", "familiar with",
        ),
        patterns=(EMAIL_RE,),
    ),
}

# Which signature each intake slot expects.
SLOT_EXPECTS = {
    "aadhaar": "AADHAAR",
    "pan": "PAN",
    "bank": "BANK",
    "medical": "MEDICAL",
    "pvc": "POLICE",
    "safety": "SAFETY",
    "resume": "RESUME",
}

# Tuning. A marker is worth 1, a structural pattern 2 — a PAN-shaped code on the
# page is far better evidence than the word "signature".
MARKER_WEIGHT = 1
PATTERN_WEIGHT = 2
# Below this, we have not seen enough to call the document anything at all.
MIN_CONFIDENT_SCORE = 3
# The winner must beat the expected type by this much before we reject.
REJECT_MARGIN = 2
# Fewer characters than this and OCR effectively failed — never judge on it.
MIN_TEXT_LENGTH = 40
# Above this much clean text, OCR demonstrably worked — so a defining mark
# being absent is a fact about the document, not about the scan. Deliberately
# well above MIN_TEXT_LENGTH: a half-legible ID card can lose its markings, and
# five lines of text is not enough to conclude anything from a silence.
MIN_DEFINING_TEXT = 200


def score_text(text: str) -> dict[str, int]:
    """Score OCR text against every known document signature."""
    lowered = (text or "").lower()
    upper = (text or "").upper()
    scores: dict[str, int] = {}
    for key, signature in SIGNATURES.items():
        if any(bad in lowered for bad in signature.negative):
            scores[key] = 0
            continue
        score = sum(MARKER_WEIGHT for m in signature.markers if m in lowered)
        score += sum(PATTERN_WEIGHT for p in signature.patterns if p.search(upper))
        scores[key] = score
    return scores


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


@dataclass
class DocumentCheck:
    """Verdict for one uploaded file against the slot it was dropped into."""

    status: str          # OK | MISMATCH | UNVERIFIED
    expected: str        # signature key, e.g. "AADHAAR"
    detected: str | None  # best-scoring alternative, when there is one
    message: str
    scores: dict[str, int]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        """Anything that is not a positive mismatch is allowed through."""
        return self.status != "MISMATCH"

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "ok": self.ok,
            "expected": self.expected,
            "expected_label": SIGNATURES[self.expected].label if self.expected in SIGNATURES else self.expected,
            "detected": self.detected,
            "detected_label": SIGNATURES[self.detected].label if self.detected in SIGNATURES else self.detected,
            "message": self.message,
            "warnings": self.warnings,
            "scores": self.scores,
        }


def check_document(slot: str, text: str, fields: dict | None = None) -> DocumentCheck:
    """Decide whether ``text`` plausibly came from the document ``slot`` expects.

    ``slot`` is an intake slot name ("aadhaar", "pan", "bank", …) or a signature
    key. Returns a verdict; only ``MISMATCH`` should block an upload.
    """
    expected = SLOT_EXPECTS.get(slot, slot).upper()
    fields = fields or {}
    warnings: list[str] = []

    if expected not in SIGNATURES:
        return DocumentCheck("UNVERIFIED", expected, None,
                             "No signature is defined for this slot.", {}, warnings)

    # Identifier-level warnings. These never block — demo and test data use
    # numbers that were never issued, and a real card can OCR one digit wrong.
    if expected == "AADHAAR":
        candidate = fields.get("aadhar_number") or ""
        if candidate and not aadhaar_checksum_valid(candidate):
            warnings.append(
                "The Aadhaar number read from this document fails its checksum — "
                "check the digits before saving."
            )
    if expected == "PAN":
        candidate = fields.get("document_number") or fields.get("aadhar_number") or ""
        if candidate and not pan_valid(candidate):
            warnings.append("That does not look like a valid PAN (ABCDE1234F).")

    stripped = (text or "").strip()
    if len(stripped) < MIN_TEXT_LENGTH:
        # Nothing readable. Could be a bad photo, a scan with no text layer, or a
        # dead OCR provider — none of which mean the wrong document.
        return DocumentCheck(
            "UNVERIFIED", expected, None,
            "Could not read enough text to verify this document — check it visually.",
            {}, warnings,
        )

    scores = score_text(stripped)
    expected_score = scores.get(expected, 0)
    others = {k: v for k, v in scores.items() if k != expected}
    best_other, best_other_score = max(others.items(), key=lambda kv: kv[1], default=(None, 0))

    # The page read cleanly but carries none of the marks this document type is
    # defined by. Below MIN_DEFINING_TEXT we stay silent — a part-legible scan
    # can lose them — but a wall of readable text that never says "Aadhaar" and
    # shows no 12-digit number is not an Aadhaar card, whatever else it is.
    signature = SIGNATURES[expected]
    if (
        len(stripped) >= MIN_DEFINING_TEXT
        and not signature.has_defining_evidence(stripped.lower(), stripped.upper())
    ):
        found = SIGNATURES[best_other].label if (
            best_other and best_other_score >= MIN_CONFIDENT_SCORE
        ) else None
        wanted = signature.label
        message = (
            f"This looks like {_article(found)} {found}, not "
            f"{_article(wanted)} {wanted}."
            if found
            else f"This does not look like {_article(wanted)} {wanted} — "
            f"{signature.missing_hint}."
        )
        return DocumentCheck(
            "MISMATCH", expected, best_other if found else None,
            f"{message} Attach the right document.", scores, warnings,
        )

    if expected_score >= MIN_CONFIDENT_SCORE and expected_score >= best_other_score:
        label = SIGNATURES[expected].label
        return DocumentCheck("OK", expected, expected,
                             f"Looks like {_article(label)} {label}.", scores, warnings)

    # Positive evidence of something else — this is the only blocking case.
    if (
        best_other
        and best_other_score >= MIN_CONFIDENT_SCORE
        and best_other_score - expected_score >= REJECT_MARGIN
    ):
        found, wanted = SIGNATURES[best_other].label, SIGNATURES[expected].label
        return DocumentCheck(
            "MISMATCH", expected, best_other,
            f"This looks like {_article(found)} {found}, not {_article(wanted)} "
            f"{wanted}. Attach it to the right slot.",
            scores, warnings,
        )

    wanted = SIGNATURES[expected].label
    return DocumentCheck(
        "UNVERIFIED", expected, None,
        f"Could not confirm this is {_article(wanted)} {wanted} — check it visually.",
        scores, warnings,
    )


# ---------------------------------------------------------------------------
# Photographs
# ---------------------------------------------------------------------------
# A face photo carries no text, so the scoring above cannot say anything about
# it. What it *can* be checked for is being an image at all — which is enough to
# keep a PDF resume out of the photo slot, the same mistake in a new place.
#
# Read from the bytes rather than the declared content type: the browser's guess
# is derived from the file extension and a renamed file lies about both.
_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "JPEG"),
    (b"\x89PNG\r\n\x1a\n", "PNG"),
    (b"GIF87a", "GIF"),
    (b"GIF89a", "GIF"),
    (b"BM", "BMP"),
)


def image_kind(blob: bytes) -> str | None:
    """The image format ``blob`` actually is, or None if it is not an image."""
    if not blob:
        return None
    for magic, name in _IMAGE_MAGIC:
        if blob.startswith(magic):
            return name
    # RIFF-wrapped (WebP) and ISO-BMFF (HEIC/HEIF, what iPhones produce) both
    # name their format a few bytes in rather than at offset zero.
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "WebP"
    if blob[4:8] == b"ftyp":
        brand = blob[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1"):
            return "HEIC"
        if brand in (b"avif", b"avis"):
            return "AVIF"
    return None


def check_photo(blob: bytes) -> DocumentCheck:
    """Is this a photograph, or a document that wandered into the photo slot?"""
    kind = image_kind(blob)
    if kind:
        return DocumentCheck("OK", "PHOTO", "PHOTO", f"{kind} image.", {}, [])
    looks_like_pdf = (blob or b"").startswith(b"%PDF")
    what = "a PDF" if looks_like_pdf else "not an image"
    return DocumentCheck(
        "MISMATCH", "PHOTO", None,
        f"The worker photo must be a photograph — this is {what}. "
        "Attach a JPEG, PNG, WebP or HEIC.",
        {}, [],
    )


def extract_bank_fields(text: str) -> dict:
    """Pull account number and IFSC off a cancelled cheque or passbook page."""
    upper = (text or "").upper()
    ifsc = IFSC_RE.search(upper)

    # Prefer a number labelled as an account; fall back to the longest digit run
    # that is not the IFSC or a cheque/MICR number.
    labelled = re.search(
        r"(?:A/C|ACCOUNT|ACC)\.?\s*(?:NO\.?|NUMBER)?\s*[:\-]?\s*(\d[\d\s-]{7,20}\d)", upper
    )
    account = ""
    if labelled:
        account = re.sub(r"[\s-]", "", labelled.group(1))
    else:
        candidates = [c for c in ACCOUNT_RE.findall(re.sub(r"[\s-]", "", upper))]
        if candidates:
            account = max(candidates, key=len)

    bank_name = ""
    name_match = re.search(r"\b([A-Z][A-Z&.\s]{3,30}?BANK(?:\s+OF\s+[A-Z]+)?)\b", upper)
    if name_match:
        bank_name = name_match.group(1).title().strip()

    return {
        "bank_account_number": account,
        "ifsc": ifsc.group(1) if ifsc else "",
        "bank_name": bank_name,
    }
