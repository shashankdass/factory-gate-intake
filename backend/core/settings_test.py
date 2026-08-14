"""Test settings — hermetic by construction.

pytest-django loads settings during ``pytest_configure``, i.e. *before* any
conftest module runs, so environment variables set in a conftest arrive too
late. Pinning the values here is the only way to guarantee the suite never
reaches for Postgres, an S3 bucket or a paid vision API.
"""
from .settings import *  # noqa: F401,F403

# In-memory SQLite: no Postgres, no migrations against a real server. The PII
# layer still runs end to end via the AES-256-GCM backend (pgcrypto is used when
# the same code runs against Postgres in production).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Deterministic, test-only crypto keys. Different from each other, as the real
# deployment requires — reusing one for both would defeat the blind index.
PII_ENCRYPTION_KEY = "test-pii-encryption-key-0123456789abcdef"
PII_BLIND_INDEX_KEY = "test-pii-blind-index-pepper-fedcba9876"

# No object store and no vision API: the local signed-storage backend and the
# canned resume parser keep the suite offline.
S3_ENDPOINT_URL = ""
S3_ACCESS_KEY_ID = ""
S3_SECRET_ACCESS_KEY = ""
RESUME_PARSER_PROVIDER = "mock"
VERIFY_DOCUMENT_TYPES = "aadhaar"

# Resume stays advisory by default so the fixtures mirror production defaults;
# the test that cares flips it explicitly.
REQUIRE_RESUME_FOR_COMPLIANCE = False

# Plain static storage: the manifest backend would demand a collectstatic run.
# (Set via STATICFILES_STORAGE, not STORAGES — base settings uses the former and
# Django rejects having both.)
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast tests
