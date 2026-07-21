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

from urllib.parse import quote

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
    TradeTestQuestion,
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
        self._seed_trade_test_questions()

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
        """Seed medical / police / trade-test pillars so each failure mode is visible.

        Ravi passes everything (Ready); everyone else fails at least one pillar.
        Idempotent: only creates records the worker doesn't already have.
        """
        today = timezone.now().date()
        recent = today - timedelta(days=30)  # well within the 1-year window
        stale = today - timedelta(days=400)  # already expired

        VERIFIED = WorkerDocument.Status.VERIFIED

        # aadhar -> (medical spec | None, police spec | None, trade_test_passed)
        plan = {
            # Ravi: everything passes -> Ready
            "100000000001": (
                {"exam_date": recent, "color_blindness": False, "vertigo": False,
                 "vision": "6/6", "blood_type": "O+"},
                {"issue_date": recent, "status": VERIFIED, "cert": "PVC-RAVI-01"},
                True,
            ),
            # Suresh: trade test not yet taken
            "100000000002": (
                {"exam_date": recent, "color_blindness": False, "vertigo": False,
                 "vision": "6/6", "blood_type": "A+"},
                {"issue_date": recent, "status": VERIFIED, "cert": "PVC-SURESH-01"},
                False,
            ),
            # Anil: medical FAILED (color blindness)
            "100000000003": (
                {"exam_date": recent, "color_blindness": True, "vertigo": False,
                 "vision": "6/9", "blood_type": "B+"},
                {"issue_date": recent, "status": VERIFIED, "cert": "PVC-ANIL-01"},
                False,
            ),
            # Mahesh: PVC expired
            "100000000004": (
                {"exam_date": recent, "color_blindness": False, "vertigo": False,
                 "vision": "6/6", "blood_type": "AB+"},
                {"issue_date": stale, "status": VERIFIED, "cert": "PVC-MAHESH-01"},
                False,
            ),
            # Deepak: no medical / no PVC / no trade test
            "100000000005": (None, None, False),
        }

        for aadhar_no, (med, pol, passed) in plan.items():
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

            # Trade test + safety video: mark Ravi complete so he stays Ready;
            # leave the rest for the Field Officer to administer.
            if passed and not worker.trade_test_attempts.exists():
                TradeTestAttempt.objects.create(
                    worker=worker, attempt_number=1, score=4, is_passed=True
                )
                worker.trade_test_status = Worker.TradeTestStatus.PASSED
                worker.save(update_fields=["trade_test_status"])
            if passed:
                SafetyTrainingProgress.objects.get_or_create(
                    worker=worker,
                    defaults={"progress_percentage": 100, "is_completed": True},
                )

    # -- Trade test question bank (image-aided practical MCQs) ---------------
    def _seed_trade_test_questions(self) -> None:
        """Seed image-aided practical questions per category. The picture is a
        comprehension aid for non-literate workers, not a 'name this object' quiz.
        Idempotent on (skill_type, question_text).
        """
        created = 0
        for cat, svg_body, text, opts, correct in TRADE_TEST_QUESTIONS:
            image = "data:image/svg+xml," + quote(_SVG.format(body=svg_body))
            _, is_new = TradeTestQuestion.objects.get_or_create(
                skill_type=cat,
                question_text=text,
                defaults={
                    "image_url": image,
                    "option_a": opts[0],
                    "option_b": opts[1],
                    "option_c": opts[2],
                    "option_d": opts[3],
                    "correct_option": correct,
                },
            )
            created += int(is_new)
        self.stdout.write(f"  Trade-test questions: {created} new, "
                          f"{TradeTestQuestion.objects.count()} total")


# ---------------------------------------------------------------------------
# Image-aided practical trade-test questions.
# The image is a comprehension aid (a valve, coloured wires, a hazard) drawn as a
# self-contained SVG data-URI so it always renders — no external image hosting.
# ---------------------------------------------------------------------------
_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 170">'
    '<rect width="300" height="170" fill="#f8fafc"/>{body}</svg>'
)

# (category, svg_body, question_text, [A, B, C, D], correct_option)
TRADE_TEST_QUESTIONS = [
    # ---- ELECTRICAL ----
    ("ELECTRICAL",
     '<rect x="30" y="35" width="210" height="20" rx="10" fill="#dc2626"/><text x="252" y="51" font-size="15" fill="#111">1</text>'
     '<rect x="30" y="75" width="210" height="20" rx="10" fill="#111827"/><text x="252" y="91" font-size="15" fill="#111">2</text>'
     '<rect x="30" y="115" width="210" height="20" rx="10" fill="#16a34a"/><text x="252" y="131" font-size="15" fill="#111">3</text>',
     "In this wiring, which coloured wire is the EARTH (safety) wire?",
     ["Red (wire 1)", "Black (wire 2)", "Green (wire 3)", "There is no earth wire"], "C"),

    ("ELECTRICAL",
     '<rect x="115" y="30" width="70" height="110" rx="10" fill="#e5e7eb" stroke="#374151" stroke-width="3"/>'
     '<rect x="130" y="92" width="40" height="40" rx="6" fill="#374151"/>'
     '<text x="150" y="24" font-size="12" text-anchor="middle" fill="#111">MAIN</text>'
     '<text x="150" y="158" font-size="13" text-anchor="middle" fill="#b91c1c">OFF</text>',
     "Before repairing a wire, what must you do FIRST?",
     ["Wear a cap", "Switch OFF the main power", "Work quickly", "Pour water on it"], "B"),

    ("ELECTRICAL",
     '<path d="M55 45 L150 85 M55 125 L150 85" stroke="#374151" stroke-width="9" fill="none" stroke-linecap="round"/>'
     '<path d="M150 85 L245 68 M150 85 L245 102" stroke="#9ca3af" stroke-width="11" fill="none" stroke-linecap="round"/>'
     '<circle cx="150" cy="85" r="7" fill="#111827"/>',
     "Which tool safely removes the plastic cover from a wire?",
     ["Hammer", "Wire stripper", "Paint brush", "Trowel"], "B"),

    ("ELECTRICAL",
     '<circle cx="150" cy="78" r="42" fill="#e5e7eb" stroke="#9ca3af" stroke-width="3"/>'
     '<rect x="136" y="116" width="28" height="22" fill="#9ca3af"/>'
     '<line x1="126" y1="54" x2="174" y2="102" stroke="#ef4444" stroke-width="5"/>'
     '<line x1="174" y1="54" x2="126" y2="102" stroke="#ef4444" stroke-width="5"/>',
     "A light does not turn on. What should you check FIRST?",
     ["The colour of the wall", "Whether the switch is ON", "Add water", "Break the bulb"], "B"),

    ("ELECTRICAL",
     '<line x1="60" y1="150" x2="92" y2="35" stroke="#78350f" stroke-width="5"/>'
     '<line x1="95" y1="150" x2="127" y2="35" stroke="#78350f" stroke-width="5"/>'
     '<line x1="70" y1="120" x2="112" y2="120" stroke="#78350f" stroke-width="4"/>'
     '<line x1="78" y1="90" x2="118" y2="90" stroke="#78350f" stroke-width="4"/>'
     '<circle cx="105" cy="52" r="10" fill="#1f2937"/>'
     '<polyline points="165,35 190,60 165,85 190,110 165,135" fill="none" stroke="#eab308" stroke-width="5"/>',
     "What is the danger in this picture?",
     ["A live electric wire is close by", "The ladder is brown", "His shirt colour", "There is no danger"], "A"),

    ("ELECTRICAL",
     '<rect x="120" y="30" width="60" height="110" rx="8" fill="#e5e7eb" stroke="#374151" stroke-width="3"/>'
     '<rect x="132" y="70" width="36" height="30" rx="4" fill="#dc2626"/>'
     '<text x="150" y="158" font-size="12" text-anchor="middle" fill="#b91c1c">TRIP</text>',
     "This switch keeps turning OFF by itself. What does it mean?",
     ["Everything is fine", "There may be an overload or fault", "It is lunch time", "The paint is dry"], "B"),

    # ---- MECHANICAL ----
    ("MECHANICAL",
     '<polygon points="150,40 186,61 186,103 150,124 114,103 114,61" fill="#9ca3af" stroke="#374151" stroke-width="3"/>'
     '<circle cx="150" cy="82" r="15" fill="#f8fafc" stroke="#374151" stroke-width="3"/>'
     '<path d="M205 55 A60 60 0 1 1 200 50" fill="none" stroke="#2563eb" stroke-width="4"/>'
     '<polygon points="196,42 214,52 194,62" fill="#2563eb"/>',
     "To TIGHTEN this nut, which way do you turn the spanner?",
     ["Clockwise", "Anticlockwise", "Push it inward", "Pull it outward"], "A"),

    ("MECHANICAL",
     '<rect x="40" y="82" width="220" height="26" rx="6" fill="#94a3b8" stroke="#475569" stroke-width="2"/>'
     '<rect x="140" y="108" width="12" height="46" rx="4" fill="#374151"/>'
     '<path d="M126 66 h44 v14 h-16 v10 h-12 v-10 h-16 z" fill="#6b7280" stroke="#374151" stroke-width="2"/>',
     "Which tool is best to grip and turn a round pipe?",
     ["Pipe wrench", "Paint brush", "Trowel", "Spirit level"], "A"),

    ("MECHANICAL",
     '<circle cx="118" cy="92" r="40" fill="#cbd5e1" stroke="#475569" stroke-width="3"/>'
     '<circle cx="118" cy="92" r="12" fill="#f8fafc" stroke="#475569" stroke-width="3"/>'
     '<polygon points="210,48 252,120 168,120" fill="#f59e0b" stroke="#b45309" stroke-width="3"/>'
     '<text x="210" y="114" font-size="34" text-anchor="middle" fill="#78350f">!</text>',
     "A machine's safety guard is missing. What should you do?",
     ["Run it faster", "Do not use it until the guard is fixed", "Ignore it", "Remove more parts"], "B"),

    ("MECHANICAL",
     '<circle cx="108" cy="88" r="36" fill="#cbd5e1" stroke="#475569" stroke-width="3"/>'
     '<circle cx="185" cy="88" r="36" fill="#cbd5e1" stroke="#475569" stroke-width="3"/>'
     '<path d="M108 52 A36 36 0 0 1 140 74" fill="none" stroke="#2563eb" stroke-width="4"/>'
     '<polygon points="134,66 148,78 130,82" fill="#2563eb"/>'
     '<text x="108" y="152" font-size="13" text-anchor="middle" fill="#111">Left</text>'
     '<text x="185" y="152" font-size="13" text-anchor="middle" fill="#111">Right</text>',
     "The LEFT gear turns clockwise. Which way does the RIGHT gear turn?",
     ["Clockwise", "Anticlockwise", "It stays still", "It falls off"], "B"),

    ("MECHANICAL",
     '<rect x="55" y="66" width="72" height="44" rx="8" fill="#6b7280"/>'
     '<polygon points="127,72 165,58 168,66 130,86" fill="#6b7280"/>'
     '<circle cx="168" cy="96" r="5" fill="#f59e0b"/><circle cx="168" cy="112" r="5" fill="#f59e0b"/>'
     '<rect x="150" y="126" width="100" height="16" rx="4" fill="#94a3b8" stroke="#475569" stroke-width="2"/>',
     "A moving joint makes a squeaking noise. What should you apply?",
     ["Water", "Oil or grease", "Sand", "Paint"], "B"),

    ("MECHANICAL",
     '<rect x="88" y="28" width="124" height="30" fill="#94a3b8" stroke="#475569" stroke-width="2"/>'
     '<polygon points="120,58 180,58 166,120 134,120" fill="#cbd5e1" stroke="#475569" stroke-width="3"/>'
     '<line x1="150" y1="120" x2="150" y2="146" stroke="#475569" stroke-width="7"/>',
     "You must lift a heavy machine part safely. What do you use?",
     ["Bare hands", "A hydraulic jack", "A broom", "A cloth"], "B"),

    # ---- CIVIL ----
    ("CIVIL",
     '<rect x="30" y="72" width="240" height="34" rx="6" fill="#fde68a" stroke="#b45309" stroke-width="3"/>'
     '<rect x="118" y="78" width="64" height="22" rx="11" fill="#dbeafe" stroke="#2563eb" stroke-width="2"/>'
     '<circle cx="138" cy="89" r="8" fill="#22c55e"/>'
     '<line x1="146" y1="76" x2="146" y2="102" stroke="#2563eb" stroke-width="2"/>'
     '<line x1="154" y1="76" x2="154" y2="102" stroke="#2563eb" stroke-width="2"/>',
     "The bubble is NOT between the centre lines. What does this mean?",
     ["The surface is perfectly level", "The surface is NOT level", "The tool is broken", "Time to stop work"], "B"),

    ("CIVIL",
     '<polygon points="55,58 175,82 92,140" fill="#cbd5e1" stroke="#475569" stroke-width="3"/>'
     '<rect x="168" y="72" width="66" height="12" rx="6" fill="#78350f"/>',
     "Which tool spreads cement mortar between bricks?",
     ["Masonry trowel", "Screwdriver", "Spanner", "Multimeter"], "A"),

    ("CIVIL",
     '<g fill="#fcd34d" stroke="#92400e" stroke-width="2">'
     '<rect x="25" y="40" width="48" height="22"/><rect x="75" y="40" width="48" height="22"/>'
     '<rect x="25" y="64" width="24" height="22"/><rect x="51" y="64" width="48" height="22"/><rect x="101" y="64" width="22" height="22"/>'
     '<rect x="25" y="88" width="48" height="22"/><rect x="75" y="88" width="48" height="22"/>'
     '<rect x="165" y="40" width="48" height="22"/><rect x="215" y="40" width="48" height="22"/>'
     '<rect x="165" y="64" width="48" height="22"/><rect x="215" y="64" width="48" height="22"/>'
     '<rect x="165" y="88" width="48" height="22"/><rect x="215" y="88" width="48" height="22"/></g>'
     '<text x="74" y="132" font-size="13" text-anchor="middle" fill="#111">Left</text>'
     '<text x="214" y="132" font-size="13" text-anchor="middle" fill="#111">Right</text>',
     "Which brick wall is STRONGER?",
     ["Left wall (overlapping joints)", "Right wall (straight joints)", "Both are equal", "Neither is strong"], "A"),

    ("CIVIL",
     '<polygon points="70,40 112,40 104,78 78,78" fill="#94a3b8" stroke="#475569" stroke-width="2"/>'
     '<path d="M100 74 q8 26 4 46" fill="none" stroke="#3b82f6" stroke-width="6"/>'
     '<ellipse cx="160" cy="132" rx="78" ry="18" fill="#a8a29e"/>',
     "Too much water is added to the concrete mix. The concrete becomes...?",
     ["Stronger", "Weaker", "Exactly the same", "Waterproof"], "B"),

    ("CIVIL",
     '<circle cx="110" cy="70" r="22" fill="#fcd34d" stroke="#92400e" stroke-width="2"/>'
     '<rect x="94" y="94" width="32" height="46" rx="4" fill="#3b82f6"/>'
     '<path d="M182 80 a26 20 0 0 1 52 0 z" fill="#f59e0b" stroke="#b45309" stroke-width="2"/>'
     '<circle cx="208" cy="74" r="30" fill="none" stroke="#dc2626" stroke-width="4"/>'
     '<line x1="188" y1="54" x2="228" y2="94" stroke="#dc2626" stroke-width="4"/>',
     "What safety item is MISSING on this worker?",
     ["A helmet", "A wristwatch", "Sunglasses", "Nothing is missing"], "A"),

    ("CIVIL",
     '<rect x="55" y="28" width="22" height="120" fill="#d6d3d1" stroke="#78716c" stroke-width="2"/>'
     '<line x1="150" y1="30" x2="150" y2="112" stroke="#374151" stroke-width="2"/>'
     '<polygon points="140,112 160,112 150,142" fill="#6b7280" stroke="#374151" stroke-width="2"/>',
     "This hanging weight (a plumb bob) is used to check...?",
     ["The weight of the wall", "A perfectly vertical straight line", "The colour", "The temperature"], "B"),
]
