"""DRF serializers. Kept thin — heavy logic stays on the models/views."""
from rest_framework import serializers

from .models import (
    IntakeList,
    IntakeListWorker,
    Project,
    ProjectRequirement,
    RequirementMaster,
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
            "created_at",
        ]
        read_only_fields = ["created_at"]


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
