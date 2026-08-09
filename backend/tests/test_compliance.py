"""The compliance engine — the single source of truth.

Every gap classification the UI renders and every deploy/deny decision the
platform makes comes out of ``Worker.compliance_against_project``, so this file
walks a worker through the whole lifecycle: compliant → each failure mode →
repaired.
"""
from datetime import timedelta

import pytest

from intake.models import (
    IntakeMedicalRecord,
    IntakePoliceVerification,
    SafetyTrainingProgress,
    Worker,
    WorkerDocument,
)

pytestmark = pytest.mark.django_db


def _gap(compliance, requirement_name):
    return next(
        (g for g in compliance["gaps"] if g["requirement_name"] == requirement_name),
        None,
    )


def test_fully_documented_worker_is_compliant(compliant_worker, project):
    compliance = compliant_worker.compliance_against_project(project)

    assert compliance["is_compliant"] is True
    assert compliance["gaps"] == []
    # Three documents + four passing pillars.
    assert len(compliance["satisfied"]) == 7


def test_missing_document_is_reported_as_missing(compliant_worker, project, requirements):
    compliant_worker.documents.filter(requirement=requirements["PAN"]).delete()

    gap = _gap(compliant_worker.compliance_against_project(project), "PAN")

    assert gap["reason"] == "MISSING"
    assert gap["document_id"] is None
    assert gap["kind"] == "document"


def test_expired_document_is_reported_as_expired(compliant_worker, project,
                                                 requirements, today):
    doc = compliant_worker.documents.get(requirement=requirements["Safety Training"])
    doc.expiry_date = today - timedelta(days=1)
    doc.save()

    gap = _gap(compliant_worker.compliance_against_project(project), "Safety Training")

    assert gap["reason"] == "EXPIRED"
    assert gap["is_expirable"] is True
    assert gap["expiry_date"] == (today - timedelta(days=1)).isoformat()


def test_document_expiring_today_still_counts(compliant_worker, project,
                                              requirements, today):
    """The boundary is `expiry_date < today` — a pass valid *through* today works."""
    doc = compliant_worker.documents.get(requirement=requirements["Safety Training"])
    doc.expiry_date = today
    doc.save()

    assert compliant_worker.compliance_against_project(project)["is_compliant"] is True


def test_rejected_document_surfaces_the_reason(compliant_worker, project, requirements):
    doc = compliant_worker.documents.get(requirement=requirements["PAN"])
    doc.verification_status = WorkerDocument.Status.REJECTED
    doc.rejection_reason = "Blurred / unreadable scan."
    doc.save()

    gap = _gap(compliant_worker.compliance_against_project(project), "PAN")

    assert gap["reason"] == "REJECTED"
    assert gap["rejection_reason"] == "Blurred / unreadable scan."


def test_pending_document_does_not_count(compliant_worker, project, requirements):
    doc = compliant_worker.documents.get(requirement=requirements["Aadhar"])
    doc.verification_status = WorkerDocument.Status.PENDING
    doc.save()

    assert _gap(compliant_worker.compliance_against_project(project), "Aadhar")["reason"] == "PENDING"


def test_a_valid_document_wins_over_a_rejected_one(compliant_worker, project,
                                                   requirements):
    """A re-upload should clear the slot even though the rejected row remains."""
    WorkerDocument.objects.create(
        worker=compliant_worker, requirement=requirements["PAN"],
        verification_status=WorkerDocument.Status.REJECTED,
        rejection_reason="Old rejected scan",
    )

    assert compliant_worker.compliance_against_project(project)["is_compliant"] is True


@pytest.mark.parametrize("flag", ["color_blindness", "vertigo"])
def test_medical_flags_fail_the_pillar(compliant_worker, project, flag):
    record = compliant_worker.medical_records.first()
    setattr(record, flag, True)
    record.save()

    gap = _gap(compliant_worker.compliance_against_project(project), "Medical Exam")

    assert gap["reason"] == "FAILED"
    assert flag.replace("_", " ") in gap["detail"]


def test_expired_medical_fails(compliant_worker, project, today):
    record = compliant_worker.medical_records.first()
    record.exam_date = today - timedelta(days=400)
    record.save()  # expiry recomputed to exam_date + 365

    gap = _gap(compliant_worker.compliance_against_project(project), "Medical Exam")
    assert gap["reason"] == "EXPIRED"


def test_medical_expiry_is_always_derived(make_worker, today):
    """expiry_date is never client-set — the model recomputes it on every save."""
    worker = make_worker(compliant=False, aadhar="100000000099")
    record = IntakeMedicalRecord.objects.create(
        worker=worker, exam_date=today - timedelta(days=10),
        expiry_date=today + timedelta(days=9999),  # deliberately absurd
    )
    assert record.expiry_date == today - timedelta(days=10) + timedelta(days=365)


def test_expired_pvc_fails(compliant_worker, project, today):
    pvc = compliant_worker.police_verifications.first()
    pvc.issue_date = today - timedelta(days=400)
    pvc.save()

    assert _gap(compliant_worker.compliance_against_project(project),
                "Police Verification")["reason"] == "EXPIRED"


def test_unpassed_trade_test_blocks(compliant_worker, project):
    compliant_worker.trade_test_status = Worker.TradeTestStatus.PENDING
    compliant_worker.save()

    assert _gap(compliant_worker.compliance_against_project(project),
                "Trade Test")["reason"] == "NOT_PASSED"


def test_locked_trade_test_blocks(compliant_worker, project):
    compliant_worker.trade_test_status = Worker.TradeTestStatus.FAILED
    compliant_worker.save()

    gap = _gap(compliant_worker.compliance_against_project(project), "Trade Test")
    assert gap["reason"] == "FAILED"
    assert "locked" in gap["detail"]


def test_incomplete_safety_video_blocks(compliant_worker, project):
    progress = compliant_worker.safety_video
    progress.progress_percentage = 60
    progress.is_completed = False
    progress.save()

    gap = _gap(compliant_worker.compliance_against_project(project),
               "Safety Training Video")
    assert gap["reason"] == "INCOMPLETE"
    assert "60%" in gap["detail"]


def test_missing_resume_is_advisory_not_blocking(compliant_worker, project):
    """Resume is reported but does not block while REQUIRE_RESUME is off."""
    compliance = compliant_worker.compliance_against_project(project)

    assert compliance["is_compliant"] is True
    assert [a["pillar"] for a in compliance["advisories"]] == ["RESUME"]


def test_missing_resume_blocks_when_required(compliant_worker, project, settings):
    settings.REQUIRE_RESUME_FOR_COMPLIANCE = True

    compliance = compliant_worker.compliance_against_project(project)

    assert compliance["is_compliant"] is False
    assert _gap(compliance, "Resume on file")["reason"] == "MISSING"


def test_repairing_every_gap_restores_compliance(make_worker, project, requirements, today):
    """The full lifecycle: a worker with nothing, fixed one pillar at a time."""
    worker = make_worker(name="Deepak Singh", aadhar="100000000005", compliant=False)
    recent = today - timedelta(days=30)

    assert worker.compliance_against_project(project)["is_compliant"] is False

    for name in ("Aadhar", "PAN", "Safety Training"):
        WorkerDocument.objects.create(
            worker=worker, requirement=requirements[name],
            verification_status=WorkerDocument.Status.VERIFIED,
            expiry_date=today + timedelta(days=90) if name == "Safety Training" else None,
        )
    IntakeMedicalRecord.objects.create(worker=worker, exam_date=recent)
    IntakePoliceVerification.objects.create(
        worker=worker, issue_date=recent,
        verification_status=WorkerDocument.Status.VERIFIED,
    )
    worker.trade_test_status = Worker.TradeTestStatus.PASSED
    worker.save()
    SafetyTrainingProgress.objects.create(
        worker=worker, progress_percentage=100, is_completed=True
    )

    worker.refresh_from_db()
    assert worker.compliance_against_project(project)["is_compliant"] is True


def test_snapshot_without_project_checks_pillars_only(compliant_worker, requirements):
    """Workforce-demand search runs before a project is chosen."""
    compliant_worker.documents.all().delete()  # documents are project-scoped

    assert compliant_worker.compliance_snapshot(None)["is_compliant"] is True
