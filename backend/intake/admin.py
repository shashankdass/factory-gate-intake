from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    IntakeList,
    IntakeListWorker,
    IntakeMedicalRecord,
    IntakePoliceVerification,
    Project,
    ProjectRequirement,
    RequirementMaster,
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
