"""DRF serializers. Kept thin — heavy logic stays on the models/views."""
from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from . import crypto, storage
from .models import (
    CandidateProfile,
    IntakeList,
    IntakeListWorker,
    IntakeMedicalRecord,
    IntakePoliceVerification,
    Project,
    ProjectRequirement,
    RequirementMaster,
    Skill,
    TradeTestQuestion,
    User,
    Worker,
    WorkerBankAccount,
    WorkerDocument,
)


class SignedUrlMixin:
    """Adds a short-lived download link for a private storage object.

    Signing is opt-in via ``context={"sign": True}`` because each link is a
    round trip to Supabase — worth it for a document table the contractor is
    about to click through, wasteful for a 200-row compliance list nobody is
    downloading from.
    """

    def _download_url(self, obj):
        if not self.context.get("sign"):
            return None
        key = getattr(obj, "storage_key", "")
        if key:
            return storage.signed_url(key)
        # Pre-Supabase rows: fall back to whatever local/legacy URL exists.
        legacy = getattr(obj, "document_file", None)
        if legacy:
            try:
                return legacy.url
            except ValueError:
                pass
        return getattr(obj, "file_url", "") or None


class UserSerializer(serializers.ModelSerializer):
    role_display = serializers.CharField(source="get_role_display", read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "role", "role_display", "organization", "first_name"]


class RequirementMasterSerializer(serializers.ModelSerializer):
    class Meta:
        model = RequirementMaster
        fields = ["id", "name", "description", "is_expirable", "kind", "pillar_code"]


class ProjectRequirementSerializer(serializers.ModelSerializer):
    requirement = RequirementMasterSerializer(read_only=True)
    # Who last changed the compliance bar, so the PE can see it was moved.
    updated_by_email = serializers.CharField(
        source="updated_by.email", read_only=True, default=None
    )

    class Meta:
        model = ProjectRequirement
        fields = ["id", "requirement", "is_mandatory", "updated_by_email", "updated_at"]


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


class WorkerDocumentSerializer(SignedUrlMixin, serializers.ModelSerializer):
    requirement_name = serializers.CharField(
        source="requirement.name", read_only=True
    )
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = WorkerDocument
        fields = [
            "id",
            "worker",
            "requirement",
            "requirement_name",
            "document_number",
            "storage_key",
            "download_url",
            "file_url",
            "verification_status",
            "expiry_date",
            "rejection_reason",
            "uploaded_at",
            "updated_at",
        ]
        read_only_fields = ["uploaded_at", "updated_at", "storage_key"]

    def get_download_url(self, obj):
        return self._download_url(obj)


class SkillSerializer(serializers.ModelSerializer):
    class Meta:
        model = Skill
        fields = ["id", "name"]


class WorkerBankAccountSerializer(SignedUrlMixin, serializers.ModelSerializer):
    """Payment details.

    The account number is masked unless the caller owns the worker — a payroll
    number is exactly the kind of field that should not be casually readable off
    a shared screen. ``shared_with_count`` surfaces the ghost-worker signal:
    several workers registered against one account.
    """

    account_number = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    shared_with_count = serializers.SerializerMethodField()

    class Meta:
        model = WorkerBankAccount
        fields = [
            "id",
            "worker",
            "account_number",
            "ifsc",
            "bank_name",
            "account_holder_name",
            "download_url",
            "shared_with_count",
            "created_at",
        ]
        read_only_fields = fields

    def get_account_number(self, obj):
        number = obj.account_number
        if not number:
            return None
        if self.context.get("reveal_pii"):
            return number
        return "•" * max(0, len(number) - 4) + number[-4:]

    def get_download_url(self, obj):
        return self._download_url(obj)

    def get_shared_with_count(self, obj):
        return obj.shared_with().count()


class CandidateProfileSerializer(SignedUrlMixin, serializers.ModelSerializer):
    """Resume-derived profile.

    PII is decrypted **only** when the view passes ``context={"reveal_pii":
    True}`` — i.e. the contractor who owns the worker, or the PE reviewing a
    submitted list. Everyone else sees masked values, and no caller ever
    receives the ciphertext columns.
    """

    name = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()
    skills = serializers.SerializerMethodField()
    resume_url = serializers.SerializerMethodField()

    class Meta:
        model = CandidateProfile
        fields = [
            "id",
            "worker",
            "name",
            "phone",
            "email",
            "place",
            "stream",
            "category",
            "years_of_experience",
            "qualification",
            "skills",
            "resume_url",
            "parser_provider",
            "parse_note",
            "created_at",
        ]
        read_only_fields = fields

    def _reveal(self) -> bool:
        return bool(self.context.get("reveal_pii"))

    def get_name(self, obj):
        name = obj.name
        if not name:
            return None
        return name if self._reveal() else f"{name.split()[0]} …"

    def get_phone(self, obj):
        phone = obj.phone
        return phone if self._reveal() else crypto.mask_phone(phone)

    def get_email(self, obj):
        email = obj.email
        return email if self._reveal() else crypto.mask_email(email)

    def get_skills(self, obj):
        return [cs.skill.name for cs in obj.candidate_skills.all()]

    def get_resume_url(self, obj):
        if not self.context.get("sign") or not obj.resume_key:
            return None
        return storage.signed_url(obj.resume_key)


class WorkerSerializer(serializers.ModelSerializer):
    documents = WorkerDocumentSerializer(many=True, read_only=True)
    # Trade-test status + attempts used, so dashboards can show exam progress.
    trade_test_attempts = serializers.SerializerMethodField()
    # Safety induction video watch status.
    safety_video = serializers.SerializerMethodField()
    # Resume profile (null until a resume has been scanned).
    candidate_profile = serializers.SerializerMethodField()
    # Payment details (null until a cheque/passbook is captured).
    bank_account = serializers.SerializerMethodField()

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
            "candidate_profile",
            "bank_account",
            "created_at",
        ]
        read_only_fields = ["created_at", "trade_test_status"]

    def get_candidate_profile(self, obj):
        try:
            profile = obj.candidate_profile
        except ObjectDoesNotExist:
            return None
        return CandidateProfileSerializer(profile, context=self.context).data

    def get_bank_account(self, obj):
        try:
            account = obj.bank_account
        except ObjectDoesNotExist:
            return None
        return WorkerBankAccountSerializer(account, context=self.context).data

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
class IntakeMedicalRecordSerializer(SignedUrlMixin, serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

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
            "storage_key",
            "download_url",
            "file_url",
        ]
        # expiry_date is always derived (365 days) on save — never client-set.
        read_only_fields = ["expiry_date", "storage_key"]

    def get_download_url(self, obj):
        return self._download_url(obj)


class IntakePoliceVerificationSerializer(SignedUrlMixin, serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = IntakePoliceVerification
        fields = [
            "id",
            "worker",
            "certificate_number",
            "issue_date",
            "expiry_date",
            "verification_status",
            "storage_key",
            "download_url",
            "file_url",
        ]
        read_only_fields = ["expiry_date", "storage_key"]

    def get_download_url(self, obj):
        return self._download_url(obj)


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
