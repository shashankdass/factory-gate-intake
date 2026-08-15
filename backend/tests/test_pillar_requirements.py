"""Intake pillars as waivable requirements.

The contractor could waive an Aadhaar card but not a trade test: the compliance
bar lived in two places, and the more serious half of it was unreachable. These
tests pin the unified behaviour — and the fallback that stops an unconfigured
project from silently enforcing nothing.
"""
import pytest

from intake.models import ProjectRequirement, RequirementMaster

pytestmark = pytest.mark.django_db


def url(project):
    return f"/api/projects/{project.id}/requirements/"


@pytest.fixture
def configured(project, pillars):
    """The project with all four blocking pillars attached, resume advisory."""
    for code, requirement in pillars.items():
        ProjectRequirement.objects.create(
            project=project, requirement=requirement,
            is_mandatory=code != "RESUME",
        )
    return project


# ---------------------------------------------------------------------------
# The thing that was asked for
# ---------------------------------------------------------------------------
def test_waiving_the_trade_test_makes_an_untested_worker_deployable(
    as_contractor, configured, requirements, pillars, make_worker
):
    worker = make_worker(name="Untested", aadhar="100000000061")
    worker.trade_test_status = "PENDING"
    worker.save(update_fields=["trade_test_status"])
    assert worker.compliance_against_project(configured)["is_compliant"] is False

    keep = [r.id for r in requirements.values()] + [
        pillars[c].id for c in ("MEDICAL", "POLICE", "SAFETY_VIDEO")
    ]
    as_contractor.put(url(configured), {"requirement_ids": keep}, format="json")
    configured.refresh_from_db()

    assert worker.compliance_against_project(configured)["is_compliant"] is True


def test_waiving_everything_leaves_nothing_blocking(
    as_contractor, configured, make_worker
):
    """The screenshot case: no required documents *and* no pillars means every
    worker in the pool is deployable."""
    worker = make_worker(name="Bare", aadhar="100000000062")
    worker.trade_test_status = "PENDING"
    worker.save(update_fields=["trade_test_status"])
    worker.medical_records.all().delete()
    worker.police_verifications.all().delete()

    as_contractor.put(url(configured), {"requirement_ids": []}, format="json")
    configured.refresh_from_db()

    compliance = worker.compliance_against_project(configured)
    assert compliance["is_compliant"] is True
    assert compliance["gaps"] == []


def test_a_waived_pillar_is_still_reported_as_an_advisory(
    as_contractor, configured, make_worker
):
    """Waived is not unasked. The contractor chose not to gate on it and can
    still see where the worker stands."""
    worker = make_worker(name="Untested", aadhar="100000000063")
    worker.trade_test_status = "PENDING"
    worker.save(update_fields=["trade_test_status"])

    as_contractor.put(url(configured), {"requirement_ids": []}, format="json")
    configured.refresh_from_db()

    compliance = worker.compliance_against_project(configured)
    assert compliance["is_compliant"] is True
    assert any(a["pillar"] == "TRADE_TEST" for a in compliance["advisories"])


def test_a_pillar_can_be_added_back(as_contractor, configured, pillars, make_worker):
    worker = make_worker(name="Untested", aadhar="100000000064")
    worker.trade_test_status = "PENDING"
    worker.save(update_fields=["trade_test_status"])
    as_contractor.put(url(configured), {"requirement_ids": []}, format="json")

    as_contractor.put(
        url(configured),
        {"requirement_ids": [pillars["TRADE_TEST"].id]},
        format="json",
    )
    configured.refresh_from_db()

    assert worker.compliance_against_project(configured)["is_compliant"] is False


def test_waiving_a_pillar_is_attributed_like_any_other_change(
    as_contractor, configured, pillars, contractor
):
    as_contractor.put(url(configured), {"requirement_ids": []}, format="json")

    waived = ProjectRequirement.objects.get(
        project=configured, requirement=pillars["TRADE_TEST"]
    )
    assert waived.is_mandatory is False
    assert waived.updated_by == contractor


# ---------------------------------------------------------------------------
# The fallback: an unconfigured project must not become a hole
# ---------------------------------------------------------------------------
def test_a_project_with_no_pillar_rows_still_enforces_the_defaults(
    project, make_worker
):
    """Projects that predate the pillar rows must not silently pass everyone."""
    assert not project.project_requirements.filter(
        requirement__kind=RequirementMaster.Kind.PILLAR
    ).exists()
    worker = make_worker(name="Untested", aadhar="100000000065")
    worker.trade_test_status = "PENDING"
    worker.save(update_fields=["trade_test_status"])

    compliance = worker.compliance_against_project(project)

    assert compliance["is_compliant"] is False
    assert any(g.get("pillar") == "TRADE_TEST" for g in compliance["gaps"])


def test_clearing_documents_alone_does_not_clear_the_pillars(
    as_contractor, project, make_worker
):
    """The behaviour that prompted all this: an unconfigured project keeps its
    pillars even when every document is waived."""
    worker = make_worker(name="Untested", aadhar="100000000066")
    worker.trade_test_status = "PENDING"
    worker.save(update_fields=["trade_test_status"])

    as_contractor.put(url(project), {"requirement_ids": []}, format="json")
    project.refresh_from_db()

    assert worker.compliance_against_project(project)["is_compliant"] is False


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------
def test_the_catalogue_marks_which_entries_are_pillars(as_contractor, requirements,
                                                       pillars):
    entries = as_contractor.get("/api/requirements/").json()
    by_name = {e["name"]: e for e in entries}

    assert by_name["Aadhar"]["kind"] == "DOCUMENT"
    assert by_name["Trade Test"]["kind"] == "PILLAR"
    assert by_name["Trade Test"]["pillar_code"] == "TRADE_TEST"


def test_a_pillar_cannot_be_given_a_document(as_contractor, pillars, compliant_worker):
    """Pillars are states the platform tracks, not slots a file goes in."""
    response = as_contractor.post(
        "/api/documents/upload/",
        {"worker": compliant_worker.id, "requirement": pillars["TRADE_TEST"].id},
        format="multipart",
    )

    assert response.status_code == 400
    assert "not a document" in response.json()["detail"]
