"""Shared fixtures.

Hermeticity is pinned in ``core/settings_test.py`` (loaded by pytest-django
before any conftest runs): in-memory SQLite, the local signed-storage backend,
and the canned resume parser. No network, no keys, no external services.

``OCR_PROVIDER`` is the one knob still read from the environment at call time,
so it is set here.
"""
import os
from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from intake.models import (
    IntakeList,
    IntakeListWorker,
    IntakeMedicalRecord,
    IntakePoliceVerification,
    Project,
    ProjectRequirement,
    RequirementMaster,
    SafetyTrainingProgress,
    TradeTestAttempt,
    User,
    Worker,
    WorkerDocument,
)

os.environ["OCR_PROVIDER"] = "mock"


@pytest.fixture
def today():
    return timezone.now().date()


@pytest.fixture
def pe(db):
    return User.objects.create_user(
        username="pe@test.com", email="pe@test.com", password="pw",
        role=User.Role.PRINCIPAL_EMPLOYER, organization="Factory HQ",
    )


@pytest.fixture
def contractor(db):
    return User.objects.create_user(
        username="c1@test.com", email="c1@test.com", password="pw",
        role=User.Role.CONTRACTOR, organization="Vendor Co.",
    )


@pytest.fixture
def other_contractor(db):
    return User.objects.create_user(
        username="c2@test.com", email="c2@test.com", password="pw",
        role=User.Role.CONTRACTOR, organization="Rival Vendor",
    )


@pytest.fixture
def gate(db):
    return User.objects.create_user(
        username="gate@test.com", email="gate@test.com", password="pw",
        role=User.Role.GATE_SECURITY, organization="Factory HQ",
    )


@pytest.fixture
def requirements(db):
    specs = [("Aadhar", False), ("PAN", False), ("Safety Training", True)]
    return {
        name: RequirementMaster.objects.create(name=name, is_expirable=expirable)
        for name, expirable in specs
    }


@pytest.fixture
def project(db, pe, contractor, requirements):
    project = Project.objects.create(name="Plant-A Turnaround", principal_employer=pe)
    project.contractors.add(contractor)
    for requirement in requirements.values():
        ProjectRequirement.objects.create(
            project=project, requirement=requirement, is_mandatory=True
        )
    return project


def _make_compliant(worker, requirements, today):
    """Give a worker a full, currently-valid document + pillar set."""
    recent = today - timedelta(days=30)
    future = today + timedelta(days=180)

    WorkerDocument.objects.create(
        worker=worker, requirement=requirements["Aadhar"],
        verification_status=WorkerDocument.Status.VERIFIED,
    )
    WorkerDocument.objects.create(
        worker=worker, requirement=requirements["PAN"],
        verification_status=WorkerDocument.Status.VERIFIED,
    )
    WorkerDocument.objects.create(
        worker=worker, requirement=requirements["Safety Training"],
        verification_status=WorkerDocument.Status.VERIFIED, expiry_date=future,
    )
    IntakeMedicalRecord.objects.create(worker=worker, exam_date=recent, vision="6/6")
    IntakePoliceVerification.objects.create(
        worker=worker, issue_date=recent, certificate_number="PVC-1",
        verification_status=WorkerDocument.Status.VERIFIED,
    )
    TradeTestAttempt.objects.create(worker=worker, attempt_number=1, score=4, is_passed=True)
    worker.trade_test_status = Worker.TradeTestStatus.PASSED
    worker.save(update_fields=["trade_test_status"])
    SafetyTrainingProgress.objects.create(
        worker=worker, progress_percentage=100, is_completed=True
    )
    return worker


@pytest.fixture
def make_worker(db, contractor, requirements, today):
    """Factory: ``make_worker(name, aadhar, skill, compliant=True)``."""

    def _make(name="Ravi Kumar", aadhar="100000000001", skill="Carpenter",
              compliant=True, owner=None):
        worker = Worker.objects.create(
            name=name, aadhar_number=aadhar, skill_type=skill,
            contractor=owner or contractor,
        )
        if compliant:
            _make_compliant(worker, requirements, today)
        return worker

    return _make


@pytest.fixture
def compliant_worker(make_worker):
    return make_worker()


@pytest.fixture
def approved_list(db, project, contractor, compliant_worker):
    """A worker who has been through the full approval flow."""
    intake_list = IntakeList.objects.create(
        project=project, contractor=contractor,
        status=IntakeList.Status.APPROVED,
        submitted_at=timezone.now(), reviewed_at=timezone.now(),
    )
    IntakeListWorker.objects.create(intake_list=intake_list, worker=compliant_worker)
    return intake_list


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def as_contractor(api, contractor):
    api.force_authenticate(user=contractor)
    return api


@pytest.fixture
def as_pe(api, pe):
    api.force_authenticate(user=pe)
    return api


@pytest.fixture
def as_gate(api, gate):
    api.force_authenticate(user=gate)
    return api
