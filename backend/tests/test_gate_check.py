"""Real-time gate compliance.

The gap this closes: an approved intake list is a *snapshot*. Documents keep
expiring after the PE signs off, so trusting the snapshot waves through a worker
whose medical or PVC died last week. Every test here checks that the gate
re-evaluates at scan time rather than trusting the approval.
"""
from datetime import timedelta

import pytest

from intake.models import IntakeList, Worker

pytestmark = pytest.mark.django_db

GATE_URL = "/api/gate-check/"


def _check(client, aadhar):
    return client.get(GATE_URL, {"aadhar": aadhar}).json()


def test_approved_and_currently_valid_is_granted(as_gate, approved_list, compliant_worker):
    body = _check(as_gate, compliant_worker.aadhar_number)

    assert body["access"] == "GRANTED"
    assert body["reason_code"] == "COMPLIANT"
    assert body["project"] == approved_list.project.name
    assert body["worker"]["name"] == compliant_worker.name


def test_unknown_aadhar_is_denied(as_gate):
    body = _check(as_gate, "999999999999")

    assert body["access"] == "DENIED"
    assert body["reason_code"] == "UNKNOWN_WORKER"
    assert body["worker"] is None


def test_worker_not_on_any_approved_list_is_denied(as_gate, compliant_worker):
    body = _check(as_gate, compliant_worker.aadhar_number)

    assert body["access"] == "DENIED"
    assert body["reason_code"] == "NOT_APPROVED"


def test_medical_that_expired_after_approval_flips_to_red(
    as_gate, approved_list, compliant_worker, today
):
    """The headline case: approved last month, medical lapsed since."""
    record = compliant_worker.medical_records.first()
    record.exam_date = today - timedelta(days=400)
    record.save()

    body = _check(as_gate, compliant_worker.aadhar_number)

    assert body["access"] == "DENIED"
    assert body["reason_code"] == "DOCUMENT_EXPIRED"
    assert "Medical Exam" in body["reason"]
    # The approval itself is untouched — it is the live check that failed.
    assert approved_list.status == IntakeList.Status.APPROVED


def test_pvc_that_expired_after_approval_flips_to_red(
    as_gate, approved_list, compliant_worker, today
):
    pvc = compliant_worker.police_verifications.first()
    pvc.issue_date = today - timedelta(days=400)
    pvc.save()

    body = _check(as_gate, compliant_worker.aadhar_number)

    assert body["access"] == "DENIED"
    assert body["reason_code"] == "DOCUMENT_EXPIRED"
    assert "Police Verification" in body["reason"]


def test_expired_safety_certificate_flips_to_red(
    as_gate, approved_list, compliant_worker, requirements, today
):
    doc = compliant_worker.documents.get(requirement=requirements["Safety Training"])
    doc.expiry_date = today - timedelta(days=1)
    doc.save()

    body = _check(as_gate, compliant_worker.aadhar_number)

    assert body["reason_code"] == "DOCUMENT_EXPIRED"
    assert "Safety Training" in body["reason"]


def test_regressed_pillar_flips_to_red(as_gate, approved_list, compliant_worker):
    """Not an expiry — a pillar that went backwards after approval."""
    compliant_worker.trade_test_status = Worker.TradeTestStatus.FAILED
    compliant_worker.save()

    body = _check(as_gate, compliant_worker.aadhar_number)

    assert body["access"] == "DENIED"
    assert body["reason_code"] == "COMPLIANCE_REGRESSED"
    assert "Trade Test" in body["reason"]


def test_revoked_document_flips_to_red(
    as_gate, approved_list, compliant_worker, requirements
):
    compliant_worker.documents.filter(requirement=requirements["PAN"]).delete()

    body = _check(as_gate, compliant_worker.aadhar_number)

    assert body["access"] == "DENIED"
    assert "PAN" in body["reason"]


def test_denied_payload_carries_the_full_compliance_detail(
    as_gate, approved_list, compliant_worker, today
):
    """The guard needs to see *why*, not just RED."""
    record = compliant_worker.medical_records.first()
    record.exam_date = today - timedelta(days=400)
    record.save()

    body = _check(as_gate, compliant_worker.aadhar_number)

    assert body["compliance"]["is_compliant"] is False
    assert any(g["reason"] == "EXPIRED" for g in body["compliance"]["gaps"])
    assert body["checked_at"]


def test_gate_check_requires_an_aadhar(as_gate):
    assert as_gate.get(GATE_URL).status_code == 400


# ---------------------------------------------------------------------------
# The worker photo — the one check the system cannot make for the guard
# ---------------------------------------------------------------------------
def test_the_photo_is_sent_so_the_guard_can_check_the_face(
    as_gate, approved_list, compliant_worker
):
    compliant_worker.photo_key = "gate-intake/worker_photos/abc.jpg"
    compliant_worker.save(update_fields=["photo_key"])

    body = as_gate.get(
        f"/api/gate-check/?aadhar={compliant_worker.aadhar_number}"
    ).json()

    assert body["access"] == "GRANTED"
    assert body["worker"]["photo_url"]


def test_the_photo_is_sent_on_a_denial_too(as_gate, compliant_worker):
    """The guard may still need to identify who they are turning away."""
    compliant_worker.photo_key = "gate-intake/worker_photos/abc.jpg"
    compliant_worker.save(update_fields=["photo_key"])

    body = as_gate.get(
        f"/api/gate-check/?aadhar={compliant_worker.aadhar_number}"
    ).json()

    assert body["access"] == "DENIED"
    assert body["worker"]["photo_url"]


def test_a_worker_without_a_photo_is_still_granted_entry(
    as_gate, approved_list, compliant_worker
):
    """A missing photograph is not a reason to hold someone at the gate."""
    assert compliant_worker.photo_key == ""

    body = as_gate.get(
        f"/api/gate-check/?aadhar={compliant_worker.aadhar_number}"
    ).json()

    assert body["access"] == "GRANTED"
    assert body["worker"]["photo_url"] is None
