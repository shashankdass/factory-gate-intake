"""Document OCR — pluggable provider with a never-fail contract.

Extracted from ``views.py`` so the resume parser can reuse the same text
extraction without importing the view layer.

Provider is chosen by ``OCR_PROVIDER``:
  ``ocrspace``  (default) free hosted OCR.space API (set ``OCRSPACE_API_KEY``)
  ``tesseract``           local Tesseract binary (dev only; not on Render native)
  ``mock``                canned values, no network (always works)

Any provider failure degrades to empty fields plus an "enter manually" note
rather than an error — the intake workflow must never hard-stop on OCR.
"""
from __future__ import annotations

import io
import os
import re
from datetime import timedelta

from django.utils import timezone

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})\b")


def to_iso_date(raw: str):
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
                "%m/%d/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return timezone.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def first_date(text: str):
    match = _DATE_RE.search(text or "")
    return to_iso_date(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
def ocrspace_text(file_bytes, filename, content_type) -> str:
    """Call the free OCR.space API and return the concatenated parsed text."""
    import requests

    api_key = os.environ.get("OCRSPACE_API_KEY", "helloworld")  # public demo key
    resp = requests.post(
        "https://api.ocr.space/parse/image",
        files={"file": (filename or "upload", file_bytes,
                        content_type or "application/octet-stream")},
        data={"apikey": api_key, "language": "eng", "OCREngine": "2",
              "isOverlayRequired": "false", "scale": "true"},
        timeout=40,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("IsErroredOnProcessing"):
        raise RuntimeError(data.get("ErrorMessage") or "OCR.space processing error")
    results = data.get("ParsedResults") or []
    return "\n".join(r.get("ParsedText", "") for r in results).strip()


def tesseract_text(file_bytes) -> str:
    import pytesseract
    from PIL import Image

    return pytesseract.image_to_string(Image.open(io.BytesIO(file_bytes)))


def extract_text(file_bytes, filename=None, content_type=None) -> tuple[str, str, str | None]:
    """Return ``(text, provider, error)``. Never raises."""
    provider = os.environ.get("OCR_PROVIDER", "ocrspace").lower()
    if provider == "mock":
        return "", "mock", None

    try:
        if provider == "tesseract":
            return tesseract_text(file_bytes), provider, None
        return ocrspace_text(file_bytes, filename, content_type), provider, None
    except Exception as exc:  # noqa: BLE001 — degrade to manual entry, never 500
        return "", provider, str(exc)


# ---------------------------------------------------------------------------
# Field parsing for the 5 intake pillars
# ---------------------------------------------------------------------------
def parse_fields(text: str, doc_type: str, requirement_name: str = "") -> dict:
    """Best-effort field extraction from raw OCR text. The contractor verifies."""
    t = text or ""
    up = t.upper()

    if doc_type == "MEDICAL":
        vm = re.search(r"\b6\s*/\s*(6|9|12|18|24|36|60)\b", t)
        # No trailing \b — a sign like "+" is non-word, so "B+" has no boundary after it.
        bm = re.search(r"\b(AB|A|B|O)\s*([+\-]|POS|NEG)", up)
        blood = ""
        if bm:
            blood = bm.group(1) + ("+" if bm.group(2) in ("+", "POS") else "-")
        flagged = re.compile(r"DETECT|PRESENT|POSITIVE|\bYES\b")
        return {
            "exam_date": first_date(t) or "",
            "vision": ("6/" + vm.group(1)) if vm else "",
            "blood_type": blood,
            "color_blindness": bool(re.search(r"COLOU?R\s*BLIND", up)) and bool(flagged.search(up)),
            "vertigo": bool(re.search(r"VERTIGO", up)) and bool(flagged.search(up)),
        }

    if doc_type == "POLICE":
        cert = re.search(r"\b([A-Z]{2,}[-/ ]?\d[\w-]*)\b", t)
        return {
            "certificate_number": cert.group(1) if cert else "",
            "issue_date": first_date(t) or "",
            "verification_status": "Verified",
        }

    # IDENTITY (Aadhaar / PAN). Use a literal space (not \s) between groups so the
    # match can't span a newline and swallow a nearby date's year.
    aadhaar = re.search(r"\b(\d{4} ?\d{4} ?\d{4})\b", t)
    pan = re.search(r"\b([A-Z]{5}\d{4}[A-Z])\b", up)
    number = ""
    if requirement_name == "PAN" and pan:
        number = pan.group(1)
    elif aadhaar:
        number = re.sub(r"\s", "", aadhaar.group(1))
    elif pan:
        number = pan.group(1)
    name = ""
    for line in t.splitlines():
        s = line.strip()
        if re.fullmatch(r"[A-Za-z ]{4,40}", s) and not re.search(
            r"GOVERNMENT|INDIA|MALE|FEMALE|DOB|YEAR|BIRTH|FATHER|ADDRESS|"
            r"PERMANENT|ACCOUNT|INCOME|DEPARTMENT|CARD|\bNAME\b|GENDER|AADHAAR|"
            r"WORKER|CERTIFICATE|VERIFICATION|FITNESS|MEDICAL|POLICE|STATUS|"
            r"ISSUE|EXAM|VISION|VERTIGO|BLOOD|COLOU?R|DETECTED|NONE|VERIFIED",
            s.upper(),
        ):
            name = s.title()
            break
    return {"name": name, "aadhar_number": number, "document_number": number}


def mock_fields(doc_type: str, requirement_name: str, today) -> dict:
    if doc_type == "MEDICAL":
        return {"exam_date": (today - timedelta(days=30)).isoformat(), "vision": "6/6",
                "blood_type": "O+", "color_blindness": False, "vertigo": False}
    if doc_type == "POLICE":
        return {"certificate_number": "PVC-DEMO-1001",
                "issue_date": (today - timedelta(days=30)).isoformat(),
                "verification_status": "Verified"}
    num = "ABCDE1234F" if requirement_name == "PAN" else "100000000001"
    return {"name": "Ravi Kumar", "aadhar_number": num, "document_number": num}


def extract_fields(file_bytes, filename, content_type, doc_type, requirement_name, today):
    """``(fields, provider, note)`` for the intake workbench's right-hand form."""
    provider = os.environ.get("OCR_PROVIDER", "ocrspace").lower()
    if provider == "mock":
        return mock_fields(doc_type, requirement_name, today), "mock", None

    text, provider, err = extract_text(file_bytes, filename, content_type)
    if not text or not text.strip():
        return {}, provider, err or "No text detected — enter the values manually."
    return parse_fields(text, doc_type, requirement_name), provider, None
