"""
Domain models for the Factory Gate-Intake platform.

The data model mirrors the PostgreSQL DDL in ``sql/schema.sql`` one-to-one. The single
most important piece of business logic lives on ``Worker.evaluate_compliance`` /
``Worker.compliance_against_project`` which decides whether a worker may be
deployed to a given project and, if not, *exactly* why.
"""
from __future__ import annotations

from datetime import timedelta

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
        FIELD_OFFICER = "FIELD_OFFICER", "Field Officer"
        GATE_SECURITY = "GATE_SECURITY", "Gate Security"

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=Role.choices)
    # Contractors and field officers belong to a vendor/company label (free text
    # for this MVP). PE + Gate belong to the factory.
    organization = models.CharField(max_length=150, blank=True, default="")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.email} ({self.get_role_display()})"


# ---------------------------------------------------------------------------
# Requirements catalogue
# ---------------------------------------------------------------------------
class RequirementMaster(models.Model):
    """A document type that a worker may be required to hold (Aadhar, PAN, ...)."""

    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=255, blank=True, default="")
    # Expirable requirements (e.g. Safety Training) are only "Verified" while the
    # attached document's expiry_date is still in the future.
    is_expirable = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

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
    """Junction: which requirements a given project demands."""

    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="project_requirements"
    )
    requirement = models.ForeignKey(
        RequirementMaster, on_delete=models.CASCADE, related_name="in_projects"
    )
    is_mandatory = models.BooleanField(default=True)

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

        # Pull the project's mandatory requirements once.
        required = list(
            project.project_requirements.filter(is_mandatory=True).select_related(
                "requirement"
            )
        )

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

        # Merge in the 5-pillar intake checks (medical, police, videos). These are
        # global to the worker, not project-specific, but a failure in any of them
        # must also block deployment — so they surface as gaps here too.
        intake_gaps, intake_satisfied = self._intake_status(today)
        gaps.extend(intake_gaps)
        satisfied.extend(intake_satisfied)

        return {
            "worker_id": self.id,
            "is_compliant": len(gaps) == 0,
            "satisfied": satisfied,
            "gaps": gaps,
        }

    def _intake_status(self, today) -> tuple[list[dict], list[dict]]:
        """Evaluate the medical / police / video pillars for this worker.

        Returns ``(gaps, satisfied)`` where each entry is a dict tagged with
        ``kind="intake"`` and a ``pillar`` so the UI can render an explanation
        (these have no uploadable document slot).
        """
        gaps: list[dict] = []
        satisfied: list[dict] = []

        def add(pillar, name, ok, reason=None, detail=""):
            if ok:
                satisfied.append({"pillar": pillar, "requirement_name": name})
            else:
                gaps.append(
                    {
                        "kind": "intake",
                        "pillar": pillar,
                        "requirement_id": None,
                        "requirement_name": name,
                        "reason": reason,
                        "detail": detail,
                    }
                )

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

        return gaps, satisfied

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

        Gate security grants entry only when the worker appears on at least one
        PE-approved deployment list.
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
    # We store an uploaded file, exposing its URL. file_url mirrors the DDL column
    # and is populated from the FileField for API consumers.
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
    # The scanned document the Field Officer verified against, on the spot.
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
