"""DRF serializers. Kept thin — heavy logic stays on the models/views."""
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from .models import (
    IntakeList,
    IntakeListWorker,
    IntakeMedicalRecord,
    IntakePoliceVerification,
    Project,
    ProjectRequirement,
    RequirementMaster,
    TradeTestQuestion,
    User,
    Worker,
    WorkerDocument,
)


class UserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "role", "role_display", "organization", "first_name"]


class RequirementMasterSerializer(serializers.ModelSerializer):
    class Meta:
        model = RequirementMaster
        fields = ["id", "name", "description", "is_expirable"]


class ProjectRequirementSerializer(serializers.ModelSerializer):
    requirement = RequirementMasterSerializer(read_only=True)

    class Meta:
        model = ProjectRequirement
        fields = ["id", "requirement", "is_mandatory"]


class ProjectSerializer(serializers.ModelSerializer):
    requirements = ProjectRequirementSerializer(
        source="project_requirements", many=True, read_only=True
    )
    contractor_ids = serializers.PrimaryKeyRelatedField(
        source="contractors",
        many=True,
        queryset=User.objects.filter(role=User.Role.CONTRACTOR),
        required=False,
    )

    class Meta:
        model = Project
        fields = [
            "id",
            "name",
            "description",
            "principal_employer",
            "contractor_ids",
            "is_active",
            "requirements",
            "created_at",
        ]
        read_only_fields = ["principal_employer", "created_at"]


class WorkerDocumentSerializer(serializers.ModelSerializer):
    requirement_name = serializers.CharField(
        source="requirement.name", read_only=True
    )

    class Meta:
        model = WorkerDocument
        fields = [
            "id",
            "worker",
            "requirement",
            "requirement_name",
            "document_number",
            "document_file",
            "file_url",
            "verification_status",
            "expiry_date",
            "rejection_reason",
            "uploaded_at",
            "updated_at",
        ]
        read_only_fields = ["uploaded_at", "updated_at"]


class WorkerSerializer(serializers.ModelSerializer):
    documents = WorkerDocumentSerializer(many=True, read_only=True)
    # Trade-test status + attempts used, so dashboards can show exam progress.
    trade_test_attempts = serializers.SerializerMethodField()
    # Safety induction video watch status.
    safety_video = serializers.SerializerMethodField()

    class Meta:
        model = Worker
        fields = [
            "id",
            "name",
            "skill_type",
            "aadhar_number",
            "status",
            "contractor",
            "documents",
            "trade_test_status",
            "trade_test_attempts",
            "safety_video",
            "created_at",
        ]
        read_only_fields = ["created_at", "trade_test_status"]

    def get_trade_test_attempts(self, obj):
        return obj.trade_test_attempts.count()

    def get_safety_video(self, obj):
        try:
            sv = obj.safety_video
        except ObjectDoesNotExist:
            return {"progress_percentage": 0, "is_completed": False}
        return {
            "progress_percentage": sv.progress_percentage,
            "is_completed": sv.is_completed,
        }


class IntakeListWorkerSerializer(serializers.ModelSerializer):
    worker = WorkerSerializer(read_only=True)

    class Meta:
        model = IntakeListWorker
        fields = ["id", "worker"]


class IntakeListSerializer(serializers.ModelSerializer):
    workers = IntakeListWorkerSerializer(
        source="list_workers", many=True, read_only=True
    )
    project_name = serializers.CharField(source="project.name", read_only=True)
    contractor_email = serializers.CharField(source="contractor.email", read_only=True)

    class Meta:
        model = IntakeList
        fields = [
            "id",
            "project",
            "project_name",
            "contractor",
            "contractor_email",
            "status",
            "pe_comments",
            "workers",
            "submitted_at",
            "reviewed_at",
            "created_at",
        ]
        read_only_fields = ["submitted_at", "reviewed_at", "created_at"]


# ---------------------------------------------------------------------------
# 5-pillar intake serializers
# ---------------------------------------------------------------------------
class IntakeMedicalRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntakeMedicalRecord
        fields = [
            "id",
            "worker",
            "color_blindness",
            "vision",
            "vertigo",
            "blood_type",
            "exam_date",
            "expiry_date",
            "document_file",
            "file_url",
        ]
        # expiry_date is always derived (365 days) on save — never client-set.
        read_only_fields = ["expiry_date"]


class IntakePoliceVerificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = IntakePoliceVerification
        fields = [
            "id",
            "worker",
            "certificate_number",
            "issue_date",
            "expiry_date",
            "verification_status",
            "document_file",
            "file_url",
        ]
        read_only_fields = ["expiry_date"]


class TradeTestQuestionSerializer(serializers.ModelSerializer):
    """Public question shape sent to the exam UI — deliberately WITHOUT the
    correct_option (never leak answers to the client; scoring is server-side)."""

    class Meta:
        model = TradeTestQuestion
        fields = [
            "id",
            "skill_type",
            "question_text",
            "image_url",
            "option_a",
            "option_b",
            "option_c",
            "option_d",
        ]
