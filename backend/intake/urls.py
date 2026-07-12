from django.urls import path

from . import views

urlpatterns = [
    # Auth
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("me/", views.me, name="me"),
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
]
