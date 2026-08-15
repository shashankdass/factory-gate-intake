"""Put the five intake pillars in the requirement catalogue.

They were hard-coded and always blocking. Making them catalogue entries lets a
contractor waive them the same way they waive a document — but the migration
must not change any project's behaviour on the way through, so every existing
project gets rows that reproduce exactly what was enforced before: medical,
police, trade test and safety video blocking; resume advisory unless
REQUIRE_RESUME_FOR_COMPLIANCE was on.
"""
from django.conf import settings
from django.db import migrations

PILLARS = [
    ("Medical Exam", "MEDICAL",
     "Medical fitness examination, valid one year from the exam date."),
    ("Police Verification", "POLICE",
     "Police verification certificate, valid one year from issue."),
    ("Trade Test", "TRADE_TEST",
     "Practical trade test administered by the contractor."),
    ("Safety Training Video", "SAFETY_VIDEO",
     "Mandatory safety induction video, watched to completion."),
    ("Resume on file", "RESUME",
     "A scanned resume parsed into a searchable candidate profile."),
]


def seed(apps, schema_editor):
    RequirementMaster = apps.get_model("intake", "RequirementMaster")
    Project = apps.get_model("intake", "Project")
    ProjectRequirement = apps.get_model("intake", "ProjectRequirement")

    resume_blocked = getattr(settings, "REQUIRE_RESUME_FOR_COMPLIANCE", False)

    created = {}
    for name, code, description in PILLARS:
        requirement, _ = RequirementMaster.objects.get_or_create(
            name=name,
            defaults={
                "description": description,
                "kind": "PILLAR",
                "pillar_code": code,
                "is_expirable": False,
            },
        )
        # get_or_create matched an existing row by name: make sure it is
        # actually flagged as a pillar rather than left looking like a document.
        if requirement.kind != "PILLAR" or requirement.pillar_code != code:
            requirement.kind = "PILLAR"
            requirement.pillar_code = code
            requirement.save(update_fields=["kind", "pillar_code"])
        created[code] = requirement

    for project in Project.objects.all():
        for code, requirement in created.items():
            ProjectRequirement.objects.get_or_create(
                project=project,
                requirement=requirement,
                defaults={
                    # Exactly what was enforced before this migration ran.
                    "is_mandatory": True if code != "RESUME" else resume_blocked,
                },
            )


def unseed(apps, schema_editor):
    RequirementMaster = apps.get_model("intake", "RequirementMaster")
    RequirementMaster.objects.filter(kind="PILLAR").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("intake", "0011_requirementmaster_kind_requirementmaster_pillar_code"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
