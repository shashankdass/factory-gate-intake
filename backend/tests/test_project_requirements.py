"""Editing a project's mandatory document set.

The contractor owns this now, which means they can lower the bar their own
workers are measured against. The tests below pin both halves of that: the edit
works, and it is never anonymous.
"""
import pytest

from intake.models import ProjectRequirement

pytestmark = pytest.mark.django_db


def url(project):
    return f"/api/projects/{project.id}/requirements/"


def names(response):
    """Currently-required document names (waived rows stay in the payload)."""
    return sorted(
        r["requirement"]["name"]
        for r in response.json()["requirements"]
        if r["is_mandatory"]
    )


def test_contractor_can_remove_a_requirement(as_contractor, project, requirements):
    keep = [requirements["Aadhar"].id, requirements["Safety Training"].id]

    response = as_contractor.put(url(project), {"requirement_ids": keep}, format="json")

    assert response.status_code == 200
    assert names(response) == ["Aadhar", "Safety Training"]
    assert project.project_requirements.filter(is_mandatory=True).count() == 2
    # The waived requirement is still on the record.
    assert project.project_requirements.count() == 3


def test_contractor_can_add_a_requirement_back(as_contractor, project, requirements):
    as_contractor.put(
        url(project), {"requirement_ids": [requirements["Aadhar"].id]}, format="json"
    )

    response = as_contractor.put(
        url(project),
        {"requirement_ids": [requirements["Aadhar"].id, requirements["PAN"].id]},
        format="json",
    )

    assert names(response) == ["Aadhar", "PAN"]


def test_a_removal_is_recorded_rather_than_erased(as_contractor, project,
                                                  requirements, contractor):
    """Removal lowers the bar, so it is the change that must leave a trace."""
    as_contractor.put(
        url(project),
        {"requirement_ids": [requirements["Aadhar"].id,
                             requirements["Safety Training"].id]},
        format="json",
    )

    waived = ProjectRequirement.objects.get(
        project=project, requirement=requirements["PAN"]
    )
    # The row survives, flagged as no longer required and attributed.
    assert waived.is_mandatory is False
    assert waived.updated_by == contractor
    assert waived.updated_at is not None


def test_the_pe_sees_who_moved_the_bar(as_contractor, as_pe, project, requirements,
                                       contractor):
    as_contractor.put(
        url(project),
        {"requirement_ids": [requirements["Aadhar"].id,
                             requirements["Safety Training"].id]},
        format="json",
    )

    entries = as_pe.get("/api/projects/").json()[0]["requirements"]
    waived = [e for e in entries if not e["is_mandatory"]]

    assert [e["requirement"]["name"] for e in waived] == ["PAN"]
    assert waived[0]["updated_by_email"] == contractor.email


def test_removing_a_requirement_makes_a_blocked_worker_deployable(
    as_contractor, project, requirements, make_worker
):
    """The whole point, and the reason the change is attributed."""
    worker = make_worker(name="No PAN", aadhar="100000000077")
    worker.documents.filter(requirement=requirements["PAN"]).delete()
    assert worker.compliance_against_project(project)["is_compliant"] is False

    as_contractor.put(
        url(project),
        {"requirement_ids": [requirements["Aadhar"].id,
                             requirements["Safety Training"].id]},
        format="json",
    )

    project.refresh_from_db()
    assert worker.compliance_against_project(project)["is_compliant"] is True


def test_an_empty_set_is_allowed(as_contractor, project):
    """Clearing every document leaves the intake pillars still enforced."""
    response = as_contractor.put(url(project), {"requirement_ids": []}, format="json")

    assert response.status_code == 200
    assert names(response) == []


def test_intake_pillars_survive_an_empty_requirement_set(
    as_contractor, project, requirements, make_worker
):
    worker = make_worker(name="No Medical", aadhar="100000000078")
    worker.medical_records.all().delete()

    as_contractor.put(url(project), {"requirement_ids": []}, format="json")
    project.refresh_from_db()

    compliance = worker.compliance_against_project(project)
    assert compliance["is_compliant"] is False
    assert any(g["pillar"] == "MEDICAL" for g in compliance["gaps"])


def test_a_contractor_not_on_the_project_is_refused(api, other_contractor, project,
                                                    requirements):
    api.force_authenticate(user=other_contractor)

    response = api.put(url(project), {"requirement_ids": []}, format="json")

    assert response.status_code == 403
    assert project.project_requirements.filter(is_mandatory=True).count() == 3


def test_gate_security_cannot_change_requirements(as_gate, project):
    assert as_gate.put(url(project), {"requirement_ids": []}, format="json").status_code == 403


def test_unknown_requirement_ids_are_rejected(as_contractor, project, requirements):
    response = as_contractor.put(
        url(project), {"requirement_ids": [requirements["Aadhar"].id, 9999]}, format="json"
    )

    assert response.status_code == 400
    assert "9999" in response.json()["detail"]
    # Nothing was changed by the failed call.
    assert project.project_requirements.filter(is_mandatory=True).count() == 3


def test_a_non_list_payload_is_rejected(as_contractor, project):
    response = as_contractor.put(url(project), {"requirement_ids": "Aadhar"}, format="json")

    assert response.status_code == 400


def test_the_pe_can_still_edit_their_own_project(as_pe, project, requirements):
    response = as_pe.put(
        url(project), {"requirement_ids": [requirements["Aadhar"].id]}, format="json"
    )

    assert response.status_code == 200
    assert names(response) == ["Aadhar"]
