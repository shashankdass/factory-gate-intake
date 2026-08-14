"""Document-type verification.

The rule under test is the asymmetric one: reject only on positive evidence of a
*different* document, never on absent evidence. Getting that backwards would
make the product unusable in the field, where half the scans are phone photos
taken in bad light.
"""
import pytest

from intake import doctype

# --- Representative OCR text for each document ----------------------------
AADHAAR = """
GOVERNMENT OF INDIA
Ravi Kumar
DOB: 14/05/1990   Male
2345 6789 0123
Unique Identification Authority of India
"""

PAN = """
INCOME TAX DEPARTMENT      GOVT. OF INDIA
Permanent Account Number
ABCDE1234F
RAVI KUMAR
Father's Name: SURESH KUMAR
Signature
"""

CHEQUE = """
HDFC BANK LTD
Pay to .................................. or bearer
A/C No: 50100123456789
IFSC: HDFC0001234    MICR 400240123
"""

MEDICAL = """
MEDICAL FITNESS CERTIFICATE
This is to certify that the applicant is physically fit for work.
Vision: 6/6    Blood Group: O+
Colour blindness: None    Vertigo: None
Examination conducted by Dr. A. Sharma
"""

POLICE = """
POLICE VERIFICATION CERTIFICATE
Character and antecedents verified at the local police station.
No adverse report. No criminal record found.
Superintendent of Police
"""

RESUME = """
RAVI KUMAR — CURRICULUM VITAE
Career Objective: to work as a welder in a reputed organisation
Work Experience: 6 years fabrication
Educational Qualification: ITI Welder
Key Skills: welding, fitting
Declaration: I hereby declare that the above is true.
"""


# ---------------------------------------------------------------------------
# The happy path: each document in its own slot
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "slot,text",
    [
        ("aadhaar", AADHAAR),
        ("pan", PAN),
        ("bank", CHEQUE),
        ("medical", MEDICAL),
        ("pvc", POLICE),
        ("resume", RESUME),
    ],
)
def test_a_document_in_its_own_slot_is_accepted(slot, text):
    verdict = doctype.check_document(slot, text, {})

    assert verdict.status == "OK"
    assert verdict.ok is True


# ---------------------------------------------------------------------------
# The requirement: a resume must not pass as an Aadhaar card
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "slot,text,expected_word",
    [
        ("aadhaar", RESUME, "resume"),
        ("aadhaar", CHEQUE, "cheque"),
        ("bank", RESUME, "resume"),
        ("pvc", RESUME, "resume"),
        ("medical", RESUME, "resume"),
    ],
)
def test_a_document_in_the_wrong_slot_is_refused(slot, text, expected_word):
    verdict = doctype.check_document(slot, text, {})

    assert verdict.status == "MISMATCH"
    assert verdict.ok is False
    assert expected_word in verdict.message.lower()


def test_the_refusal_names_both_documents():
    verdict = doctype.check_document("aadhaar", RESUME, {})

    assert "resume" in verdict.message.lower()
    assert "aadhaar card" in verdict.message.lower()
    assert verdict.as_dict()["detected_label"] == "resume"
    assert verdict.as_dict()["expected_label"] == "Aadhaar card"


# ---------------------------------------------------------------------------
# Absent evidence is never a rejection
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["", "   ", "blurry", "x" * 30])
def test_unreadable_text_is_never_refused(text):
    verdict = doctype.check_document("aadhaar", text, {})

    assert verdict.status == "UNVERIFIED"
    assert verdict.ok is True


def test_text_that_matches_nothing_is_allowed_through():
    """An unrecognised but legible document is a shrug, not a rejection."""
    verdict = doctype.check_document(
        "aadhaar", "The quick brown fox jumps over the lazy dog, repeatedly.", {}
    )

    assert verdict.status == "UNVERIFIED"
    assert verdict.ok is True


def test_a_weak_signal_does_not_beat_the_expected_type():
    """One stray word must not outrank the slot the user chose."""
    verdict = doctype.check_document("medical", "Certificate. Signature. Bank.", {})

    assert verdict.ok is True


# ---------------------------------------------------------------------------
# Identifier validation — warnings, never blocks
# ---------------------------------------------------------------------------
def test_aadhaar_checksum_uses_verhoeff():
    # Generated so the Verhoeff check digit is correct.
    assert doctype.aadhaar_checksum_valid("234567890124") is True
    # Same digits, wrong check digit.
    assert doctype.aadhaar_checksum_valid("234567890123") is False
    assert doctype.aadhaar_checksum_valid("12345") is False
    assert doctype.aadhaar_checksum_valid(None) is False


def test_a_failing_checksum_warns_but_does_not_refuse():
    """Seed and demo data legitimately use numbers that were never issued."""
    verdict = doctype.check_document("aadhaar", AADHAAR, {"aadhar_number": "100000000001"})

    assert verdict.ok is True
    assert any("checksum" in w for w in verdict.warnings)


def test_a_valid_checksum_produces_no_warning():
    verdict = doctype.check_document("aadhaar", AADHAAR, {"aadhar_number": "234567890124"})

    assert verdict.warnings == []


@pytest.mark.parametrize(
    "value,valid",
    [("ABCDE1234F", True), ("abcde1234f", True), ("ABCD1234F", False), ("", False)],
)
def test_pan_format(value, valid):
    assert doctype.pan_valid(value) is valid


@pytest.mark.parametrize(
    "value,valid",
    [
        ("HDFC0001234", True),
        ("SBIN0000456", True),
        ("HDFC1001234", False),  # the fifth character must be zero
        ("HDF00001234", False),  # bank code is four letters
        ("", False),
    ],
)
def test_ifsc_format(value, valid):
    assert doctype.ifsc_valid(value) is valid


def test_a_bad_pan_warns_without_refusing():
    verdict = doctype.check_document("pan", PAN, {"document_number": "NOTAPAN"})

    assert verdict.ok is True
    assert any("PAN" in w for w in verdict.warnings)


# ---------------------------------------------------------------------------
# Bank field extraction
# ---------------------------------------------------------------------------
def test_bank_fields_come_off_a_cheque():
    fields = doctype.extract_bank_fields(CHEQUE)

    assert fields["bank_account_number"] == "50100123456789"
    assert fields["ifsc"] == "HDFC0001234"
    assert "Bank" in fields["bank_name"]


def test_bank_extraction_prefers_a_labelled_account_number():
    text = "Date 01/01/2026 Cheque 000123456 A/C No 91234567890123 IFSC SBIN0000456"

    fields = doctype.extract_bank_fields(text)

    assert fields["bank_account_number"] == "91234567890123"


def test_bank_extraction_tolerates_a_blank_page():
    assert doctype.extract_bank_fields("") == {
        "bank_account_number": "",
        "ifsc": "",
        "bank_name": "",
    }


# ---------------------------------------------------------------------------
# Slot plumbing
# ---------------------------------------------------------------------------
def test_every_intake_slot_maps_to_a_signature():
    for slot, key in doctype.SLOT_EXPECTS.items():
        assert key in doctype.SIGNATURES, slot


def test_an_unknown_slot_is_not_treated_as_a_mismatch():
    verdict = doctype.check_document("nonsense", AADHAAR, {})

    assert verdict.ok is True
    assert verdict.status == "UNVERIFIED"
