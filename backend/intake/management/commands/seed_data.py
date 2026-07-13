"""
Idempotent data-seed run on every deploy (see Procfile `release:` line).

Creates:
  * The 4 dummy persona accounts with exact credentials.
  * Base requirements: Aadhar, PAN, Safety Training.
  * Two sample projects with mandatory requirements.
  * A handful of workers pre-assigned to the contractor, some fully compliant and
    some deliberately missing / expired / rejected so every UI state is visible.

Safe to run repeatedly: everything uses get_or_create keyed on natural keys.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from intake.models import (
    IntakeList,
    IntakeListWorker,
    IntakeMedicalRecord,
    IntakePoliceVerification,
    IntakeVideoProgress,
    Project,
    ProjectRequirement,
    RequirementMaster,
    User,
    Worker,
    WorkerDocument,
)

# The exact dummy credentials the frontend role-switcher logs in with.
DUMMY_USERS = [
    {
        "email": "pe.admin@factory.com",
        "password": "pe_test_123",
        "role": User.Role.PRINCIPAL_EMPLOYER,
        "first_name": "Priya",
        "organization": "Factory HQ",
    },
    {
        "email": "contractor.one@vendor.com",
        "password": "contractor_test_123",
        "role": User.Role.CONTRACTOR,
        "first_name": "Contractor One",
        "organization": "Vendor Co.",
    },
    {
        "email": "field.officer@vendor.com",
        "password": "field_test_123",
        "role": User.Role.FIELD_OFFICER,
        "first_name": "Field Officer",
        "organization": "Vendor Co.",
    },
    {
        "email": "gate.security@factory.com",
        "password": "gate_test_123",
        "role": User.Role.GATE_SECURITY,
        "first_name": "Gate Security",
        "organization": "Factory HQ",
    },
]


class Command(BaseCommand):
    help = "Seed dummy persona accounts, requirements, projects and sample workers."

    @transaction.atomic
    def handle(self, *args, **options):
        users = self._seed_users()
        pe = users["pe.admin@factory.com"]
        contractor = users["contractor.one@vendor.com"]

        requirements = self._seed_requirements()
        projects = self._seed_projects(pe, contractor, requirements)
        self._seed_workers(contractor, requirements)
        self._seed_intake_pillars()

        self.stdout.write(self.style.SUCCESS("✔ Seed complete."))
        self.stdout.write("  Dummy logins:")
        for u in DUMMY_USERS:
            self.stdout.write(f"    - {u['role']:14} {u['email']} / {u['password']}")
        self.stdout.write(f"  Projects: {', '.join(p.name for p in projects)}")

    # -- users ---------------------------------------------------------------
    def _seed_users(self) -> dict[str, User]:
        created = {}
        for spec in DUMMY_USERS:
            user, is_new = User.objects.get_or_create(
                email=spec["email"],
                defaults={
                    "username": spec["email"],
                    "role": spec["role"],
                    "first_name": spec["first_name"],
                    "organization": spec["organization"],
                    "is_staff": spec["role"] == User.Role.PRINCIPAL_EMPLOYER,
                },
            )
            # Always (re)set the password so credentials stay exactly as documented.
            user.role = spec["role"]
            user.organization = spec["organization"]
            user.set_password(spec["password"])
            user.save()
            created[spec["email"]] = user
            self.stdout.write(
                ("＋ created " if is_new else "· exists  ") + spec["email"]
            )
        return created

    # -- requirements --------------------------------------------------------
    def _seed_requirements(self) -> dict[str, RequirementMaster]:
        specs = [
            ("Aadhar", "National identity card", False),
            ("PAN", "Permanent Account Number card", False),
            ("Safety Training", "Site safety certification", True),
        ]
        out = {}
        for name, desc, expirable in specs:
            req, _ = RequirementMaster.objects.get_or_create(
                name=name,
                defaults={"description": desc, "is_expirable": expirable},
            )
            out[name] = req
        return out

    # -- projects ------------------------------------------------------------
    def _seed_projects(self, pe, contractor, requirements) -> list[Project]:
        project_specs = [
            {
                "name": "Plant-A Turnaround 2026",
                "description": "Annual maintenance shutdown, Plant A.",
                "requirements": ["Aadhar", "PAN", "Safety Training"],
            },
            {
                "name": "Warehouse Expansion Phase-2",
                "description": "Civil works for the new warehouse block.",
                "requirements": ["Aadhar", "Safety Training"],
            },
        ]
        projects = []
        for spec in project_specs:
            project, _ = Project.objects.get_or_create(
                name=spec["name"],
                defaults={
                    "description": spec["description"],
                    "principal_employer": pe,
                },
            )
            project.contractors.add(contractor)
            for req_name in spec["requirements"]:
                ProjectRequirement.objects.get_or_create(
                    project=project,
                    requirement=requirements[req_name],
                    defaults={"is_mandatory": True},
                )
            projects.append(project)
        return projects

    # -- workers + documents -------------------------------------------------
    def _seed_workers(self, contractor, requirements) -> None:
        today = timezone.now().date()
        future = today + timedelta(days=180)
        past = today - timedelta(days=10)

        aadhar = requirements["Aadhar"]
        pan = requirements["PAN"]
        safety = requirements["Safety Training"]

        # Each tuple: (name, aadhar, skill, [ (requirement, status, expiry, rej) ])
        VERIFIED = WorkerDocument.Status.VERIFIED
        PENDING = WorkerDocument.Status.PENDING
        REJECTED = WorkerDocument.Status.REJECTED

        worker_specs = [
            # Fully compliant -> "Ready to Deploy"
            (
                "Ravi Kumar",
                "100000000001",
                "Carpenter",
                [
                    (aadhar, VERIFIED, None, ""),
                    (pan, VERIFIED, None, ""),
                    (safety, VERIFIED, future, ""),
                ],
            ),
            (
                "Suresh Yadav",
                "100000000002",
                "Welder",
                [
                    (aadhar, VERIFIED, None, ""),
                    (pan, VERIFIED, None, ""),
                    (safety, VERIFIED, future, ""),
                ],
            ),
            # Missing PAN entirely -> "Fix Requirements" (MISSING)
            (
                "Anil Sharma",
                "100000000003",
                "Electrician",
                [
                    (aadhar, VERIFIED, None, ""),
                    (safety, VERIFIED, future, ""),
                ],
            ),
            # Expired safety training -> "Fix Requirements" (EXPIRED)
            (
                "Mahesh Patil",
                "100000000004",
                "Carpenter",
                [
                    (aadhar, VERIFIED, None, ""),
                    (pan, VERIFIED, None, ""),
                    (safety, VERIFIED, past, ""),
                ],
            ),
            # Rejected PAN + pending safety -> "Fix Requirements" (REJECTED/PENDING)
            (
                "Deepak Singh",
                "100000000005",
                "Fitter",
                [
                    (aadhar, VERIFIED, None, ""),
                    (pan, REJECTED, None, "Blurred / unreadable scan."),
                    (safety, PENDING, future, ""),
                ],
            ),
        ]

        for name, aadhar_no, skill, docs in worker_specs:
            worker, _ = Worker.objects.get_or_create(
                aadhar_number=aadhar_no,
                defaults={
                    "name": name,
                    "skill_type": skill,
                    "contractor": contractor,
                },
            )
            # Ensure ownership even if the worker pre-existed.
            worker.contractor = contractor
            worker.save()

            for requirement, doc_status, expiry, rejection in docs:
                WorkerDocument.objects.get_or_create(
                    worker=worker,
                    requirement=requirement,
                    defaults={
                        "document_number": f"{requirement.name[:3].upper()}-{aadhar_no[-4:]}",
                        "verification_status": doc_status,
                        "expiry_date": expiry,
                        "rejection_reason": rejection,
                        "file_url": "https://example.com/sample-document.pdf",
                    },
                )

    # -- 5-pillar intake records --------------------------------------------
    def _seed_intake_pillars(self) -> None:
        """Seed medical / police / video pillars so each failure mode is visible.

        Ravi is fully compliant (Ready); everyone else fails at least one pillar.
        Idempotent: only creates records the worker doesn't already have.
        """
        today = timezone.now().date()
        recent = today - timedelta(days=30)  # well within the 1-year window
        stale = today - timedelta(days=400)  # already expired

        VERIFIED = WorkerDocument.Status.VERIFIED
        TRADE = IntakeVideoProgress.VideoType.TRADE_TEST
        SAFETY = IntakeVideoProgress.VideoType.SAFETY_TRAINING

        # aadhar -> (medical spec | None, police spec | None, [(video_type, pct)])
        plan = {
            # Ravi: everything passes -> Ready
            "100000000001": (
                {"exam_date": recent, "color_blindness": False, "vertigo": False,
                 "vision": "6/6", "blood_type": "O+"},
                {"issue_date": recent, "status": VERIFIED, "cert": "PVC-RAVI-01"},
                [(TRADE, 100), (SAFETY, 100)],
            ),
            # Suresh: trade-test video incomplete
            "100000000002": (
                {"exam_date": recent, "color_blindness": False, "vertigo": False,
                 "vision": "6/6", "blood_type": "A+"},
                {"issue_date": recent, "status": VERIFIED, "cert": "PVC-SURESH-01"},
                [(TRADE, 40), (SAFETY, 100)],
            ),
            # Anil: medical FAILED (color blindness)
            "100000000003": (
                {"exam_date": recent, "color_blindness": True, "vertigo": False,
                 "vision": "6/9", "blood_type": "B+"},
                {"issue_date": recent, "status": VERIFIED, "cert": "PVC-ANIL-01"},
                [(TRADE, 100), (SAFETY, 100)],
            ),
            # Mahesh: PVC expired
            "100000000004": (
                {"exam_date": recent, "color_blindness": False, "vertigo": False,
                 "vision": "6/6", "blood_type": "AB+"},
                {"issue_date": stale, "status": VERIFIED, "cert": "PVC-MAHESH-01"},
                [(TRADE, 100), (SAFETY, 100)],
            ),
            # Deepak: no medical / no PVC / no videos at all
            "100000000005": (None, None, []),
        }

        for aadhar_no, (med, pol, videos) in plan.items():
            worker = Worker.objects.filter(aadhar_number=aadhar_no).first()
            if worker is None:
                continue

            if med and not worker.medical_records.exists():
                IntakeMedicalRecord.objects.create(
                    worker=worker,
                    exam_date=med["exam_date"],
                    color_blindness=med["color_blindness"],
                    vertigo=med["vertigo"],
                    vision=med["vision"],
                    blood_type=med["blood_type"],
                )  # expiry auto-computed

            if pol and not worker.police_verifications.exists():
                IntakePoliceVerification.objects.create(
                    worker=worker,
                    issue_date=pol["issue_date"],
                    certificate_number=pol["cert"],
                    verification_status=pol["status"],
                )

            for vtype, pct in videos:
                IntakeVideoProgress.objects.get_or_create(
                    worker=worker,
                    video_type=vtype,
                    defaults={"progress_percentage": pct, "is_completed": pct >= 100},
                )
