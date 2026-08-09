"""
Django settings for the Factory Gate-Intake Optimization platform.

Every deployment-sensitive value is read from the environment (os.environ) so the
exact same codebase runs locally, on Render, or on Railway with only env changes.
"""
import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load a local .env in development. In production the platform injects real env vars.
load_dotenv(BASE_DIR / ".env")


def env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(key: str, default: str = "") -> list[str]:
    raw = os.environ.get(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Core security / debug
# ---------------------------------------------------------------------------
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-secret-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1") or ["*"]

# Trust the deployment platform's proxy for HTTPS + host headers.
CSRF_TRUSTED_ORIGINS = [
    origin
    for origin in env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
    if origin.startswith("http")
]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "rest_framework.authtoken",
    "corsheaders",
    # Local
    "intake",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.application"

# ---------------------------------------------------------------------------
# Database (PostgreSQL)
#
# Resolution order:
#   1. USE_SQLITE_FALLBACK=True   -> local SQLite file (zero-config escape hatch)
#   2. DATABASE_URL set           -> parsed directly (this is what Render injects)
#   3. individual DB_* env vars   -> assembled into a Postgres connection
# ---------------------------------------------------------------------------
if env_bool("USE_SQLITE_FALLBACK", False):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
elif os.environ.get("DATABASE_URL"):
    DATABASES = {
        "default": dj_database_url.config(
            env="DATABASE_URL",
            conn_max_age=600,
            # Managed Postgres (Render, etc.) requires SSL in production.
            ssl_require=not DEBUG,
        )
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": os.environ.get("DB_ENGINE", "django.db.backends.postgresql"),
            "NAME": os.environ.get("DB_NAME", "gate_intake"),
            "USER": os.environ.get("DB_USER", ""),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "5432"),
        }
    }

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "intake.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "http://localhost:5173")
# In DEBUG we relax CORS so the frontend "just works" from any dev port.
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOW_CREDENTIALS = True

# ---------------------------------------------------------------------------
# I18N / static / media
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# S3-compatible private object storage (Supabase Storage / Cloudflare R2 / MinIO)
#
# Same mechanism as the resume-scanner service: one PRIVATE bucket, opaque keys,
# and presigned GET URLs as the only read path. Every uploaded document —
# Aadhaar, PAN, Safety cert, Medical, PVC, Resume — goes here.
#
#   Supabase Storage:  https://<project-ref>.supabase.co/storage/v1/s3
#                      (S3_REGION must be the real project region, e.g.
#                       ap-south-1 — Supabase rejects "auto")
#   Cloudflare R2:     https://<account_id>.r2.cloudflarestorage.com
#   MinIO (local):     http://127.0.0.1:9000
#
# When S3_ENDPOINT_URL is unset the storage layer falls back to a local signed
# filesystem backend so `runserver` and the test suite work with zero config.
# ---------------------------------------------------------------------------
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "").rstrip("/")
S3_BUCKET = os.environ.get("S3_BUCKET", "gate-intake-docs")
S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID", "")
S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", "")
S3_REGION = os.environ.get("S3_REGION", "auto")
S3_KEY_PREFIX = os.environ.get("S3_KEY_PREFIX", "gate-intake")
# TTL for presigned download links. Clamped to the S3 signature limits.
PRESIGN_EXPIRY_SECONDS = max(
    60, min(3600, int(os.environ.get("PRESIGN_EXPIRY_SECONDS", "900")))
)

# ---------------------------------------------------------------------------
# PII encryption (candidate name / phone / email)
#
# PII_ENCRYPTION_KEY  -> passphrase for pgcrypto's pgp_sym_encrypt (AES-256).
# PII_BLIND_INDEX_KEY -> separate pepper for the deterministic HMAC blind index
#                        that makes encrypted columns searchable for equality.
#
# The two keys MUST be different: the blind index is what makes duplicate
# detection possible without decrypting, and reusing the cipher passphrase for
# it would leak the passphrase into every index lookup.
# ---------------------------------------------------------------------------
PII_ENCRYPTION_KEY = os.environ.get(
    "PII_ENCRYPTION_KEY", "dev-only-pii-encryption-key-change-me-32+chars"
)
PII_BLIND_INDEX_KEY = os.environ.get(
    "PII_BLIND_INDEX_KEY", "dev-only-pii-blind-index-pepper-change-me"
)

# ---------------------------------------------------------------------------
# Resume parsing provider
#   claude  — Anthropic vision → strict JSON (best quality; needs ANTHROPIC_API_KEY)
#   gemini  — Google Gemini vision (needs GEMINI_API_KEY)
#   ocr     — reuse the OCR text pipeline + heuristics (no LLM key required)
#   mock    — canned values, no network (always works; used by the test suite)
# ---------------------------------------------------------------------------
RESUME_PARSER_PROVIDER = os.environ.get("RESUME_PARSER_PROVIDER", "ocr").lower()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

# Should a missing/unparsed resume block deployment?
# Off by default so the seeded demo workers stay deployable; the resume pillar is
# still reported in every compliance payload either way.
REQUIRE_RESUME_FOR_COMPLIANCE = env_bool("REQUIRE_RESUME_FOR_COMPLIANCE", False)
