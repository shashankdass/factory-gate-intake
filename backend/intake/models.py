"""
Domain models for the Factory Gate-Intake platform.

The data model mirrors the PostgreSQL DDL in ``sql/schema.sql`` one-to-one. The single
most important piece of business logic lives on ``Worker.evaluate_compliance`` /
``Worker.compliance_against_project`` which decides whether a worker may be
deployed to a given project and, if not, *exactly* why.
"""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

# Strict 1-year validity window applied to medical exams and police verifications.
INTAKE_EXPIRY_DAYS = 365

# A worker's free-text skill maps to one of the three trade-test categories.
TRADE_CATEGORIES = ("MECHANICAL", "CIVIL", "ELECTRICAL")
_SKILL_TO_CATEGORY = {
    # Electrical
    "electrician": "ELECTRICAL", "electrical": "ELECTRICAL", "wireman": "ELECTRICAL",
    "lineman": "ELECTRICAL", "wiring": "ELECTRICAL",
    # Civil
    "mason": "CIVIL", "carpenter": "CIVIL", "plumber": "CIVIL", "painter": "CIVIL",
    "helper": "CIVIL", "civil": "CIVIL", "tiler": "CIVIL", "shuttering": "CIVIL",
    # Mechanical
    "welder": "MECHANICAL", "fitter": "MECHANICAL", "mechanic": "MECHANICAL",
    "machinist": "MECHANICAL", "turner": "MECHANICAL", "rigger": "MECHANICAL",
    "mechanical": "MECHANICAL",
}


def category_for_skill(skill_type: str) -> str:
    """Map a worker's free-text skill to a trade-test category (default MECHANICAL)."""
    return _SKILL_TO_CATEGORY.get((skill_type or "").strip().lower(), "MECHANICAL")


# ---------------------------------------------------------------------------
# Users & roles
# ---------------------------------------------------------------------------
class User(AbstractUser):
    """Custom user carrying a factory persona role.

    We authenticate by email but keep ``username`` (Django needs it) mirrored to
    the email for simplicity.
    """

    class Role(models.TextChoices):
        PRINCIPAL_EMPLOYER = "PE", "Principal Employer"
        CONTRACTOR = "CONTRACTOR", "Contractor"
        GATE_SECURITY = "GATE_SECURITY", "Gate Security"

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices)
    # Contractors belong to a vendor/company label (free text for this MVP).
    # PE + Gate belong to the factory.
    organization = models.CharField(max_length=150, blank=True, default="")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.email} ({self.get_role_display()})"


# ---------------------------------------------------------------------------
# Requirements catalogue
# ---------------------------------------------------------------------------
class RequirementMaster(models.Model):
    """Something a worker may be required to have before they can be deployed.

    Two kinds, deliberately in one table. A **document** is a scan the worker
    holds (Aadhar, PAN, Safety Training certificate). A **pillar** is a state
    the platform tracks itself — the medical, the police verification, the trade
    test, the safety video, the resume.

    The pillars used to be hard-coded and always blocking, which left the
    contractor able to waive an Aadhaar card but not a trade test: the whole bar
    in two places, with the more serious half of it unreachable. Putting both
    kinds here means one catalogue, one ProjectRequirement row per entry, one
    soft-waive with attribution, and one strip in the UI.
    """

    class Kind(models.TextChoices):
        DOCUMENT = "DOCUMENT", "Document the worker holds"
        PILLAR = "PILLAR", "Intake pillar the platform tracks"

    class Pillar(models.TextChoices):
        MEDICAL = "MEDICAL", "Medical Exam"
        POLICE = "POLICE", "Police Verification"
        TRADE_TEST = "TRADE_TEST", "Trade Test"
        SAFETY_VIDEO = "SAFETY_VIDEO", "Safety Training Video"
        RESUME = "RESUME", "Resume on file"

    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True, default="")
    kind = models.CharField(
        max_length=10, choices=Kind.choices, default=Kind.DOCUMENT, db_index=True
    )
    # Set only on PILLAR rows; ties the catalogue entry to the check in
    # _intake_status that evaluates it.
    pillar_code = models.CharField(
        max_length=20, choices=Pillar.choices, blank=True, default=""
    )
    # Expirable requirements (e.g. Safety Training) are only "Verified" while the
    # attached document's expiry_date is still in the future.
    is_expirable = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_pillar(self) -> bool:
        return self.kind == self.Kind.PILLAR

    class Meta:
        db_table = "requirements_master"
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class Project(models.Model):
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True, default="")
    principal_employer = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="owned_projects",
        limit_choices_to={"role": User.Role.PRINCIPAL_EMPLOYER},
    )
    # Contractors that the PE has assigned to work on this project.
    contractors = models.ManyToManyField(
        User,
        related_name="assigned_projects",
        blank=True,
        limit_choices_to={"role": User.Role.CONTRACTOR},
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "projects"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["is_active"])]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    def mandatory_requirement_ids(self) -> list[int]:
        return list(
            self.project_requirements.filter(is_mandatory=True).values_list(
                "requirement_id", flat=True
            )
        )


class ProjectRequirement(models.Model):
    """Junction: which requirements a given project demands.

    Contractors can edit this set, which means they can lower the bar their own
    workers are measured against. That is a deliberate product decision, so the
    change is **attributed** rather than silent: who last touched it and when
    travel with the row and are shown to the Principal Employer at review.
    """

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="project_requirements"
    )
    requirement = models.ForeignKey(
        RequirementMaster, on_delete=models.CASCADE, related_name="in_projects"
    )
    is_mandatory = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requirement_changes",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "project_requirements"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "requirement"],
                name="uq_project_requirement",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.project.name} → {self.requirement.name}"


# ---------------------------------------------------------------------------
# Workers & documents
# ---------------------------------------------------------------------------
class Worker(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        INACTIVE = "INACTIVE", "Inactive"
        BLOCKED = "BLOCKED", "Blocked"

    class TradeTestStatus(models.TextChoices):
        PENDING = "PENDING", "Not yet taken"
        PASSED = "PASSED", "Passed"
        FAILED = "FAILED", "Failed (locked)"

    name = models.CharField(max_length=150)
    skill_type = models.CharField(max_length=100, db_index=True)
    # Aadhar is the worker's unique national ID — enforced UNIQUE to prevent
    # duplicate master profiles.
    aadhar_number = models.CharField(max_length=12, unique=True)
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.ACTIVE
    )
    # Result of the Field-Officer-administered practical trade test. Locked to
    # FAILED after 3 unsuccessful attempts (see intake/views.py trade-test flow).
    trade_test_status = models.CharField(
        max_length=10,
        choices=TradeTestStatus.choices,
        default=TradeTestStatus.PENDING,
    )
    # The vendor/contractor this worker is pre-assigned to. Field Officers create
    # workers in the master registry and stamp this ownership.
    contractor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workers",
        limit_choices_to={"role": User.Role.CONTRACTOR},
    )
    # A face photo, if the contractor has one. Optional: plenty of workers are
    # onboarded from a stack of paperwork with no photograph in it, and a
    # missing photo must never be a reason someone cannot work. Stored in the
    # same private bucket as the documents and served as an expiring link.
    photo_key = models.CharField(max_length=500, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "workers"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["aadhar_number"]),
            models.Index(fields=["contractor", "skill_type"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.name} [{self.aadhar_number}]"

    # -- Compliance engine ---------------------------------------------------
    def compliance_against_project(self, project: "Project") -> dict:
        """Evaluate this worker against a project's mandatory requirements.

        Returns a structured dict describing readiness and, for every gap, the
        precise reason it is a gap. This is the single source of truth consumed by
        the eligible-workers endpoint, the contractor UI, and the gate check.

        Shape::

            {
                "worker_id": 12,
                "is_compliant": False,
                "satisfied": [{"requirement_id": 1, "requirement_name": "Aadhar"}],
                "gaps": [
                    {
                        "requirement_id": 3,
                        "requirement_name": "Safety Training",
                        "is_expirable": True,
                        "reason": "EXPIRED",          # MISSING | EXPIRED | REJECTED | PENDING
                        "document_id": 45,            # null when MISSING
                        "expiry_date": "2025-01-01",  # null when not applicable
                        "rejection_reason": "",
                    },
                    ...
                ],
            }
        """
        today = timezone.now().date()

        # Pull the project's mandatory requirements once, then split them: the
        # document rows drive the loop below, the pillar rows decide which of
        # the intake checks block.
        all_rows = list(project.project_requirements.select_related("requirement"))
        required = [
            pr for pr in all_rows
            if pr.is_mandatory and not pr.requirement.is_pillar
        ]
        mandatory_pillars = {
            pr.requirement.pillar_code
            for pr in all_rows
            if pr.is_mandatory and pr.requirement.is_pillar
        }
        # A project that has never had pillar rows written is not a project with
        # nothing to check — it is a project that predates them. Falling through
        # to "enforce none" would silently pass every worker, so an unconfigured
        # project keeps the platform defaults.
        if not any(pr.requirement.is_pillar for pr in all_rows):
            mandatory_pillars = None

        # Index this worker's documents by requirement so lookups are O(1). A
        # worker can technically have more than one doc per requirement (e.g. a
        # rejected one plus a re-uploaded one) so we keep the *best* per slot.
        docs_by_requirement: dict[int, list[WorkerDocument]] = {}
        for doc in self.documents.all():
            docs_by_requirement.setdefault(doc.requirement_id, []).append(doc)

        satisfied: list[dict] = []
        gaps: list[dict] = []

        for pr in required:
            req = pr.requirement
            candidate_docs = docs_by_requirement.get(req.id, [])

            best = self._best_document(candidate_docs, req, today)

            if best is not None and best["reason"] is None:
                satisfied.append(
                    {"requirement_id": req.id, "requirement_name": req.name}
                )
            else:
                # No document at all, or the best available one is not usable.
                reason = best["reason"] if best else "MISSING"
                doc = best["doc"] if best else None
                gaps.append(
                    {
                        "kind": "document",
                        "requirement_id": req.id,
                        "requirement_name": req.name,
                        "is_expirable": req.is_expirable,
                        "reason": reason,
                        "document_id": doc.id if doc else None,
                        "expiry_date": doc.expiry_date.isoformat()
                        if doc and doc.expiry_date
                        else None,
                        "rejection_reason": doc.rejection_reason if doc else "",
                    }
                )

        # Merge in the intake pillar checks. These are global to the worker
        # rather than project-specific, but whether each one *blocks* is the
        # project's call, exactly as it is for documents.
        intake_gaps, intake_satisfied, advisories = self._intake_status(
            today, mandatory_pillars
        )
        gaps.extend(intake_gaps)
        satisfied.extend(intake_satisfied)

        return {
            "worker_id": self.id,
            "is_compliant": len(gaps) == 0,
            "satisfied": satisfied,
            "gaps": gaps,
            # Non-blocking findings (e.g. a missing resume while
            # REQUIRE_RESUME_FOR_COMPLIANCE is off). Surfaced to the UI but never
            # counted against compliance.
            "advisories": advisories,
            "evaluated_at": timezone.now().isoformat(),
        }

    def compliance_snapshot(self, project: "Project | None" = None) -> dict:
        """Compliance with or without a project.

        With a project this is exactly ``compliance_against_project``. Without
        one — e.g. the contractor's workforce-demand search before a project is
        chosen — only the worker-global intake pillars are evaluated, since
        document requirements are defined per project.
        """
        if project is not None:
            return self.compliance_against_project(project)

        today = timezone.now().date()
        gaps, satisfied, advisories = self._intake_status(today)
        return {
            "worker_id": self.id,
            "is_compliant": len(gaps) == 0,
            "satisfied": satisfied,
            "gaps": gaps,
            "advisories": advisories,
            "evaluated_at": timezone.now().isoformat(),
        }

    def default_blocking_pillars(self) -> set[str]:
        """Which pillars block when a project has not said otherwise.

        The behaviour the platform had before pillars became waivable, kept as
        the fallback so an unconfigured project is strict rather than empty.
        """
        pillars = {
            RequirementMaster.Pillar.MEDICAL,
            RequirementMaster.Pillar.POLICE,
            RequirementMaster.Pillar.TRADE_TEST,
            RequirementMaster.Pillar.SAFETY_VIDEO,
        }
        if getattr(settings, "REQUIRE_RESUME_FOR_COMPLIANCE", False):
            pillars.add(RequirementMaster.Pillar.RESUME)
        return set(pillars)

    def _intake_status(
        self, today, mandatory_pillars: set[str] | None = None
    ) -> tuple[list[dict], list[dict], list[dict]]:
        """Evaluate the medical / police / trade-test / video / resume pillars.

        ``mandatory_pillars`` names which of them block. Anything outside it is
        still evaluated and still reported — as an *advisory* rather than a gap.
        A waived pillar is not an unasked question: the contractor chose not to
        gate on it, and can still see where their workers stand.

        ``None`` means the caller has no project to ask, so the platform
        defaults apply.

        Returns ``(gaps, satisfied, advisories)`` where each entry is a dict
        tagged with ``kind="intake"`` and a ``pillar`` so the UI can render an
        explanation (these have no uploadable document slot).
        """
        if mandatory_pillars is None:
            mandatory_pillars = self.default_blocking_pillars()

        gaps: list[dict] = []
        satisfied: list[dict] = []
        advisories: list[dict] = []

        def add(pillar, name, ok, reason=None, detail="", blocking=None):
            if blocking is None:
                blocking = pillar in mandatory_pillars
            if ok:
                satisfied.append({"pillar": pillar, "requirement_name": name})
                return
            entry = {
                "kind": "intake",
                "pillar": pillar,
                "requirement_id": None,
                "requirement_name": name,
                "reason": reason,
                "detail": detail,
            }
            (gaps if blocking else advisories).append(entry)

        # --- Pillar 1: Medical ---
        med = self.medical_records.order_by("-exam_date").first()
        if med is None:
            add("MEDICAL", "Medical Exam", False, "MISSING", "No medical record on file.")
        elif med.expiry_date and med.expiry_date < today:
            add("MEDICAL", "Medical Exam", False, "EXPIRED",
                f"Medical expired on {med.expiry_date.isoformat()}.")
        else:
            fails = []
            if med.color_blindness:
                fails.append("color blindness")
            if med.vertigo:
                fails.append("vertigo")
            if fails:
                add("MEDICAL", "Medical Exam", False, "FAILED",
                    "Medical flag(s): " + ", ".join(fails) + ".")
            else:
                add("MEDICAL", "Medical Exam", True)

        # --- Pillar 2: Police verification (PVC) ---
        pvc = self.police_verifications.order_by("-issue_date").first()
        if pvc is None:
            add("POLICE", "Police Verification", False, "MISSING",
                "No police verification on file.")
        elif pvc.verification_status != WorkerDocument.Status.VERIFIED:
            add("POLICE", "Police Verification", False, "PENDING",
                f"PVC status is {pvc.verification_status}.")
        elif pvc.expiry_date and pvc.expiry_date < today:
            add("POLICE", "Police Verification", False, "EXPIRED",
                f"PVC expired on {pvc.expiry_date.isoformat()}.")
        else:
            add("POLICE", "Police Verification", True)

        # --- Pillar 3: Trade Test (Field-Officer-administered practical exam) ---
        if self.trade_test_status == Worker.TradeTestStatus.PASSED:
            add("TRADE_TEST", "Trade Test", True)
        elif self.trade_test_status == Worker.TradeTestStatus.FAILED:
            add("TRADE_TEST", "Trade Test", False, "FAILED",
                "Failed all 3 trade-test attempts — profile locked.")
        else:
            add("TRADE_TEST", "Trade Test", False, "NOT_PASSED",
                "Practical trade test not yet passed.")

        # --- Pillar 4: Safety Training video (mandatory induction clip) ---
        try:
            sv = self.safety_video
        except SafetyTrainingProgress.DoesNotExist:
            sv = None
        if sv and sv.is_completed and sv.progress_percentage >= 100:
            add("SAFETY_VIDEO", "Safety Training Video", True)
        else:
            pct = sv.progress_percentage if sv else 0
            add("SAFETY_VIDEO", "Safety Training Video", False, "INCOMPLETE",
                f"Safety induction video only {pct}% watched.")

        # --- Pillar 5: Resume on file (scanned + parsed candidate profile) ---
        try:
            profile = self.candidate_profile
        except CandidateProfile.DoesNotExist:
            profile = None
        if profile is not None and profile.resume_key:
            add("RESUME", "Resume on file", True)
        elif profile is not None:
            add("RESUME", "Resume on file", False, "MISSING",
                "A candidate profile exists but no resume document is stored.")
        else:
            add("RESUME", "Resume on file", False, "MISSING",
                "No resume has been scanned for this worker.")

        return gaps, satisfied, advisories

    @staticmethod
    def _best_document(
        docs: list["WorkerDocument"], requirement: "RequirementMaster", today
    ) -> dict | None:
        """Pick the most favourable document for a requirement and classify it.

        Preference order: a fully valid Verified (non-expired) document wins. If
        none is valid we surface the *least bad* reason so the contractor sees the
        most actionable message (an expired verified doc beats a pending one).

        Returns ``{"doc": WorkerDocument, "reason": <str|None>}`` or ``None`` when
        the worker holds no document for this requirement at all.
        """
        if not docs:
            return None

        ranked: list[tuple[int, WorkerDocument, str | None]] = []
        for doc in docs:
            if doc.verification_status == WorkerDocument.Status.VERIFIED:
                if requirement.is_expirable and doc.expiry_date and doc.expiry_date < today:
                    ranked.append((3, doc, "EXPIRED"))
                else:
                    ranked.append((0, doc, None))  # fully valid
            elif doc.verification_status == WorkerDocument.Status.REJECTED:
                ranked.append((2, doc, "REJECTED"))
            else:  # Pending
                ranked.append((1, doc, "PENDING"))

        ranked.sort(key=lambda t: t[0])  # 0 (valid) is best
        _, best_doc, reason = ranked[0]
        return {"doc": best_doc, "reason": reason}

    def is_gate_cleared(self) -> "IntakeList | None":
        """Return an Approved intake list containing this worker, if any.

        Approval is necessary but **not sufficient** — see ``gate_decision``.
        """
        return (
            IntakeList.objects.filter(
                status=IntakeList.Status.APPROVED,
                list_workers__worker=self,
            )
            .select_related("project")
            .order_by("-reviewed_at")
            .first()
        )

    def gate_decision(self) -> dict:
        """Real-time GREEN/RED decision for gate security.

        An approved intake list is a *snapshot* taken when the Principal
        Employer signed off. Documents keep expiring after that: a medical or
        PVC can lapse, a Safety Training certificate can hit its expiry date, a
        pillar can regress. Trusting the snapshot admits a worker whose papers
        died last week.

        So the gate re-runs ``compliance_against_project`` against the approved
        list's project **at scan time** and denies entry the moment any blocking
        gap exists — reporting the precise reason so the guard can say why.

        Shape::

            {"access": "GRANTED"|"DENIED", "reason_code": ..., "reason": ...,
             "project": ..., "list_id": ..., "compliance": {...}|None}
        """
        approved_list = self.is_gate_cleared()
        if approved_list is None:
            return {
                "access": "DENIED",
                "reason_code": "NOT_APPROVED",
                "reason": "Worker is not on any approved deployment list.",
                "project": None,
                "list_id": None,
                "compliance": None,
            }

        compliance = self.compliance_against_project(approved_list.project)
        if compliance["is_compliant"]:
            return {
                "access": "GRANTED",
                "reason_code": "COMPLIANT",
                "reason": "Approved for deployment and all documents are currently valid.",
                "project": approved_list.project.name,
                "list_id": approved_list.id,
                "compliance": compliance,
            }

        # Expiry is the headline case the gate exists to catch — lead with it.
        expired = [g for g in compliance["gaps"] if g.get("reason") == "EXPIRED"]
        if expired:
            names = ", ".join(g["requirement_name"] for g in expired)
            return {
                "access": "DENIED",
                "reason_code": "DOCUMENT_EXPIRED",
                "reason": f"Document expired since approval: {names}.",
                "project": approved_list.project.name,
                "list_id": approved_list.id,
                "compliance": compliance,
            }

        names = ", ".join(g["requirement_name"] for g in compliance["gaps"])
        return {
            "access": "DENIED",
            "reason_code": "COMPLIANCE_REGRESSED",
            "reason": f"No longer compliant since approval: {names}.",
            "project": approved_list.project.name,
            "list_id": approved_list.id,
            "compliance": compliance,
        }


class WorkerDocument(models.Model):
    class Status(models.TextChoices):
        PENDING = "Pending", "Pending"
        VERIFIED = "Verified", "Verified"
        REJECTED = "Rejected", "Rejected"

    worker = models.ForeignKey(
        Worker, on_delete=models.CASCADE, related_name="documents"
    )
    requirement = models.ForeignKey(
        RequirementMaster, on_delete=models.CASCADE, related_name="documents"
    )
    document_number = models.CharField(max_length=120, blank=True, default="")
    # Object key inside the PRIVATE Supabase bucket. Never publicly reachable —
    # the API mints a short-lived signed URL on request (see storage.py).
    storage_key = models.CharField(max_length=500, blank=True, default="")
    # Legacy local-media columns, retained so pre-Supabase rows keep resolving.
    document_file = models.FileField(upload_to="worker_docs/", null=True, blank=True)
    file_url = models.URLField(max_length=500, blank=True, default="")
    verification_status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING
    )
    expiry_date = models.DateField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "worker_documents"
        indexes = [
            models.Index(fields=["worker", "requirement"]),
            models.Index(fields=["verification_status"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.worker.name} · {self.requirement.name} · {self.verification_status}"


# ---------------------------------------------------------------------------
# Deployment / intake lists
# ---------------------------------------------------------------------------
class IntakeList(models.Model):
    class Status(models.TextChoices):
        DRAFT = "Draft", "Draft"
        SUBMITTED = "Submitted", "Submitted"
        REVISION_REQUESTED = "Revision_Requested", "Revision Requested"
        APPROVED = "Approved", "Approved"
        REJECTED = "Rejected", "Rejected"

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="intake_lists"
    )
    contractor = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="intake_lists",
        limit_choices_to={"role": User.Role.CONTRACTOR},
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    pe_comments = models.TextField(blank=True, default="")
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "intake_lists"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["project", "contractor", "status"])]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"List #{self.id} · {self.project.name} · {self.status}"


class IntakeListWorker(models.Model):
    intake_list = models.ForeignKey(
        IntakeList, on_delete=models.CASCADE, related_name="list_workers"
    )
    worker = models.ForeignKey(
        Worker, on_delete=models.CASCADE, related_name="list_memberships"
    )

    class Meta:
        db_table = "intake_list_workers"
        constraints = [
            models.UniqueConstraint(
                fields=["intake_list", "worker"],
                name="uq_intake_list_worker",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.worker.name} @ list#{self.intake_list_id}"


# ---------------------------------------------------------------------------
# 5-pillar intake records
# ---------------------------------------------------------------------------
class IntakeMedicalRecord(models.Model):
    """Medical fitness exam. Valid for exactly 1 year from ``exam_date``."""

    worker = models.ForeignKey(
        Worker, on_delete=models.CASCADE, related_name="medical_records"
    )
    color_blindness = models.BooleanField(default=False)
    vision = models.CharField(max_length=20, blank=True, default="")  # e.g. "6/6"
    vertigo = models.BooleanField(default=False)
    blood_type = models.CharField(max_length=5, blank=True, default="")
    exam_date = models.DateField()
    expiry_date = models.DateField(blank=True, null=True)
    # The scanned document the Contractor verified against, on the spot — stored
    # in the private Supabase bucket.
    storage_key = models.CharField(max_length=500, blank=True, default="")
    document_file = models.FileField(upload_to="intake_docs/", null=True, blank=True)
    file_url = models.URLField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "intake_medical_records"
        ordering = ["-exam_date"]
        indexes = [models.Index(fields=["worker", "expiry_date"])]

    def save(self, *args, **kwargs):
        # Strictly recompute the 1-year expiry window from exam_date every save.
        if self.exam_date:
            self.expiry_date = self.exam_date + timedelta(days=INTAKE_EXPIRY_DAYS)
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"Medical · {self.worker.name} · exp {self.expiry_date}"


class IntakePoliceVerification(models.Model):
    """Police verification certificate (PVC). Valid 1 year from ``issue_date``."""

    worker = models.ForeignKey(
        Worker, on_delete=models.CASCADE, related_name="police_verifications"
    )
    certificate_number = models.CharField(max_length=120, blank=True, default="")
    issue_date = models.DateField()
    expiry_date = models.DateField(blank=True, null=True)
    verification_status = models.CharField(
        max_length=10,
        choices=WorkerDocument.Status.choices,
        default=WorkerDocument.Status.VERIFIED,
    )
    storage_key = models.CharField(max_length=500, blank=True, default="")
    document_file = models.FileField(upload_to="intake_docs/", null=True, blank=True)
    file_url = models.URLField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "intake_police_verifications"
        ordering = ["-issue_date"]
        indexes = [models.Index(fields=["worker", "expiry_date"])]

    def save(self, *args, **kwargs):
        if self.issue_date:
            self.expiry_date = self.issue_date + timedelta(days=INTAKE_EXPIRY_DAYS)
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"PVC · {self.worker.name} · exp {self.expiry_date}"


# ---------------------------------------------------------------------------
# Trade test — Field-Officer-administered, on-the-spot practical MCQ exam
# ---------------------------------------------------------------------------
class TradeTestQuestion(models.Model):
    """A role-specific, image-aided multiple-choice question.

    The image is a *visual aid* (a valve, coloured wires, a hazard) so a worker
    with no book training can understand the plain-language question — not merely
    a "name this object" prompt.
    """

    class Category(models.TextChoices):
        MECHANICAL = "MECHANICAL", "Mechanical"
        CIVIL = "CIVIL", "Civil"
        ELECTRICAL = "ELECTRICAL", "Electrical"

    class Option(models.TextChoices):
        A = "A", "A"
        B = "B", "B"
        C = "C", "C"
        D = "D", "D"

    skill_type = models.CharField(max_length=12, choices=Category.choices, db_index=True)
    question_text = models.TextField()
    # A URL or a self-contained data: URI (SVG diagram) — hence TextField, not URLField.
    image_url = models.TextField(blank=True, default="")
    option_a = models.CharField(max_length=200)
    option_b = models.CharField(max_length=200)
    option_c = models.CharField(max_length=200)
    option_d = models.CharField(max_length=200)
    correct_option = models.CharField(max_length=1, choices=Option.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "trade_test_questions"
        indexes = [models.Index(fields=["skill_type"])]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.skill_type}] {self.question_text[:50]}"


class TradeTestAttempt(models.Model):
    """One historical exam attempt for a worker (max 3 per worker)."""

    worker = models.ForeignKey(
        Worker, on_delete=models.CASCADE, related_name="trade_test_attempts"
    )
    attempt_number = models.PositiveSmallIntegerField()  # 1, 2 or 3
    score = models.PositiveSmallIntegerField()  # out of 5
    is_passed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "trade_test_attempts"
        ordering = ["worker", "attempt_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["worker", "attempt_number"], name="uq_worker_attempt_number"
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.worker.name} · attempt {self.attempt_number} · {self.score}/5"


# ---------------------------------------------------------------------------
# Safety Training video — a mandatory induction clip every worker must watch
# ---------------------------------------------------------------------------
class SafetyTrainingProgress(models.Model):
    """Per-worker watch progress for the mandatory safety induction video.

    Distinct from the trade test (a practical exam) and from the Safety Training
    certificate document — this is the induction clip every worker watches.
    """

    worker = models.OneToOneField(
        Worker, on_delete=models.CASCADE, related_name="safety_video"
    )
    progress_percentage = models.PositiveIntegerField(default=0)
    is_completed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "safety_training_progress"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"Safety video · {self.worker.name} · {self.progress_percentage}%"


# ---------------------------------------------------------------------------
# Resume profiles — encrypted PII + plaintext filterable attributes
#
# PII STRATEGY
# ------------
# name / phone / email are stored ONLY as AES-256 ciphertext (BYTEA) produced by
# pgcrypto's pgp_sym_encrypt via the app_encrypt() helper. The passphrase never
# lives in the database — it is bound per statement from PII_ENCRYPTION_KEY.
#
# pgp_sym_encrypt is non-deterministic (random session key + IV), so the same
# phone yields different ciphertext every time and a UNIQUE index on ciphertext
# is useless. Duplicate detection therefore uses a *blind index*: a deterministic
# HMAC-SHA256 of the normalised value, keyed by a SEPARATE pepper
# (PII_BLIND_INDEX_KEY). Equality lookups stay O(log n); the digest is not
# reversible without the pepper.
#
# Everything non-PII lives in plaintext columns so it can carry real indexes and
# power fast multi-attribute fuzzy filtering.
# ---------------------------------------------------------------------------
class CandidateProfile(models.Model):
    """Structured resume data for one worker. 1:1 with the master Worker record."""

    worker = models.OneToOneField(
        Worker, on_delete=models.CASCADE, related_name="candidate_profile"
    )
    # Denormalised owner so the contractor's candidate search never has to join
    # through workers just to scope rows.
    contractor = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="candidate_profiles",
        limit_choices_to={"role": User.Role.CONTRACTOR},
        null=True,
        blank=True,
    )

    # -- PII: ciphertext only. Never add a plaintext column here. -------------
    name_encrypted = models.BinaryField(null=True, blank=True)
    phone_encrypted = models.BinaryField(null=True, blank=True)
    email_encrypted = models.BinaryField(null=True, blank=True)
    # Keyed blind indexes over the normalised values (32-byte HMAC-SHA256).
    phone_hash = models.BinaryField(null=True, blank=True, db_index=True)
    email_hash = models.BinaryField(null=True, blank=True, db_index=True)

    # -- Non-PII: plaintext, indexed, filterable ------------------------------
    place = models.CharField(max_length=120, blank=True, default="", db_index=True)
    stream = models.CharField(max_length=60, blank=True, default="", db_index=True)
    category = models.CharField(max_length=60, blank=True, default="", db_index=True)
    years_of_experience = models.PositiveSmallIntegerField(null=True, blank=True)
    qualification = models.CharField(max_length=60, blank=True, default="", db_index=True)

    # Private Supabase object key for the resume itself.
    resume_key = models.CharField(max_length=500, blank=True, default="")
    parser_provider = models.CharField(max_length=20, blank=True, default="")
    parse_note = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "candidate_profiles"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["contractor", "category"]),
            models.Index(fields=["contractor", "stream"]),
            models.Index(fields=["years_of_experience"]),
        ]
        constraints = [
            # A phone/email identifies one candidate *within a contractor's pool*.
            # Scoped rather than global so two vendors can each hold a record for
            # the same person without one blocking the other's import.
            models.UniqueConstraint(
                fields=["contractor", "phone_hash"],
                name="uq_candidate_contractor_phone",
                condition=models.Q(phone_hash__isnull=False),
            ),
            models.UniqueConstraint(
                fields=["contractor", "email_hash"],
                name="uq_candidate_contractor_email",
                condition=models.Q(email_hash__isnull=False),
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"Candidate profile · worker#{self.worker_id}"

    # -- PII accessors --------------------------------------------------------
    # Encryption happens here so no caller ever holds a plaintext column.
    def set_pii(self, *, name=None, phone=None, email=None) -> None:
        """Encrypt and stamp the PII fields plus their blind indexes."""
        from . import crypto

        if name is not None:
            self.name_encrypted = crypto.encrypt(name)
        if phone is not None:
            self.phone_encrypted = crypto.encrypt(phone)
            self.phone_hash = crypto.blind_index(phone)
        if email is not None:
            self.email_encrypted = crypto.encrypt(email)
            self.email_hash = crypto.blind_index(email)

    @property
    def name(self) -> str | None:
        from . import crypto

        return crypto.decrypt(self.name_encrypted)

    @property
    def phone(self) -> str | None:
        from . import crypto

        return crypto.decrypt(self.phone_encrypted)

    @property
    def email(self) -> str | None:
        from . import crypto

        return crypto.decrypt(self.email_encrypted)

    def sync_name_tokens(self, name: str | None) -> None:
        """Rebuild the searchable blind-index tokens for a name."""
        from . import crypto

        self.name_tokens.all().delete()
        CandidateNameToken.objects.bulk_create(
            [
                CandidateNameToken(profile=self, token_hash=digest)
                for digest in crypto.name_tokens(name)
            ],
            ignore_conflicts=True,
        )


class WorkerBankAccount(models.Model):
    """Where the worker gets paid. One account per worker.

    The account number is PII in the same sense as a phone number, so it gets the
    same treatment: AES-256 ciphertext plus a keyed blind index. The blind index
    earns its keep here beyond lookups — several "different" workers sharing one
    account is the classic ghost-worker signature, and this makes that
    detectable without ever storing a comparable account number.

    IFSC and bank name are public routing information, not PII, so they stay in
    plaintext where they can be indexed and validated.
    """

    worker = models.OneToOneField(
        Worker, on_delete=models.CASCADE, related_name="bank_account"
    )
    account_number_encrypted = models.BinaryField(null=True, blank=True)
    # Not UNIQUE: relatives legitimately share an account. Indexed so the
    # collision can be surfaced for review rather than silently blocked.
    account_number_hash = models.BinaryField(null=True, blank=True, db_index=True)

    ifsc = models.CharField(max_length=11, blank=True, default="", db_index=True)
    bank_name = models.CharField(max_length=120, blank=True, default="")
    account_holder_name = models.CharField(max_length=150, blank=True, default="")

    # Cancelled cheque or passbook page, in the private bucket.
    storage_key = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "worker_bank_accounts"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"Bank account · {self.worker.name}"

    def set_account_number(self, number: str | None) -> None:
        from . import crypto

        digits = "".join(ch for ch in (number or "") if ch.isdigit())
        self.account_number_encrypted = crypto.encrypt(digits)
        self.account_number_hash = crypto.blind_index(digits)

    @property
    def account_number(self) -> str | None:
        from . import crypto

        return crypto.decrypt(self.account_number_encrypted)

    def shared_with(self):
        """Other workers registered against this same account number."""
        if not self.account_number_hash:
            return WorkerBankAccount.objects.none()
        return (
            WorkerBankAccount.objects.filter(account_number_hash=self.account_number_hash)
            .exclude(pk=self.pk)
            .select_related("worker")
        )


class CandidateNameToken(models.Model):
    """Searchable-encryption side table for candidate names.

    Searching an encrypted ``name`` with ILIKE would have to decrypt every row,
    which defeats live filtering. Instead we store keyed HMAC digests of each
    name token and of its 3..12 character prefixes, so "raj" finds "Rajesh"
    through a plain indexed equality lookup with no decryption at all.

    Accepted trade-offs: prefix/whole-token matching only (no infix search), and
    an attacker holding the database but not PII_BLIND_INDEX_KEY can tell that
    two candidates share a name prefix without learning the name.
    """

    profile = models.ForeignKey(
        CandidateProfile, on_delete=models.CASCADE, related_name="name_tokens"
    )
    token_hash = models.BinaryField()

    class Meta:
        db_table = "candidate_name_tokens"
        indexes = [models.Index(fields=["profile"])]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"name token · profile#{self.profile_id}"


class Skill(models.Model):
    """Canonical, de-duplicated skill vocabulary (stored lowercase). Not PII."""

    name = models.CharField(max_length=80, unique=True)

    class Meta:
        db_table = "skills"
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    @classmethod
    def normalise(cls, raw: str) -> str:
        return " ".join((raw or "").strip().lower().split())[:80]

    @classmethod
    def get_or_create_normalised(cls, raw: str) -> "Skill | None":
        name = cls.normalise(raw)
        if not name:
            return None
        skill, _ = cls.objects.get_or_create(name=name)
        return skill


class CandidateSkill(models.Model):
    """Many-to-many join between a candidate profile and the skill vocabulary."""

    profile = models.ForeignKey(
        CandidateProfile, on_delete=models.CASCADE, related_name="candidate_skills"
    )
    skill = models.ForeignKey(
        Skill, on_delete=models.CASCADE, related_name="candidate_skills"
    )

    class Meta:
        db_table = "candidate_skills"
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "skill"], name="uq_candidate_skill"
            )
        ]
        indexes = [models.Index(fields=["skill", "profile"])]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.skill.name} @ profile#{self.profile_id}"
