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
    IntakeList,
    IntakeListWorker,
    Project,
    RequirementMaster,
    User,
    Worker,
    WorkerDocument,
)
from .serializers import (
    IntakeListSerializer,
    ProjectSerializer,
    UserSerializer,
    WorkerDocumentSerializer,
    WorkerSerializer,
)


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

        # Prefetch documents so each compliance evaluation avoids N+1 queries.
        workers_qs = workers_qs.prefetch_related("documents")

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
    """GET /api/workers/  — role-scoped worker registry (used by dashboards)."""

    def get(self, request):
        if request.user.role == User.Role.CONTRACTOR:
            qs = Worker.objects.filter(contractor=request.user)
        else:
            qs = Worker.objects.all()
        qs = qs.prefetch_related("documents")
        return Response(WorkerSerializer(qs, many=True).data)


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
