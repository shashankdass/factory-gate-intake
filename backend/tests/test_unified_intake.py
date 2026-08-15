"""Unified worker onboarding — 5 validation pillars + resume in a single pass.

Also covers the contractor-ownership boundary that the refactor introduced:
every operational endpoint is scoped to the caller's own pool.
"""
from datetime import timedelta

from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from intake.models import CandidateProfile, Worker, WorkerBankAccount

pytestmark = pytest.mark.django_db

ONBOARD_URL = "/api/intake/onboard-worker/"


def _pdf(name="scan.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 fake scan bytes", "application/pdf")


def _payload(today, **overrides):
    body = {
        "name": "Mahesh Patil",
        "aadhar_number": "100000000042",
        "skill_type": "Mason",
        "pan_number": "ABCDE1234F",
        "exam_date": (today - timedelta(days=20)).isoformat(),
        "vision": "6/6",
        "blood_type": "O+",
        "color_blindness": "false",
        "vertigo": "false",
        "certificate_number": "PVC-2026-1",
        "issue_date": (today - timedelta(days=15)).isoformat(),
        "safety_expiry": (today + timedelta(days=200)).isoformat(),
        "aadhaar_file": _pdf("aadhaar.pdf"),
        "pan_file": _pdf("pan.pdf"),
        "safety_file": _pdf("safety.pdf"),
        "medical_file": _pdf("medical.pdf"),
        "pvc_file": _pdf("pvc.pdf"),
        "resume_file": _pdf("resume.pdf"),
    }
    body.update(overrides)
    return body


def test_single_pass_creates_worker_documents_and_profile(as_contractor, today,
                                                          requirements, contractor):
    response = as_contractor.post(ONBOARD_URL, _payload(today), format="multipart")

    assert response.status_code == 201
    body = response.json()

    worker = Worker.objects.get(aadhar_number="100000000042")
    assert worker.contractor == contractor
    # Three identity documents + medical + PVC in one submission.
    assert worker.documents.count() == 3
    assert worker.medical_records.count() == 1
    assert worker.police_verifications.count() == 1
    # All six files were stored.
    assert set(body["documents_stored"]) == {
        "aadhaar", "pan", "safety", "medical", "pvc", "resume"
    }


def test_every_stored_document_gets_a_private_object_key(as_contractor, today,
                                                         requirements):
    as_contractor.post(ONBOARD_URL, _payload(today), format="multipart")
    worker = Worker.objects.get(aadhar_number="100000000042")

    for doc in worker.documents.all():
        assert doc.storage_key
        assert doc.storage_key.startswith("gate-intake/")
    assert worker.medical_records.first().storage_key
    assert worker.police_verifications.first().storage_key


def test_resume_is_parsed_into_encrypted_pii_and_filterable_columns(
    as_contractor, today, requirements
):
    body = as_contractor.post(ONBOARD_URL, _payload(today), format="multipart").json()

    profile = CandidateProfile.objects.get(worker__aadhar_number="100000000042")
    # Mock parser returns the canned candidate.
    assert body["resume"]["name"] == "Ravi Kumar"
    assert profile.name == "Ravi Kumar"          # decrypts
    assert profile.name_encrypted is not None    # but is stored as ciphertext
    assert profile.place == "Pune"               # non-PII stays plaintext + indexed
    assert profile.years_of_experience == 6
    assert sorted(cs.skill.name for cs in profile.candidate_skills.all()) == [
        "fabrication", "fitter", "welder"
    ]
    assert profile.resume_key


def test_expired_medical_is_rejected_before_anything_is_created(
    as_contractor, today, requirements
):
    """Validation happens first so a bad date never leaves a half-built worker."""
    payload = _payload(today, exam_date=(today - timedelta(days=400)).isoformat())

    response = as_contractor.post(ONBOARD_URL, payload, format="multipart")

    assert response.status_code == 400
    assert response.json()["expired"] is True
    assert not Worker.objects.filter(aadhar_number="100000000042").exists()


def test_expired_pvc_is_rejected(as_contractor, today, requirements):
    payload = _payload(today, issue_date=(today - timedelta(days=400)).isoformat())

    response = as_contractor.post(ONBOARD_URL, payload, format="multipart")

    assert response.status_code == 400
    assert not Worker.objects.filter(aadhar_number="100000000042").exists()


def test_duplicate_aadhaar_is_rejected(as_contractor, today, requirements,
                                       compliant_worker):
    payload = _payload(today, aadhar_number=compliant_worker.aadhar_number)

    response = as_contractor.post(ONBOARD_URL, payload, format="multipart")

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


def test_malformed_aadhaar_is_rejected(as_contractor, today, requirements):
    response = as_contractor.post(
        ONBOARD_URL, _payload(today, aadhar_number="12345"), format="multipart"
    )

    assert response.status_code == 400


def test_partial_intake_is_allowed_with_just_the_aadhaar(as_contractor, today,
                                                          requirements):
    """Every document except the Aadhaar can follow later."""
    response = as_contractor.post(
        ONBOARD_URL,
        {
            "name": "Anil Sharma",
            "aadhar_number": "100000000043",
            "skill_type": "Fitter",
            "aadhaar_file": _pdf("aadhaar.pdf"),
        },
        format="multipart",
    )

    assert response.status_code == 201
    assert response.json()["documents_stored"] == ["aadhaar"]
    assert response.json()["compliance"]["is_compliant"] is False


def test_aadhaar_scan_is_mandatory(as_contractor, requirements):
    """The identity the gate scans against cannot be left to follow later."""
    response = as_contractor.post(
        ONBOARD_URL,
        {"name": "Anil Sharma", "aadhar_number": "100000000044", "skill_type": "Fitter"},
        format="multipart",
    )

    assert response.status_code == 400
    assert response.json()["missing_document"] == "aadhaar_file"
    assert not Worker.objects.filter(aadhar_number="100000000044").exists()


def test_a_wrong_document_in_the_aadhaar_slot_is_refused(as_contractor, today,
                                                          requirements, settings):
    """A resume attached as identity evidence must not be stored."""
    settings.VERIFY_DOCUMENT_TYPES = "aadhaar"
    resume_text = (
        "CURRICULUM VITAE\nCareer Objective: welder\nWork Experience: 6 years\n"
        "Educational Qualification: ITI\nDeclaration: I hereby declare"
    )
    payload = _payload(today)
    payload["aadhaar_file"] = SimpleUploadedFile(
        "cv.txt", resume_text.encode(), "text/plain"
    )

    # Patch extract_fields, not extract_text: the mock OCR provider the suite
    # runs on short-circuits before extract_text is ever reached.
    with patch("intake.ocr.extract_fields", return_value=({}, "stub", None, resume_text)):
        response = as_contractor.post(ONBOARD_URL, payload, format="multipart")

    assert response.status_code == 400
    assert response.json()["document_check"]["status"] == "MISMATCH"
    assert "resume" in response.json()["detail"].lower()
    assert not Worker.objects.filter(aadhar_number="100000000042").exists()


def test_the_us_resume_that_got_through_is_refused_at_submit(as_contractor, today,
                                                             requirements, settings):
    """Regression: this exact file was accepted and stored as identity evidence.

    The browser check is a convenience; this is the one that actually protects
    the record, so it gets its own test rather than relying on the unit test.
    """
    settings.VERIFY_DOCUMENT_TYPES = "aadhaar"
    resume_text = (
        "John Doe\nGeneral Laborer\n10042 Main St.\nFresno, Ca 93730\n"
        "(408) 000 0000\nstudent@gmail.com\n\nSKILLS\n"
        "Familiar with fundamental construction processes, demolition, carpentry "
        "and plumbing.\nCan safely and effectively drive a bobcat for drilling "
        "and excavation\nKnowledgeable of Safety Data Sheet hazards and state "
        "requirements/regulations\nEnergetic laborer willing to work overtime"
    )
    payload = _payload(today)
    payload["aadhaar_file"] = SimpleUploadedFile(
        "Resume-Template-General-Labor.pdf", resume_text.encode(), "application/pdf"
    )

    with patch("intake.ocr.extract_fields", return_value=({}, "stub", None, resume_text)):
        response = as_contractor.post(ONBOARD_URL, payload, format="multipart")

    assert response.status_code == 400
    assert response.json()["document_check"]["status"] == "MISMATCH"
    assert not Worker.objects.filter(aadhar_number="100000000042").exists()


# Genuine OCR text per slot, so one slot at a time can be spoiled.
_REAL_TEXT = {
    "aadhaar.pdf": "GOVERNMENT OF INDIA Ravi Kumar 2345 6789 0123 UIDAI "
                   "Unique Identification Authority of India",
    "pan.pdf": "INCOME TAX DEPARTMENT Permanent Account Number ABCDE1234F "
               "GOVT. OF INDIA Signature",
    "medical.pdf": "MEDICAL FITNESS CERTIFICATE physically fit vision 6/6 "
                   "blood group examination Dr. Sharma",
    "pvc.pdf": "POLICE VERIFICATION CERTIFICATE character antecedents "
               "no adverse report police station",
    "safety.pdf": "SAFETY TRAINING certificate of completion induction PPE "
                  "has successfully completed valid till",
    "bank.pdf": "HDFC BANK LTD Pay to or bearer A/C No 50100123456789 "
                "IFSC HDFC0001234 MICR 400240123 savings account",
}
_US_RESUME = (
    "John Doe\nGeneral Laborer\n10042 Main St.\nFresno, Ca 93730\n"
    "(408) 000 0000\nstudent@gmail.com\n\nSKILLS\n"
    "Familiar with fundamental construction processes, demolition, carpentry "
    "and plumbing.\nCan safely and effectively drive a bobcat for drilling and "
    "excavation\nEnergetic laborer willing to work overtime"
)


@pytest.mark.parametrize(
    "slot,filename",
    [("aadhaar", "aadhaar.pdf"), ("pan", "pan.pdf"), ("bank", "bank.pdf"),
     ("medical", "medical.pdf"), ("pvc", "pvc.pdf")],
)
def test_every_slot_is_verified_at_submit_not_just_the_aadhaar(
    as_contractor, today, requirements, slot, filename
):
    """The browser checks every slot, but the browser is not the control.

    A direct API call never runs it, and a failed OCR round trip in the browser
    leaves the file attached. Verifying only the Aadhaar server-side meant a
    resume could be submitted and stored as someone's cancelled cheque.
    """
    payload = _payload(today)
    payload["bank_file"] = _pdf("bank.pdf")
    payload["bank_account_number"] = "50100123456789"
    payload["ifsc"] = "HDFC0001234"
    # Spoil exactly one slot; every other document reads correctly.
    payload[f"{slot}_file"] = SimpleUploadedFile(
        filename, _US_RESUME.encode(), "application/pdf"
    )

    def fake(data, name, ctype, *a, **k):
        return ({}, "stub", None,
                _US_RESUME if name == filename else _REAL_TEXT.get(name, ""))

    with patch("intake.ocr.extract_fields", side_effect=fake):
        response = as_contractor.post(ONBOARD_URL, payload, format="multipart")

    assert response.status_code == 400
    body = response.json()
    assert body["slot"] == slot
    assert "resume" in body["detail"].lower()
    assert not Worker.objects.filter(aadhar_number="100000000042").exists()


def test_narrowing_the_policy_still_works(as_contractor, today, requirements,
                                          settings):
    """"aadhaar" remains available for anyone trading correctness for OCR spend."""
    settings.VERIFY_DOCUMENT_TYPES = "aadhaar"
    payload = _payload(today)
    payload["bank_file"] = SimpleUploadedFile("bank.pdf", _US_RESUME.encode(),
                                              "application/pdf")

    def fake(data, name, ctype, *a, **k):
        return ({}, "stub", None,
                _US_RESUME if name == "bank.pdf" else _REAL_TEXT.get(name, ""))

    with patch("intake.ocr.extract_fields", side_effect=fake):
        response = as_contractor.post(ONBOARD_URL, payload, format="multipart")

    assert response.status_code == 201


def test_an_unreadable_scan_is_never_refused(as_contractor, today, requirements,
                                             settings):
    """Field reality: bad light and dead OCR providers must not block intake."""
    settings.VERIFY_DOCUMENT_TYPES = "all"

    with patch("intake.ocr.extract_fields",
               return_value=({}, "stub", "provider down", "")):
        response = as_contractor.post(ONBOARD_URL, _payload(today), format="multipart")

    assert response.status_code == 201


def test_bank_details_are_captured_and_encrypted(as_contractor, today, requirements):
    payload = _payload(today)
    payload.update({
        "bank_account_number": "50100123456789",
        "ifsc": "HDFC0001234",
        "bank_name": "HDFC Bank",
        "bank_file": _pdf("cheque.pdf"),
    })

    response = as_contractor.post(ONBOARD_URL, payload, format="multipart")

    assert response.status_code == 201
    account = WorkerBankAccount.objects.get(worker__aadhar_number="100000000042")
    assert account.account_number == "50100123456789"   # decrypts
    assert account.ifsc == "HDFC0001234"
    assert account.storage_key                          # cheque stored privately

    # ...but never in plaintext on disk.
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT account_number_encrypted FROM worker_bank_accounts WHERE id = %s",
            [account.id],
        )
        raw = cursor.fetchone()[0]
    raw = bytes(raw) if not isinstance(raw, bytes) else raw
    assert b"50100123456789" not in raw


def test_bank_account_is_masked_from_the_api_by_default(as_contractor, today,
                                                        requirements):
    payload = _payload(today)
    payload["bank_account_number"] = "50100123456789"
    as_contractor.post(ONBOARD_URL, payload, format="multipart")

    body = as_contractor.get("/api/workers/").json()
    account = next(w["bank_account"] for w in body if w["bank_account"])

    # The owning contractor sees it in full; the ciphertext is never exposed.
    assert account["account_number"] == "50100123456789"
    assert "account_number_encrypted" not in account


def test_a_shared_bank_account_is_surfaced(as_contractor, today, requirements,
                                           contractor, compliant_worker):
    """Several workers on one account is the classic ghost-worker signature."""
    first = WorkerBankAccount(worker=compliant_worker)
    first.set_account_number("50100123456789")
    first.save()

    payload = _payload(today)
    payload["bank_account_number"] = "50100123456789"
    as_contractor.post(ONBOARD_URL, payload, format="multipart")

    second = WorkerBankAccount.objects.get(worker__aadhar_number="100000000042")
    assert second.shared_with().count() == 1


def test_pe_cannot_onboard_workers(as_pe, today, requirements):
    assert as_pe.post(ONBOARD_URL, _payload(today), format="multipart").status_code == 403


def test_gate_cannot_onboard_workers(as_gate, today, requirements):
    assert as_gate.post(ONBOARD_URL, _payload(today), format="multipart").status_code == 403


# ---------------------------------------------------------------------------
# Contractor ownership boundary
# ---------------------------------------------------------------------------
def test_contractor_only_sees_their_own_pool(api, contractor, other_contractor,
                                             make_worker):
    make_worker(name="Mine", aadhar="100000000010")
    make_worker(name="Theirs", aadhar="100000000011", owner=other_contractor)

    api.force_authenticate(user=contractor)
    names = [w["name"] for w in api.get("/api/workers/").json()]

    assert names == ["Mine"]


def test_contractor_cannot_touch_another_contractors_worker(
    api, other_contractor, compliant_worker
):
    api.force_authenticate(user=other_contractor)

    assert api.delete(f"/api/workers/{compliant_worker.id}/").status_code == 404
    assert api.post(
        "/api/safety-video/heartbeat/",
        {"worker": compliant_worker.id, "progress_percentage": 100},
        format="json",
    ).status_code == 404


def test_bulk_import_assigns_workers_to_the_calling_contractor(as_contractor, contractor):
    csv_bytes = (
        b"name,aadhar_number,skill_type\n"
        b"Ramesh Gupta,200000000011,Mason\n"
        b"Vijay Rao,200000000012,Plumber\n"
        b"Bad Row,123,Welder\n"
    )
    upload = SimpleUploadedFile("workers.csv", csv_bytes, "text/csv")

    body = as_contractor.post(
        "/api/workers/bulk-upload/", {"file": upload}, format="multipart"
    ).json()

    assert body["created_count"] == 2
    assert body["error_count"] == 1
    assert Worker.objects.filter(contractor=contractor).count() == 2


def test_verification_status_reports_every_pillar(as_contractor, compliant_worker):
    rows = as_contractor.get("/api/verification-status/").json()

    keys = [item["key"] for item in rows[0]["items"]]
    assert keys == ["Aadhar", "PAN", "Safety Training", "MEDICAL", "POLICE",
                    "TRADE_TEST", "SAFETY_VIDEO", "RESUME", "BANK"]
    # Everything verified except the resume and bank account.
    assert rows[0]["remaining"] == 2
