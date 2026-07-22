"""
API views for the Gate-Intake platform.

Endpoints (all under /api/):
  POST   auth/login/                          -> token + persona
  GET    me/                                  -> current user
  GET    projects/                            -> list projects (role scoped)
  POST   projects/                            -> PE creates a project
  GET    projects/<id>/                       -> project detail
  GET    projects/<id>/eligible-workers/      -> compliance split for a contractor
  POST   workers/bulk-upload/                 -> Field Officer CSV/Excel import
  POST   documents/upload/                    -> Contractor inline document upload
  GET/POST intake-lists/                      -> list / create (submit) a list
  PATCH  intake-lists/<id>/review/            -> PE approve / request changes / reject
  GET    gate-check/?aadhar=<n>               -> gate security lookup
"""
from __future__ import annotations

import csv
import io
import os
import re
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import api_view, permission_classes
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    INTAKE_EXPIRY_DAYS,
    IntakeList,
    IntakeListWorker,
    IntakeMedicalRecord,
    IntakePoliceVerification,
    Project,
    RequirementMaster,
    SafetyTrainingProgress,
    TradeTestAttempt,
    TradeTestQuestion,
    User,
    Worker,
    WorkerDocument,
    category_for_skill,
)
from .serializers import (
    IntakeListSerializer,
    IntakeMedicalRecordSerializer,
    IntakePoliceVerificationSerializer,
    ProjectSerializer,
    RequirementMasterSerializer,
    TradeTestQuestionSerializer,
    UserSerializer,
    WorkerDocumentSerializer,
    WorkerSerializer,
)

TRADE_TEST_QUESTION_COUNT = 5
TRADE_TEST_PASS_MARK = 3
TRADE_TEST_MAX_ATTEMPTS = 3


# ---------------------------------------------------------------------------
# Small role helpers
# ---------------------------------------------------------------------------
def _require_role(request, *roles) -> Response | None:
    """Return a 403 Response if the user's role is not in ``roles``; else None."""
    if request.user.role not in roles:
        return Response(
            {
                "detail": f"This action requires role(s): {', '.join(roles)}. "
                f"You are '{request.user.role}'."
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    return None


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginView(APIView):
    """Email/password login returning a DRF token and the persona payload.

    The frontend role-switcher simply calls this with each hardcoded credential
    to obtain that persona's token, then masquerades by swapping the active token.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password") or ""

        user = User.objects.filter(email=email).first()
        if user is None or not user.check_password(password):
            return Response(
                {"detail": "Invalid credentials."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        token, _ = Token.objects.get_or_create(user=user)
        return Response(
            {
                "token": token.key,
                "user": UserSerializer(user).data,
            }
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me(request):
    return Response(UserSerializer(request.user).data)


# ---------------------------------------------------------------------------
# Requirements catalogue
# ---------------------------------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def requirements(request):
    """GET /api/requirements/ — the full master requirement catalogue.

    Used by the contractor's checkbox filter to search workers by which
    requirements they have fulfilled, independent of any project's mandatory set.
    """
    qs = RequirementMaster.objects.all()
    return Response(RequirementMasterSerializer(qs, many=True).data)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
class ProjectListCreateView(APIView):
    def get(self, request):
        user = request.user
        if user.role == User.Role.PRINCIPAL_EMPLOYER:
            qs = Project.objects.filter(principal_employer=user)
        elif user.role == User.Role.CONTRACTOR:
            qs = Project.objects.filter(contractors=user)
        else:
            qs = Project.objects.all()
        qs = qs.prefetch_related("project_requirements__requirement", "contractors")
        return Response(ProjectSerializer(qs, many=True).data)

    def post(self, request):
        denied = _require_role(request, User.Role.PRINCIPAL_EMPLOYER)
        if denied:
            return denied
        serializer = ProjectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = serializer.save(principal_employer=request.user)
        return Response(
            ProjectSerializer(project).data, status=status.HTTP_201_CREATED
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def project_detail(request, pk):
    project = get_object_or_404(
        Project.objects.prefetch_related("project_requirements__requirement"), pk=pk
    )
    return Response(ProjectSerializer(project).data)


class EligibleWorkersView(APIView):
    """
    GET /api/projects/<id>/eligible-workers/

    Evaluates the project's mandatory requirements against the calling
    contractor's assigned workers and returns a structured split:

      {
        "project": {...},
        "required_documents": [...],
        "summary": {"total": 8, "ready": 5, "needs_fixes": 3},
        "ready_to_deploy": [ {worker + compliance}, ... ],
        "needs_fixes":     [ {worker + compliance (with explicit gaps)}, ... ]
      }
    """

    def get(self, request, pk):
        project = get_object_or_404(
            Project.objects.prefetch_related("project_requirements__requirement"),
            pk=pk,
        )

        # Contractors only see their own workers. PE/Field officer can inspect all.
        if request.user.role == User.Role.CONTRACTOR:
            workers_qs = Worker.objects.filter(contractor=request.user)
        else:
            contractor_id = request.query_params.get("contractor_id")
            workers_qs = Worker.objects.all()
            if contractor_id:
                workers_qs = workers_qs.filter(contractor_id=contractor_id)

        # Prefetch all pillar relations so each compliance evaluation avoids N+1.
        workers_qs = workers_qs.select_related("safety_video").prefetch_related(
            "documents", "medical_records", "police_verifications", "trade_test_attempts"
        )

        required = [
            {
                "requirement_id": pr.requirement.id,
                "requirement_name": pr.requirement.name,
                "is_expirable": pr.requirement.is_expirable,
            }
            for pr in project.project_requirements.filter(is_mandatory=True)
        ]

        ready, needs_fixes = [], []
        for worker in workers_qs:
            compliance = worker.compliance_against_project(project)
            payload = {
                "worker": WorkerSerializer(worker).data,
                "compliance": compliance,
            }
            (ready if compliance["is_compliant"] else needs_fixes).append(payload)

        return Response(
            {
                "project": ProjectSerializer(project).data,
                "required_documents": required,
                "summary": {
                    "total": len(ready) + len(needs_fixes),
                    "ready": len(ready),
                    "needs_fixes": len(needs_fixes),
                },
                "ready_to_deploy": ready,
                "needs_fixes": needs_fixes,
            }
        )


# ---------------------------------------------------------------------------
# Workers — Field Officer bulk upload
# ---------------------------------------------------------------------------
class WorkerBulkUploadView(APIView):
    """
    POST /api/workers/bulk-upload/   (Field Officer only)

    Accepts a CSV (or Excel .xlsx) file under the form field ``file`` with columns:
        name, aadhar_number, skill_type   (optional: contractor_email)

    Idempotent-ish: existing Aadhar numbers are reported as skipped, never
    duplicated, honouring the UNIQUE constraint.
    """

    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        denied = _require_role(request, User.Role.FIELD_OFFICER)
        if denied:
            return denied

        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"detail": "No file provided under form field 'file'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            rows = self._parse_rows(upload)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        created, skipped, errors = [], [], []
        contractor_cache: dict[str, User | None] = {}

        for idx, row in enumerate(rows, start=2):  # row 1 is the header
            name = (row.get("name") or "").strip()
            aadhar = (row.get("aadhar_number") or "").strip()
            skill = (row.get("skill_type") or "").strip()
            contractor_email = (row.get("contractor_email") or "").strip().lower()

            if not (name and aadhar and skill):
                errors.append({"row": idx, "error": "Missing name/aadhar/skill."})
                continue
            if len(aadhar) != 12 or not aadhar.isdigit():
                errors.append({"row": idx, "error": f"Invalid Aadhar '{aadhar}'."})
                continue

            contractor = None
            if contractor_email:
                if contractor_email not in contractor_cache:
                    contractor_cache[contractor_email] = User.objects.filter(
                        email=contractor_email, role=User.Role.CONTRACTOR
                    ).first()
                contractor = contractor_cache[contractor_email]

            try:
                with transaction.atomic():
                    worker = Worker.objects.create(
                        name=name,
                        aadhar_number=aadhar,
                        skill_type=skill,
                        contractor=contractor,
                    )
                created.append({"row": idx, "id": worker.id, "aadhar": aadhar})
            except IntegrityError:
                skipped.append({"row": idx, "aadhar": aadhar, "reason": "duplicate"})

        return Response(
            {
                "created_count": len(created),
                "skipped_count": len(skipped),
                "error_count": len(errors),
                "created": created,
                "skipped": skipped,
                "errors": errors,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @staticmethod
    def _parse_rows(upload) -> list[dict]:
        """Return a list of dict rows from a CSV or XLSX upload."""
        filename = (upload.name or "").lower()

        if filename.endswith(".xlsx"):
            try:
                from openpyxl import load_workbook
            except ImportError as exc:  # pragma: no cover
                raise ValueError("openpyxl not installed for .xlsx parsing.") from exc

            wb = load_workbook(upload, read_only=True, data_only=True)
            ws = wb.active
            rows_iter = ws.iter_rows(values_only=True)
            try:
                header = [str(h).strip().lower() if h else "" for h in next(rows_iter)]
            except StopIteration:
                return []
            result = []
            for values in rows_iter:
                result.append(
                    {header[i]: values[i] for i in range(len(header)) if i < len(values)}
                )
            return result

        # Default: CSV
        decoded = upload.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(decoded))
        normalized = []
        for raw in reader:
            normalized.append({(k or "").strip().lower(): v for k, v in raw.items()})
        return normalized


class WorkerListView(APIView):
    """
    GET  /api/workers/  — role-scoped worker registry (used by dashboards).
    POST /api/workers/  — Field Officer creates a single worker from scratch.
    """

    def get(self, request):
        if request.user.role == User.Role.CONTRACTOR:
            qs = Worker.objects.filter(contractor=request.user)
        else:
            qs = Worker.objects.all()
        qs = qs.select_related("safety_video").prefetch_related(
            "documents", "trade_test_attempts"
        )
        return Response(WorkerSerializer(qs, many=True).data)

    def post(self, request):
        denied = _require_role(request, User.Role.FIELD_OFFICER)
        if denied:
            return denied

        name = (request.data.get("name") or "").strip()
        aadhar = (request.data.get("aadhar_number") or "").strip()
        skill = (request.data.get("skill_type") or "").strip()
        contractor_id = request.data.get("contractor")

        if not (name and aadhar and skill):
            return Response(
                {"detail": "name, aadhar_number and skill_type are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(aadhar) != 12 or not aadhar.isdigit():
            return Response(
                {"detail": "Aadhar number must be exactly 12 digits."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contractor = None
        if contractor_id:
            contractor = User.objects.filter(
                id=contractor_id, role=User.Role.CONTRACTOR
            ).first()
            if contractor is None:
                return Response(
                    {"detail": "Assigned contractor not found."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            worker = Worker.objects.create(
                name=name,
                aadhar_number=aadhar,
                skill_type=skill,
                contractor=contractor,
            )
        except IntegrityError:
            return Response(
                {"detail": f"A worker with Aadhar {aadhar} already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            WorkerSerializer(worker).data, status=status.HTTP_201_CREATED
        )


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def contractors(request):
    """GET /api/contractors/ — list contractors, for the Field Officer's
    'assign worker to contractor' picker."""
    qs = User.objects.filter(role=User.Role.CONTRACTOR).order_by("email")
    return Response(
        [
            {"id": u.id, "email": u.email, "organization": u.organization}
            for u in qs
        ]
    )


class VerificationStatusView(APIView):
    """
    GET /api/verification-status/   (Field Officer)

    A whole-registry verification matrix: one row per worker with the status of
    every verification type and a link to each uploaded document, so the officer
    can see at a glance what is verified vs remaining, and re-open any scan.
    """

    # status values are grouped into "done" vs "remaining" for the summary count.
    _DONE = {"VERIFIED", "PASSED"}

    def get(self, request):
        denied = _require_role(request, User.Role.FIELD_OFFICER)
        if denied:
            return denied

        today = timezone.now().date()
        reqs = {r.name: r for r in RequirementMaster.objects.all()}

        workers = (
            Worker.objects.select_related("contractor", "safety_video")
            .prefetch_related("documents", "medical_records", "police_verifications")
            .order_by("name")
        )

        def doc_link(obj):
            f = getattr(obj, "document_file", None)
            if f:
                try:
                    return request.build_absolute_uri(f.url)
                except ValueError:
                    pass
            return getattr(obj, "file_url", "") or None

        rows = []
        for w in workers:
            # Most-recent document per requirement.
            latest_doc = {}
            for d in sorted(w.documents.all(), key=lambda x: x.uploaded_at):
                latest_doc[d.requirement_id] = d

            items = []

            # --- Document requirements: Aadhaar, PAN, Safety Cert ---
            for name, label in (("Aadhar", "Aadhaar"), ("PAN", "PAN"),
                                 ("Safety Training", "Safety Cert")):
                req = reqs.get(name)
                d = latest_doc.get(req.id) if req else None
                if d is None:
                    items.append({"key": name, "label": label, "status": "MISSING", "doc_url": None})
                    continue
                st = d.verification_status.upper()  # VERIFIED / PENDING / REJECTED
                if (st == "VERIFIED" and req.is_expirable and d.expiry_date
                        and d.expiry_date < today):
                    st = "EXPIRED"
                items.append({"key": name, "label": label, "status": st, "doc_url": doc_link(d)})

            # --- Medical ---
            med = max(w.medical_records.all(), key=lambda m: m.exam_date, default=None)
            if med is None:
                m_st = "MISSING"
            elif med.expiry_date and med.expiry_date < today:
                m_st = "EXPIRED"
            elif med.color_blindness or med.vertigo:
                m_st = "FAILED"
            else:
                m_st = "VERIFIED"
            items.append({"key": "MEDICAL", "label": "Medical", "status": m_st,
                          "doc_url": doc_link(med) if med else None})

            # --- Police verification ---
            pol = max(w.police_verifications.all(), key=lambda p: p.issue_date, default=None)
            if pol is None:
                p_st = "MISSING"
            elif pol.verification_status != WorkerDocument.Status.VERIFIED:
                p_st = "PENDING"
            elif pol.expiry_date and pol.expiry_date < today:
                p_st = "EXPIRED"
            else:
                p_st = "VERIFIED"
            items.append({"key": "POLICE", "label": "Police", "status": p_st,
                          "doc_url": doc_link(pol) if pol else None})

            # --- Trade test (no document) ---
            items.append({"key": "TRADE_TEST", "label": "Trade Test",
                          "status": w.trade_test_status, "doc_url": None})

            # --- Safety video (no document) ---
            try:
                sv = w.safety_video
            except SafetyTrainingProgress.DoesNotExist:
                sv = None
            items.append({"key": "SAFETY_VIDEO", "label": "Safety Video",
                          "status": "VERIFIED" if (sv and sv.is_completed) else "INCOMPLETE",
                          "doc_url": None})

            remaining = sum(1 for it in items if it["status"] not in self._DONE)
            rows.append({
                "id": w.id,
                "name": w.name,
                "skill_type": w.skill_type,
                "aadhar_number": w.aadhar_number,
                "contractor_email": w.contractor.email if w.contractor else None,
                "items": items,
                "remaining": remaining,
                "all_verified": remaining == 0,
            })

        return Response(rows)


# ---------------------------------------------------------------------------
# Documents — Contractor inline upload
# ---------------------------------------------------------------------------
class DocumentUploadView(APIView):
    """
    POST /api/documents/upload/   (Contractor)

    Creates or updates the document for a (worker, requirement) slot. Uploading a
    fresh document resets its status to 'Pending' and clears any prior rejection.

    Form fields: worker, requirement, document_number, expiry_date, file / file_url
    """

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        denied = _require_role(request, User.Role.CONTRACTOR)
        if denied:
            return denied

        worker_id = request.data.get("worker")
        requirement_id = request.data.get("requirement")
        if not worker_id or not requirement_id:
            return Response(
                {"detail": "'worker' and 'requirement' are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        worker = get_object_or_404(Worker, pk=worker_id)
        # Contractors may only touch their own workers.
        if worker.contractor_id != request.user.id:
            return Response(
                {"detail": "You may only upload documents for your own workers."},
                status=status.HTTP_403_FORBIDDEN,
            )
        requirement = get_object_or_404(RequirementMaster, pk=requirement_id)

        # One document slot per (worker, requirement): update in place if present.
        doc, _created = WorkerDocument.objects.get_or_create(
            worker=worker, requirement=requirement
        )
        doc.document_number = request.data.get("document_number", doc.document_number)
        doc.expiry_date = request.data.get("expiry_date") or doc.expiry_date

        if "file" in request.FILES:
            doc.document_file = request.FILES["file"]
            doc.file_url = ""  # served via document_file.url henceforth
        elif request.data.get("file_url"):
            doc.file_url = request.data["file_url"]

        # A re-upload always re-enters the verification queue.
        doc.verification_status = WorkerDocument.Status.PENDING
        doc.rejection_reason = ""
        doc.save()

        return Response(
            WorkerDocumentSerializer(doc).data, status=status.HTTP_200_OK
        )


class DocumentReviewView(APIView):
    """
    PATCH /api/documents/<id>/review/   (PE / Field Officer verifier)

    Body: {"verification_status": "Verified"|"Rejected", "rejection_reason": "..."}
    Provided so the compliance engine has verified documents to work with.
    """

    def patch(self, request, pk):
        denied = _require_role(
            request, User.Role.PRINCIPAL_EMPLOYER, User.Role.FIELD_OFFICER
        )
        if denied:
            return denied
        doc = get_object_or_404(WorkerDocument, pk=pk)
        new_status = request.data.get("verification_status")
        if new_status not in WorkerDocument.Status.values:
            return Response(
                {"detail": f"Invalid status. Use one of {WorkerDocument.Status.values}."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        doc.verification_status = new_status
        doc.rejection_reason = (
            request.data.get("rejection_reason", "")
            if new_status == WorkerDocument.Status.REJECTED
            else ""
        )
        doc.save()
        return Response(WorkerDocumentSerializer(doc).data)


# ---------------------------------------------------------------------------
# Intake lists — submit & review
# ---------------------------------------------------------------------------
class IntakeListView(APIView):
    """
    GET  /api/intake-lists/            -> role-scoped lists
    POST /api/intake-lists/            -> Contractor submits a finalized list

    POST body:
      {"project": <id>, "worker_ids": [1,2,3], "submit": true}
    When submit is true the list goes straight to 'Submitted'; otherwise 'Draft'.
    Only fully-compliant workers are accepted onto a submitted list.
    """

    def get(self, request):
        user = request.user
        if user.role == User.Role.CONTRACTOR:
            qs = IntakeList.objects.filter(contractor=user)
        elif user.role == User.Role.PRINCIPAL_EMPLOYER:
            qs = IntakeList.objects.filter(project__principal_employer=user)
        else:
            qs = IntakeList.objects.all()
        qs = qs.select_related("project", "contractor").prefetch_related(
            "list_workers__worker__documents"
        )
        return Response(IntakeListSerializer(qs, many=True).data)

    def post(self, request):
        denied = _require_role(request, User.Role.CONTRACTOR)
        if denied:
            return denied

        project = get_object_or_404(Project, pk=request.data.get("project"))
        worker_ids = request.data.get("worker_ids") or []
        submit = bool(request.data.get("submit", True))

        if not worker_ids:
            return Response(
                {"detail": "worker_ids must contain at least one worker."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        workers = list(
            Worker.objects.filter(
                id__in=worker_ids, contractor=request.user
            ).prefetch_related("documents")
        )
        if len(workers) != len(set(worker_ids)):
            return Response(
                {"detail": "Some workers were not found or are not yours."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Guard: on submission every worker must be fully compliant.
        if submit:
            non_compliant = [
                w.name
                for w in workers
                if not w.compliance_against_project(project)["is_compliant"]
            ]
            if non_compliant:
                return Response(
                    {
                        "detail": "Cannot submit: these workers are not compliant.",
                        "non_compliant_workers": non_compliant,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            intake_list = IntakeList.objects.create(
                project=project,
                contractor=request.user,
                status=IntakeList.Status.SUBMITTED
                if submit
                else IntakeList.Status.DRAFT,
                submitted_at=timezone.now() if submit else None,
            )
            IntakeListWorker.objects.bulk_create(
                [
                    IntakeListWorker(intake_list=intake_list, worker=w)
                    for w in workers
                ]
            )

        return Response(
            IntakeListSerializer(intake_list).data, status=status.HTTP_201_CREATED
        )


class IntakeListDetailView(APIView):
    """
    Contractor-owned edit / resubmit of a single list (the revise-in-place loop).

    GET   /api/intake-lists/<id>/           -> detail
    PATCH /api/intake-lists/<id>/           -> edit roster and/or resubmit

    PATCH body (all optional):
      {"worker_ids": [1,2], "submit": true}
    Only 'Draft' or 'Revision_Requested' lists may be edited. Omitting
    worker_ids keeps the existing roster (typical after fixing documents).
    Resubmitting flips the SAME list back to 'Submitted' and clears the prior
    PE verdict so it re-enters review as the same list id.
    """

    EDITABLE = {IntakeList.Status.DRAFT, IntakeList.Status.REVISION_REQUESTED}

    def get(self, request, pk):
        intake_list = get_object_or_404(
            IntakeList.objects.select_related("project", "contractor").prefetch_related(
                "list_workers__worker__documents"
            ),
            pk=pk,
        )
        return Response(IntakeListSerializer(intake_list).data)

    def patch(self, request, pk):
        denied = _require_role(request, User.Role.CONTRACTOR)
        if denied:
            return denied

        intake_list = get_object_or_404(
            IntakeList.objects.select_related("project"), pk=pk
        )
        if intake_list.contractor_id != request.user.id:
            return Response(
                {"detail": "You can only edit your own lists."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if intake_list.status not in self.EDITABLE:
            return Response(
                {
                    "detail": f"Only Draft or Revision_Requested lists can be edited "
                    f"(this list is '{intake_list.status}')."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        project = intake_list.project
        resubmit = bool(request.data.get("submit", True))

        # Roster: use provided worker_ids, else keep the existing membership.
        if "worker_ids" in request.data:
            worker_ids = request.data.get("worker_ids") or []
            if not worker_ids:
                return Response(
                    {"detail": "worker_ids must contain at least one worker."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            workers = list(
                Worker.objects.filter(
                    id__in=worker_ids, contractor=request.user
                ).prefetch_related("documents")
            )
            if len(workers) != len(set(worker_ids)):
                return Response(
                    {"detail": "Some workers were not found or are not yours."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            workers = [ilw.worker for ilw in intake_list.list_workers.all()]

        # On resubmission every worker must (again) be fully compliant.
        if resubmit:
            non_compliant = [
                w.name
                for w in workers
                if not w.compliance_against_project(project)["is_compliant"]
            ]
            if non_compliant:
                return Response(
                    {
                        "detail": "Cannot resubmit: these workers are still not compliant.",
                        "non_compliant_workers": non_compliant,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        with transaction.atomic():
            if "worker_ids" in request.data:
                intake_list.list_workers.all().delete()
                IntakeListWorker.objects.bulk_create(
                    [
                        IntakeListWorker(intake_list=intake_list, worker=w)
                        for w in workers
                    ]
                )
            if resubmit:
                intake_list.status = IntakeList.Status.SUBMITTED
                intake_list.submitted_at = timezone.now()
                # Clear the previous verdict so it is a fresh review.
                intake_list.pe_comments = ""
                intake_list.reviewed_at = None
            intake_list.save()

        return Response(IntakeListSerializer(intake_list).data)


class IntakeListReviewView(APIView):
    """
    PATCH /api/intake-lists/<id>/review/   (PE only)

    Body: {"action": "approve"|"request_changes"|"reject", "comments": "..."}
    """

    ACTION_STATUS = {
        "approve": IntakeList.Status.APPROVED,
        "request_changes": IntakeList.Status.REVISION_REQUESTED,
        "reject": IntakeList.Status.REJECTED,
    }

    def patch(self, request, pk):
        denied = _require_role(request, User.Role.PRINCIPAL_EMPLOYER)
        if denied:
            return denied

        intake_list = get_object_or_404(
            IntakeList.objects.select_related("project"), pk=pk
        )
        if intake_list.project.principal_employer_id != request.user.id:
            return Response(
                {"detail": "You can only review lists for your own projects."},
                status=status.HTTP_403_FORBIDDEN,
            )

        action = request.data.get("action")
        if action not in self.ACTION_STATUS:
            return Response(
                {"detail": f"action must be one of {list(self.ACTION_STATUS)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        intake_list.status = self.ACTION_STATUS[action]
        intake_list.pe_comments = request.data.get("comments", "")
        intake_list.reviewed_at = timezone.now()
        intake_list.save()
        return Response(IntakeListSerializer(intake_list).data)


# ---------------------------------------------------------------------------
# Gate security quick lookup
# ---------------------------------------------------------------------------
class GateCheckView(APIView):
    """
    GET /api/gate-check/?aadhar=<number>   (Gate Security)

    Returns a simple GREEN/RED decision based on whether the worker sits on any
    PE-approved intake list.
    """

    def get(self, request):
        aadhar = (request.query_params.get("aadhar") or "").strip()
        if not aadhar:
            return Response(
                {"detail": "Provide ?aadhar=<number>."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        worker = (
            Worker.objects.filter(aadhar_number=aadhar)
            .prefetch_related("documents")
            .first()
        )
        if worker is None:
            return Response(
                {
                    "access": "DENIED",
                    "reason": "No worker found for this Aadhar number.",
                    "worker": None,
                }
            )

        approved_list = worker.is_gate_cleared()
        if approved_list is not None:
            return Response(
                {
                    "access": "GRANTED",
                    "reason": "Worker is on an approved deployment list.",
                    "worker": {
                        "id": worker.id,
                        "name": worker.name,
                        "skill_type": worker.skill_type,
                        "aadhar_number": worker.aadhar_number,
                    },
                    "project": approved_list.project.name,
                    "list_id": approved_list.id,
                }
            )

        return Response(
            {
                "access": "DENIED",
                "reason": "Worker is not on any approved deployment list.",
                "worker": {
                    "id": worker.id,
                    "name": worker.name,
                    "skill_type": worker.skill_type,
                    "aadhar_number": worker.aadhar_number,
                },
            }
        )


# ---------------------------------------------------------------------------
# Field Officer Intake Workbench — mock OCR, strict verification, video heartbeat
# ---------------------------------------------------------------------------
def _iso(d):
    return d.isoformat() if d else None


class MockOcrView(APIView):
    """
    GET /api/intake/mock-ocr/?sample=<key>   (Field Officer)

    Simulates an OCR engine. Returns a mock extraction for the chosen sample
    document, including a ``form_type`` that tells the workbench which right-pane
    form to render. Dates are computed relative to *today* so samples stay
    meaningful over time (the "expired" sample is always ~400 days old).
    """

    def get(self, request):
        denied = _require_role(request, User.Role.FIELD_OFFICER)
        if denied:
            return denied

        today = timezone.now().date()
        sample = (request.query_params.get("sample") or "aadhar_clean").strip()

        samples = {
            "aadhar_clean": {
                "form_type": "IDENTITY",
                "label": "Clean Aadhar Card",
                "requirement_name": "Aadhar",
                "fields": {
                    "name": "Ravi Kumar",
                    "aadhar_number": "100000000001",
                    "dob": "1990-05-14",
                    "gender": "Male",
                    "address": "12, Industrial Area, Pune",
                },
            },
            "medical_expired": {
                "form_type": "MEDICAL",
                "label": "Expired Medical Form",
                "requirement_name": "Medical Exam",
                "fields": {
                    "exam_date": _iso(today - timedelta(days=400)),  # already expired
                    "color_blindness": False,
                    "vision": "6/9",
                    "vertigo": False,
                    "blood_type": "B+",
                },
            },
            "pvc_valid": {
                "form_type": "POLICE",
                "label": "Valid Police Verification (PVC)",
                "requirement_name": "Police Verification",
                "fields": {
                    "certificate_number": "PVC-2026-8842",
                    "issue_date": _iso(today - timedelta(days=30)),  # valid
                    "verification_status": "Verified",
                },
            },
        }

        data = samples.get(sample)
        if data is None:
            return Response(
                {"detail": f"Unknown sample '{sample}'.", "available": list(samples)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"sample": sample, **data})


class VerifyDocumentView(APIView):
    """
    POST /api/intake/verify-document/   (Field Officer)

    Strict verification-saving endpoint. For MEDICAL / POLICE it enforces that the
    exam/issue date is not older than 1 year and writes ``expiry_date`` exactly
    365 days out (the model does this on save). For IDENTITY it marks the named
    WorkerDocument as Verified.

    Accepts either JSON or multipart/form-data. When multipart, the scanned
    document is read from the ``file`` field and stored on the record — so the
    Field Officer can upload the physical document and verify it on the spot.

    Body: {"worker": <id>, "doc_type": "MEDICAL"|"POLICE"|"IDENTITY", ...fields}
    """

    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @staticmethod
    def _as_bool(value):
        # Multipart sends booleans as strings ("true"/"false"); coerce safely.
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def post(self, request):
        denied = _require_role(request, User.Role.FIELD_OFFICER)
        if denied:
            return denied

        worker = get_object_or_404(Worker, pk=request.data.get("worker"))
        doc_type = (request.data.get("doc_type") or "").upper()
        upload = request.FILES.get("file")
        today = timezone.now().date()

        def check_not_expired(date_str, field_label):
            """Return (date, error_response). Rejects dates > 365 days old."""
            if not date_str:
                return None, Response(
                    {"detail": f"{field_label} is required."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                d = timezone.datetime.fromisoformat(str(date_str)).date()
            except ValueError:
                return None, Response(
                    {"detail": f"{field_label} is not a valid ISO date."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if (today - d).days > INTAKE_EXPIRY_DAYS:
                return None, Response(
                    {
                        "detail": f"Rejected: {field_label} ({d.isoformat()}) is more "
                        f"than {INTAKE_EXPIRY_DAYS} days old — the document is already "
                        f"expired.",
                        "expired": True,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return d, None

        if doc_type == "MEDICAL":
            exam_date, err = check_not_expired(
                request.data.get("exam_date"), "Medical exam date"
            )
            if err:
                return err
            rec, _ = IntakeMedicalRecord.objects.update_or_create(
                worker=worker,
                exam_date=exam_date,
                defaults={
                    "color_blindness": self._as_bool(
                        request.data.get("color_blindness", False)
                    ),
                    "vision": request.data.get("vision", ""),
                    "vertigo": self._as_bool(request.data.get("vertigo", False)),
                    "blood_type": request.data.get("blood_type", ""),
                },
            )  # expiry_date computed in model.save()
            if upload:
                rec.document_file = upload
                rec.save()
            return Response(
                IntakeMedicalRecordSerializer(rec).data, status=status.HTTP_201_CREATED
            )

        if doc_type == "POLICE":
            issue_date, err = check_not_expired(
                request.data.get("issue_date"), "Police verification issue date"
            )
            if err:
                return err
            rec, _ = IntakePoliceVerification.objects.update_or_create(
                worker=worker,
                issue_date=issue_date,
                defaults={
                    "certificate_number": request.data.get("certificate_number", ""),
                    "verification_status": request.data.get(
                        "verification_status", WorkerDocument.Status.VERIFIED
                    ),
                },
            )
            if upload:
                rec.document_file = upload
                rec.save()
            return Response(
                IntakePoliceVerificationSerializer(rec).data,
                status=status.HTTP_201_CREATED,
            )

        if doc_type == "IDENTITY":
            requirement_name = request.data.get("requirement_name", "Aadhar")
            requirement = RequirementMaster.objects.filter(
                name=requirement_name
            ).first()
            if requirement is None:
                return Response(
                    {"detail": f"No requirement named '{requirement_name}'."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            defaults = {
                "document_number": request.data.get("document_number", ""),
                "verification_status": WorkerDocument.Status.VERIFIED,
                "rejection_reason": "",
            }
            # Expirable identity docs (e.g. Safety Training) may carry an expiry.
            expiry = request.data.get("expiry_date")
            if expiry:
                defaults["expiry_date"] = expiry
            doc, _ = WorkerDocument.objects.update_or_create(
                worker=worker,
                requirement=requirement,
                defaults=defaults,
            )
            if upload:
                doc.document_file = upload
                doc.file_url = ""
                doc.save()
            return Response(
                WorkerDocumentSerializer(doc).data, status=status.HTTP_201_CREATED
            )

        return Response(
            {"detail": "doc_type must be MEDICAL, POLICE or IDENTITY."},
            status=status.HTTP_400_BAD_REQUEST,
        )


# ---------------------------------------------------------------------------
# Trade test — Field-Officer-administered practical MCQ exam
# ---------------------------------------------------------------------------
def _trade_test_state(worker):
    """Current attempt bookkeeping for a worker."""
    attempts_used = worker.trade_test_attempts.count()
    return {
        "attempts_used": attempts_used,
        "attempts_remaining": max(0, TRADE_TEST_MAX_ATTEMPTS - attempts_used),
        "status": worker.trade_test_status,
        "passed": worker.trade_test_status == Worker.TradeTestStatus.PASSED,
        "locked": worker.trade_test_status == Worker.TradeTestStatus.FAILED,
    }


class TradeTestStartView(APIView):
    """
    GET /api/trade-test/start/?worker_id=<id>   (Field Officer)

    Validates the worker has remaining attempts and has not already passed, then
    returns exactly 5 random questions for the worker's skill category — WITHOUT
    the correct answers.
    """

    def get(self, request):
        denied = _require_role(request, User.Role.FIELD_OFFICER)
        if denied:
            return denied

        worker = get_object_or_404(Worker, pk=request.query_params.get("worker_id"))
        state = _trade_test_state(worker)

        if state["passed"]:
            return Response(
                {"detail": "This worker has already passed the trade test.", **state},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if state["locked"] or state["attempts_remaining"] <= 0:
            return Response(
                {"detail": "No attempts remaining — profile is locked as Failed.", **state},
                status=status.HTTP_400_BAD_REQUEST,
            )

        category = category_for_skill(worker.skill_type)
        questions = list(
            TradeTestQuestion.objects.filter(skill_type=category).order_by("?")[
                :TRADE_TEST_QUESTION_COUNT
            ]
        )
        if len(questions) < TRADE_TEST_QUESTION_COUNT:
            return Response(
                {
                    "detail": f"Only {len(questions)} questions exist for {category}; "
                    f"need {TRADE_TEST_QUESTION_COUNT}. Seed more questions."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "worker_id": worker.id,
                "worker_name": worker.name,
                "category": category,
                "attempt_number": state["attempts_used"] + 1,
                "attempts_remaining": state["attempts_remaining"],
                "pass_mark": TRADE_TEST_PASS_MARK,
                "questions": TradeTestQuestionSerializer(questions, many=True).data,
            }
        )


class TradeTestSubmitView(APIView):
    """
    POST /api/trade-test/submit-attempt/   (Field Officer)

    Body: {"worker_id": <id>, "answers": [{"question_id": <id>, "selected_option": "A"}]}

    Scores server-side, records the attempt, and updates the worker's trade-test
    status: PASSED at >= 3/5, or FAILED (locked) once the 3rd attempt is used up.
    """

    def post(self, request):
        denied = _require_role(request, User.Role.FIELD_OFFICER)
        if denied:
            return denied

        worker = get_object_or_404(Worker, pk=request.data.get("worker_id"))
        state = _trade_test_state(worker)
        if state["passed"]:
            return Response(
                {"detail": "Worker has already passed.", **state},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if state["locked"] or state["attempts_remaining"] <= 0:
            return Response(
                {"detail": "No attempts remaining — profile is locked.", **state},
                status=status.HTTP_400_BAD_REQUEST,
            )

        answers = request.data.get("answers") or []
        if not isinstance(answers, list) or not answers:
            return Response(
                {"detail": "answers must be a non-empty list."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Score server-side against the stored correct_option (never trust client).
        selected = {}
        for a in answers:
            try:
                selected[int(a.get("question_id"))] = (a.get("selected_option") or "").upper()
            except (TypeError, ValueError):
                continue

        questions = TradeTestQuestion.objects.filter(id__in=selected.keys())
        score = sum(
            1 for q in questions if selected.get(q.id) == q.correct_option
        )
        passed = score >= TRADE_TEST_PASS_MARK
        attempt_number = state["attempts_used"] + 1

        with transaction.atomic():
            TradeTestAttempt.objects.create(
                worker=worker,
                attempt_number=attempt_number,
                score=score,
                is_passed=passed,
            )
            if passed:
                worker.trade_test_status = Worker.TradeTestStatus.PASSED
            elif attempt_number >= TRADE_TEST_MAX_ATTEMPTS:
                worker.trade_test_status = Worker.TradeTestStatus.FAILED
            worker.save(update_fields=["trade_test_status"])

        attempts_remaining = max(0, TRADE_TEST_MAX_ATTEMPTS - attempt_number)
        return Response(
            {
                "worker_id": worker.id,
                "score": score,
                "total": TRADE_TEST_QUESTION_COUNT,
                "pass_mark": TRADE_TEST_PASS_MARK,
                "is_passed": passed,
                "attempt_number": attempt_number,
                "attempts_remaining": attempts_remaining,
                "trade_test_status": worker.trade_test_status,
                "locked": worker.trade_test_status == Worker.TradeTestStatus.FAILED,
            },
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Safety Training video — watch-progress heartbeat
# ---------------------------------------------------------------------------
class SafetyVideoHeartbeatView(APIView):
    """
    POST /api/safety-video/heartbeat/   (Field Officer)

    Records how much of the mandatory safety induction video a worker has watched.
    ``is_completed`` flips to True at 100%. Progress never moves backwards.

    Body: {"worker": <id>, "progress_percentage": <0-100>}
    """

    def post(self, request):
        denied = _require_role(request, User.Role.FIELD_OFFICER)
        if denied:
            return denied

        worker = get_object_or_404(Worker, pk=request.data.get("worker"))
        try:
            pct = int(request.data.get("progress_percentage", 0))
        except (TypeError, ValueError):
            return Response(
                {"detail": "progress_percentage must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        pct = max(0, min(100, pct))

        sv, _ = SafetyTrainingProgress.objects.get_or_create(worker=worker)
        # Monotonic: never regress a previously higher watermark.
        sv.progress_percentage = max(sv.progress_percentage, pct)
        sv.is_completed = sv.progress_percentage >= 100
        sv.save()
        return Response(
            {
                "worker": worker.id,
                "progress_percentage": sv.progress_percentage,
                "is_completed": sv.is_completed,
            }
        )


# ---------------------------------------------------------------------------
# Real OCR extraction — pluggable provider (OCR.space default), mock fallback
# ---------------------------------------------------------------------------
# Provider is chosen by the OCR_PROVIDER env var:
#   "ocrspace"  (default) -> free hosted OCR.space API (set OCRSPACE_API_KEY)
#   "tesseract"           -> local Tesseract binary (dev only; not on Render native)
#   "mock"                -> canned sample values (no network, always works)
#   "claude"              -> Anthropic vision (paid; requires `anthropic` + key)
# Any provider failure falls back to empty fields + a "enter manually" note, so
# the Field Officer workflow never breaks.

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})\b")


def _to_iso_date(raw: str):
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
                "%m/%d/%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return timezone.datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _first_date(text: str):
    match = _DATE_RE.search(text or "")
    return _to_iso_date(match.group(1)) if match else None


def _ocrspace_text(file_bytes, filename, content_type) -> str:
    """Call the free OCR.space API and return the concatenated parsed text."""
    import requests

    api_key = os.environ.get("OCRSPACE_API_KEY", "helloworld")  # public demo key
    resp = requests.post(
        "https://api.ocr.space/parse/image",
        files={"file": (filename or "upload", file_bytes, content_type or "application/octet-stream")},
        data={"apikey": api_key, "language": "eng", "OCREngine": "2",
              "isOverlayRequired": "false", "scale": "true"},
        timeout=40,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("IsErroredOnProcessing"):
        raise RuntimeError(data.get("ErrorMessage") or "OCR.space processing error")
    results = data.get("ParsedResults") or []
    return "\n".join(r.get("ParsedText", "") for r in results).strip()


def _tesseract_text(file_bytes) -> str:
    import pytesseract
    from PIL import Image

    return pytesseract.image_to_string(Image.open(io.BytesIO(file_bytes)))


def _parse_fields(text: str, doc_type: str, requirement_name: str = "") -> dict:
    """Best-effort field extraction from raw OCR text. The FO verifies/corrects."""
    t = text or ""
    up = t.upper()

    if doc_type == "MEDICAL":
        vm = re.search(r"\b6\s*/\s*(6|9|12|18|24|36|60)\b", t)
        # No trailing \b — a sign like "+" is non-word, so "B+" has no boundary after it.
        bm = re.search(r"\b(AB|A|B|O)\s*([+\-]|POS|NEG)", up)
        blood = ""
        if bm:
            blood = bm.group(1) + ("+" if bm.group(2) in ("+", "POS") else "-")
        flagged = re.compile(r"DETECT|PRESENT|POSITIVE|\bYES\b")
        return {
            "exam_date": _first_date(t) or "",
            "vision": ("6/" + vm.group(1)) if vm else "",
            "blood_type": blood,
            "color_blindness": bool(re.search(r"COLOU?R\s*BLIND", up)) and bool(flagged.search(up)),
            "vertigo": bool(re.search(r"VERTIGO", up)) and bool(flagged.search(up)),
        }

    if doc_type == "POLICE":
        cert = re.search(r"\b([A-Z]{2,}[-/ ]?\d[\w-]*)\b", t)
        return {
            "certificate_number": cert.group(1) if cert else "",
            "issue_date": _first_date(t) or "",
            "verification_status": "Verified",
        }

    # IDENTITY (Aadhaar / PAN). Use a literal space (not \s) between groups so the
    # match can't span a newline and swallow a nearby date's year.
    aadhaar = re.search(r"\b(\d{4} ?\d{4} ?\d{4})\b", t)
    pan = re.search(r"\b([A-Z]{5}\d{4}[A-Z])\b", up)
    number = ""
    if requirement_name == "PAN" and pan:
        number = pan.group(1)
    elif aadhaar:
        number = re.sub(r"\s", "", aadhaar.group(1))
    elif pan:
        number = pan.group(1)
    name = ""
    for line in t.splitlines():
        s = line.strip()
        if re.fullmatch(r"[A-Za-z ]{4,40}", s) and not re.search(
            r"GOVERNMENT|INDIA|MALE|FEMALE|DOB|YEAR|BIRTH|FATHER|ADDRESS|"
            r"PERMANENT|ACCOUNT|INCOME|DEPARTMENT|CARD|\bNAME\b|GENDER|AADHAAR|"
            r"WORKER|CERTIFICATE|VERIFICATION|FITNESS|MEDICAL|POLICE|STATUS|"
            r"ISSUE|EXAM|VISION|VERTIGO|BLOOD|COLOU?R|DETECTED|NONE|VERIFIED",
            s.upper(),
        ):
            name = s.title()
            break
    return {"name": name, "aadhar_number": number, "document_number": number}


def _mock_fields(doc_type: str, requirement_name: str, today) -> dict:
    if doc_type == "MEDICAL":
        return {"exam_date": (today - timedelta(days=30)).isoformat(), "vision": "6/6",
                "blood_type": "O+", "color_blindness": False, "vertigo": False}
    if doc_type == "POLICE":
        return {"certificate_number": "PVC-DEMO-1001",
                "issue_date": (today - timedelta(days=30)).isoformat(),
                "verification_status": "Verified"}
    num = "ABCDE1234F" if requirement_name == "PAN" else "100000000001"
    return {"name": "Ravi Kumar", "aadhar_number": num, "document_number": num}


def _extract_fields(file_bytes, filename, content_type, doc_type, requirement_name, today):
    provider = os.environ.get("OCR_PROVIDER", "ocrspace").lower()
    if provider == "mock":
        return _mock_fields(doc_type, requirement_name, today), "mock", None

    text, err = None, None
    try:
        if provider == "tesseract":
            text = _tesseract_text(file_bytes)
        else:  # "ocrspace" (default)
            text = _ocrspace_text(file_bytes, filename, content_type)
    except Exception as exc:  # noqa: BLE001 — degrade to manual entry, never 500
        err = str(exc)

    if not text or not text.strip():
        return {}, provider, err or "No text detected — enter the values manually."
    return _parse_fields(text, doc_type, requirement_name), provider, None


class OcrExtractView(APIView):
    """
    POST /api/intake/ocr-extract/   (Field Officer)

    Runs OCR on the uploaded scan and returns best-effort form fields for the
    given doc_type (IDENTITY | MEDICAL | POLICE). The FO reviews/corrects the
    values, then commits via /verify-document/. Provider is env-selected; a
    failure returns empty fields + a note rather than an error.
    """

    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        denied = _require_role(request, User.Role.FIELD_OFFICER)
        if denied:
            return denied

        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"detail": "No file provided under form field 'file'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        doc_type = (request.data.get("doc_type") or "").upper()
        if doc_type not in {"IDENTITY", "MEDICAL", "POLICE"}:
            return Response(
                {"detail": "doc_type must be IDENTITY, MEDICAL or POLICE."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        requirement_name = request.data.get("requirement_name", "")
        today = timezone.now().date()

        fields, provider, note = _extract_fields(
            upload.read(), upload.name, upload.content_type,
            doc_type, requirement_name, today,
        )
        return Response(
            {"form_type": doc_type, "fields": fields, "provider": provider, "note": note}
        )
