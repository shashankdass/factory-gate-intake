"""Manual skill-count requirement filtering.

The contractor types their immediate needs ("3 Carpenters, 4 Masons") and the
app answers, per line, against their own labour pool: who is deployable now, who
could be with paperwork fixes, and how many bodies short they are.
"""
import pytest

from intake.models import CandidateProfile, CandidateSkill, Skill, Worker

pytestmark = pytest.mark.django_db

DEMAND_URL = "/api/workforce-demand/"


@pytest.fixture
def pool(make_worker, requirements, today):
    """Two ready carpenters, one carpenter needing fixes, one ready mason."""
    return {
        "ready_carpenter_a": make_worker("Ravi Kumar", "100000000001", "Carpenter"),
        "ready_carpenter_b": make_worker("Mahesh Patil", "100000000004", "Carpenter"),
        "broken_carpenter": make_worker(
            "Deepak Singh", "100000000005", "Carpenter", compliant=False
        ),
        "ready_mason": make_worker("Suresh Yadav", "100000000002", "Mason"),
    }


def test_demand_line_reports_availability_and_shortfall(as_contractor, pool, project):
    body = as_contractor.post(
        DEMAND_URL,
        {"demands": [{"skill": "Carpenter", "count": 3}], "project": project.id},
        format="json",
    ).json()

    line = body["lines"][0]
    assert line["skill"] == "Carpenter"
    assert line["required"] == 3
    assert line["available"] == 2      # two fully compliant
    assert line["shortfall"] == 1
    assert line["fixable"] == 1        # the third needs paperwork, not hiring


def test_multiple_demand_lines_are_answered_independently(as_contractor, pool, project):
    body = as_contractor.post(
        DEMAND_URL,
        {
            "demands": [
                {"skill": "Carpenter", "count": 3},
                {"skill": "Mason", "count": 4},
            ],
            "project": project.id,
        },
        format="json",
    ).json()

    by_skill = {line["skill"]: line for line in body["lines"]}
    assert by_skill["Carpenter"]["available"] == 2
    assert by_skill["Mason"]["available"] == 1
    assert by_skill["Mason"]["shortfall"] == 3
    assert body["summary"]["total_required"] == 7
    assert body["summary"]["total_shortfall"] == 4


def test_demand_with_no_project_falls_back_to_pillar_only_compliance(
    as_contractor, pool
):
    body = as_contractor.post(
        DEMAND_URL, {"demands": [{"skill": "Carpenter", "count": 2}]}, format="json"
    ).json()

    assert body["project"] is None
    assert body["lines"][0]["available"] == 2


def test_matching_is_case_insensitive_and_partial(as_contractor, pool, project):
    body = as_contractor.post(
        DEMAND_URL, {"demands": [{"skill": "carpen", "count": 1}]}, format="json"
    ).json()

    assert body["lines"][0]["available"] == 2


def test_resume_skills_also_match(as_contractor, pool, contractor, project):
    """A worker registered as "Helper" whose CV says Welder still matches Welder."""
    helper = Worker.objects.create(
        name="Vijay Rao", aadhar_number="100000000077", skill_type="Helper",
        contractor=contractor,
    )
    profile = CandidateProfile.objects.create(worker=helper, contractor=contractor)
    CandidateSkill.objects.create(
        profile=profile, skill=Skill.get_or_create_normalised("Welder")
    )

    body = as_contractor.post(
        DEMAND_URL, {"demands": [{"skill": "Welder", "count": 1}]}, format="json"
    ).json()

    line = body["lines"][0]
    # Matched, but not deployable — the helper has no documents or pillars.
    assert line["available"] == 0
    assert line["fixable"] == 1


def test_unmatched_skill_reports_a_full_shortfall(as_contractor, pool, project):
    body = as_contractor.post(
        DEMAND_URL, {"demands": [{"skill": "Crane Operator", "count": 5}]}, format="json"
    ).json()

    line = body["lines"][0]
    assert line["available"] == 0
    assert line["shortfall"] == 5
    assert line["ready_workers"] == []


def test_search_is_scoped_to_the_callers_own_pool(api, other_contractor, pool):
    api.force_authenticate(user=other_contractor)

    body = api.post(
        DEMAND_URL, {"demands": [{"skill": "Carpenter", "count": 3}]}, format="json"
    ).json()

    assert body["summary"]["pool_size"] == 0
    assert body["lines"][0]["shortfall"] == 3


def test_empty_demands_is_rejected(as_contractor):
    assert as_contractor.post(DEMAND_URL, {"demands": []}, format="json").status_code == 400


def test_pe_cannot_search_a_contractors_pool(as_pe):
    response = as_pe.post(
        DEMAND_URL, {"demands": [{"skill": "Carpenter", "count": 1}]}, format="json"
    )

    assert response.status_code == 403
