from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from . import crypto
from .models import (
    CandidateProfile,
    CandidateSkill,
    IntakeList,
    IntakeListWorker,
    IntakeMedicalRecord,
    IntakePoliceVerification,
    Project,
    ProjectRequirement,
    RequirementMaster,
    SafetyTrainingProgress,
    Skill,
    TradeTestAttempt,
    TradeTestQuestion,
    User,
    Worker,
    WorkerDocument,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("email", "role", "organization", "is_staff")
    list_filter = ("role", "is_staff")
    search_fields = ("email", "organization")
    ordering = ("email",)
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Persona", {"fields": ("role", "organization")}),
    )


class ProjectRequirementInline(admin.TabularInline):
    model = ProjectRequirement
    extra = 1


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "principal_employer", "is_active", "created_at")
    inlines = [ProjectRequirementInline]
    filter_horizontal = ("contractors",)


@admin.register(RequirementMaster)
class RequirementMasterAdmin(admin.ModelAdmin):
    list_display = ("name", "is_expirable")


class WorkerDocumentInline(admin.TabularInline):
    model = WorkerDocument
    extra = 0


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ("name", "aadhar_number", "skill_type", "status", "contractor")
    list_filter = ("skill_type", "status")
    search_fields = ("name", "aadhar_number", "skill_type")
    inlines = [WorkerDocumentInline]


class IntakeListWorkerInline(admin.TabularInline):
    model = IntakeListWorker
    extra = 0


@admin.register(IntakeList)
class IntakeListAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "contractor", "status", "submitted_at")
    list_filter = ("status",)
    inlines = [IntakeListWorkerInline]


admin.site.register(WorkerDocument)


@admin.register(IntakeMedicalRecord)
class IntakeMedicalRecordAdmin(admin.ModelAdmin):
    list_display = ("worker", "exam_date", "expiry_date", "color_blindness", "vertigo")
    list_filter = ("color_blindness", "vertigo")


@admin.register(IntakePoliceVerification)
class IntakePoliceVerificationAdmin(admin.ModelAdmin):
    list_display = ("worker", "certificate_number", "issue_date", "expiry_date", "verification_status")


@admin.register(TradeTestQuestion)
class TradeTestQuestionAdmin(admin.ModelAdmin):
    list_display = ("id", "skill_type", "question_text", "correct_option")
    list_filter = ("skill_type",)
    search_fields = ("question_text",)


@admin.register(TradeTestAttempt)
class TradeTestAttemptAdmin(admin.ModelAdmin):
    list_display = ("worker", "attempt_number", "score", "is_passed", "created_at")
    list_filter = ("is_passed",)


@admin.register(SafetyTrainingProgress)
class SafetyTrainingProgressAdmin(admin.ModelAdmin):
    list_display = ("worker", "progress_percentage", "is_completed", "updated_at")
    list_filter = ("is_completed",)


class CandidateSkillInline(admin.TabularInline):
    model = CandidateSkill
    extra = 0


@admin.register(CandidateProfile)
class CandidateProfileAdmin(admin.ModelAdmin):
    """Resume profiles.

    PII is shown **masked** — the admin is a support surface, not a data-export
    tool, and the ciphertext columns are excluded outright so a stray page load
    can never dump them.
    """

    list_display = ("worker", "masked_phone", "masked_email", "place", "stream",
                    "category", "years_of_experience", "qualification")
    list_filter = ("stream", "category", "qualification")
    search_fields = ("place",)  # deliberately NOT name/phone/email — those are encrypted
    readonly_fields = ("masked_phone", "masked_email", "parser_provider",
                       "parse_note", "created_at", "updated_at")
    exclude = ("name_encrypted", "phone_encrypted", "email_encrypted",
               "phone_hash", "email_hash")
    inlines = [CandidateSkillInline]

    @admin.display(description="Phone")
    def masked_phone(self, obj):
        return crypto.mask_phone(obj.phone)

    @admin.display(description="Email")
    def masked_email(self, obj):
        return crypto.mask_email(obj.email)


@admin.register(Skill)
class SkillAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
