from django.urls import path

from . import views

urlpatterns = [
    # Auth
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("me/", views.me, name="me"),
    # Requirements catalogue
    path("requirements/", views.requirements, name="requirements"),
    # Projects
    path("projects/", views.ProjectListCreateView.as_view(), name="projects"),
    path("projects/<int:pk>/", views.project_detail, name="project-detail"),
    path(
        "projects/<int:pk>/eligible-workers/",
        views.EligibleWorkersView.as_view(),
        name="eligible-workers",
    ),
    # Workers
    path("workers/", views.WorkerListView.as_view(), name="workers"),
    path("workers/<int:pk>/", views.WorkerDetailView.as_view(), name="worker-detail"),
    path("contractors/", views.contractors, name="contractors"),
    path(
        "verification-status/",
        views.VerificationStatusView.as_view(),
        name="verification-status",
    ),
    path(
        "workers/bulk-upload/",
        views.WorkerBulkUploadView.as_view(),
        name="worker-bulk-upload",
    ),
    # Documents
    path(
        "documents/upload/",
        views.DocumentUploadView.as_view(),
        name="document-upload",
    ),
    path(
        "documents/<int:pk>/review/",
        views.DocumentReviewView.as_view(),
        name="document-review",
    ),
    # Intake lists
    path("intake-lists/", views.IntakeListView.as_view(), name="intake-lists"),
    path(
        "intake-lists/<int:pk>/",
        views.IntakeListDetailView.as_view(),
        name="intake-list-detail",
    ),
    path(
        "intake-lists/<int:pk>/review/",
        views.IntakeListReviewView.as_view(),
        name="intake-list-review",
    ),
    # Gate security
    path("gate-check/", views.GateCheckView.as_view(), name="gate-check"),
    # Field Officer Intake Workbench (5-pillar)
    path("intake/mock-ocr/", views.MockOcrView.as_view(), name="mock-ocr"),
    path(
        "intake/verify-document/",
        views.VerifyDocumentView.as_view(),
        name="verify-document",
    ),
    path(
        "intake/ocr-extract/",
        views.OcrExtractView.as_view(),
        name="ocr-extract",
    ),
    # Trade test (Field Officer administered practical MCQ exam)
    path(
        "trade-test/start/",
        views.TradeTestStartView.as_view(),
        name="trade-test-start",
    ),
    path(
        "trade-test/submit-attempt/",
        views.TradeTestSubmitView.as_view(),
        name="trade-test-submit",
    ),
    # Safety induction video watch progress
    path(
        "safety-video/heartbeat/",
        views.SafetyVideoHeartbeatView.as_view(),
        name="safety-video-heartbeat",
    ),
]
