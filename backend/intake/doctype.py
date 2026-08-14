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

The decision rule is deliberately asymmetric:

    reject ONLY on positive evidence of a *different* document type.

Absence of evidence is never a rejection. A phone photo taken in bad light, a
scanned page with no text layer, an OCR provider that is down or rate-limited —
all produce little or no text, and blocking those would make the whole intake
unusable in exactly the field conditions this product exists for. Those cases
return ``UNVERIFIED`` and the upload proceeds with a note.

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
    ),
    "PAN": Signature(
        label="PAN card",
        markers=(
            "income tax department", "permanent account number", "govt. of india",
            "pan card", "father's name", "signature",
        ),
        patterns=(PAN_RE,),
        negative=("curriculum vitae",),
    ),
    "BANK": Signature(
        label="cancelled cheque or passbook",
        markers=(
            "ifsc", "micr", "a/c no", "account number", "account no", "bank",
            "branch", "passbook", "cheque", "payee", "savings account",
            "current account", "pay to", "or bearer",
        ),
        patterns=(IFSC_RE,),
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
            "curriculum vitae", "resume", "career objective", "objective",
            "work experience", "professional experience", "educational qualification",
            "education", "key skills", "declaration", "i hereby declare",
            "references", "employment history",
        ),
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
