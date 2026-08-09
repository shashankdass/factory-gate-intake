"""Column-level PII encryption + keyed blind indexes.

Candidate ``name`` / ``phone`` / ``email`` are never stored in plaintext. They
live in ``BYTEA`` columns as AES-256 ciphertext produced by pgcrypto's
``pgp_sym_encrypt`` (wrapped in the ``app_encrypt`` SQL helper installed by
migration 0006).

Two keys, two jobs — and they must be different:

``PII_ENCRYPTION_KEY``
    The cipher passphrase. Supplied per-statement as a bind parameter, so it
    never lives in the database.

``PII_BLIND_INDEX_KEY``
    A separate pepper for a deterministic HMAC-SHA256 "blind index".
    ``pgp_sym_encrypt`` is randomised — the same email yields different
    ciphertext every time — so ciphertext can never be compared for equality or
    constrained with UNIQUE. The blind index restores O(log n) equality lookups
    and duplicate detection without storing anything reversible.

The HMAC is computed **in the application process**, so the pepper never travels
to the database, never appears in a query, and never lands in
``pg_stat_statements``. It stays byte-for-byte compatible with the SQL helper
``app_blind_index(text, text)``::

    hmac(lower(btrim(plain)), pepper, 'sha256')

Backends
--------
On PostgreSQL the pgcrypto path is used (this is the production configuration).
On SQLite — the repo's documented ``USE_SQLITE_FALLBACK`` escape hatch, also
what the test suite runs on — there is no pgcrypto, so an equivalent AES-256-GCM
implementation runs in-process. Both backends are AES-256; only the location of
the cipher differs, and the blind index is identical either way.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re

from django.conf import settings
from django.db import connection

# --- Name-token search tuning ---------------------------------------------
# A 2-word name yields at most MAX_TOKENS * (MAX_PREFIX - MIN_PREFIX + 1)
# digests — bounded and small.
MIN_PREFIX = 3
MAX_PREFIX = 12
MAX_TOKENS = 6

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Marker prepended to locally-encrypted values so the two backends can never be
# confused if a database is moved between engines.
_LOCAL_PREFIX = b"AGCM1:"


# ---------------------------------------------------------------------------
# Blind index (identical on every backend — pure Python HMAC)
# ---------------------------------------------------------------------------
def _pepper() -> bytes:
    pepper = settings.PII_BLIND_INDEX_KEY
    if not pepper:
        raise RuntimeError("PII_BLIND_INDEX_KEY must be set.")
    return pepper.encode("utf-8")


def blind_index(value: str | None) -> bytes | None:
    """Return a 32-byte keyed digest, or None when there is nothing to index."""
    if value is None:
        return None
    normalised = value.strip().lower()
    if not normalised:
        return None
    return hmac.new(_pepper(), normalised.encode("utf-8"), hashlib.sha256).digest()


def _tokenise(value: str | None) -> list[str]:
    if not value:
        return []
    words = _TOKEN_RE.findall(value.lower())
    return [word for word in words if len(word) >= MIN_PREFIX][:MAX_TOKENS]


def name_tokens(name: str | None) -> list[bytes]:
    """Digests to STORE for a name: every word plus its 3..12 char prefixes.

    ``"Rajesh Kumar"`` -> digests of raj, raje, rajes, rajesh, kum, kuma, kumar.
    Storing prefixes is what turns an equality index into prefix search over a
    column the database cannot read.
    """
    digests: set[bytes] = set()
    for token in _tokenise(name):
        for length in range(MIN_PREFIX, min(len(token), MAX_PREFIX) + 1):
            digest = blind_index(token[:length])
            if digest is not None:
                digests.add(digest)
    return sorted(digests)


def name_query_tokens(query: str | None) -> list[bytes]:
    """Digests to LOOK UP for a search string.

    Each word is truncated to MAX_PREFIX so a long query still matches the
    stored prefix set; words shorter than MIN_PREFIX are dropped because they
    would match far too broadly to be useful.
    """
    digests: list[bytes] = []
    for token in _tokenise(query):
        digest = blind_index(token[:MAX_PREFIX])
        if digest is not None and digest not in digests:
            digests.append(digest)
    return digests


# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------
def _passphrase() -> str:
    key = settings.PII_ENCRYPTION_KEY
    if not key:
        raise RuntimeError("PII_ENCRYPTION_KEY must be set.")
    return key


def _is_postgres() -> bool:
    return connection.vendor == "postgresql"


def _as_bytes(value) -> bytes | None:
    """psycopg returns BYTEA as memoryview; SQLite returns bytes."""
    if value is None:
        return None
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, bytearray):
        return bytes(value)
    return value


# -- local AES-256-GCM (SQLite / dev / tests) -------------------------------
def _local_key() -> bytes:
    """Derive a 32-byte AES key from the passphrase.

    Deterministic (fixed salt) so the same passphrase always decrypts rows
    written earlier — this mirrors how pgcrypto derives its session key from the
    passphrase carried in each PGP message header.
    """
    return hashlib.pbkdf2_hmac(
        "sha256", _passphrase().encode("utf-8"), b"gate-intake-pii-v1", 200_000, dklen=32
    )


def _local_encrypt(plain: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    ciphertext = AESGCM(_local_key()).encrypt(nonce, plain.encode("utf-8"), None)
    return _LOCAL_PREFIX + nonce + ciphertext


def _local_decrypt(blob: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    body = blob[len(_LOCAL_PREFIX):]
    nonce, ciphertext = body[:12], body[12:]
    return AESGCM(_local_key()).decrypt(nonce, ciphertext, None).decode("utf-8")


# -- public API -------------------------------------------------------------
def encrypt(value: str | None) -> bytes | None:
    """Encrypt a PII string. Returns None for empty input (NULL column)."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None

    if _is_postgres():
        with connection.cursor() as cursor:
            cursor.execute("SELECT app_encrypt(%s, %s)", [value, _passphrase()])
            return _as_bytes(cursor.fetchone()[0])
    return _local_encrypt(value)


def decrypt(ciphertext) -> str | None:
    """Decrypt a PII column. Returns None when the column is NULL.

    Never raises on a bad/rotated key — PII that cannot be read is reported as
    None so a dashboard degrades to "—" instead of returning a 500.
    """
    blob = _as_bytes(ciphertext)
    if not blob:
        return None

    try:
        if blob.startswith(_LOCAL_PREFIX):
            return _local_decrypt(blob)
        if _is_postgres():
            with connection.cursor() as cursor:
                cursor.execute("SELECT app_decrypt(%s, %s)", [blob, _passphrase()])
                return cursor.fetchone()[0]
    except Exception:  # noqa: BLE001 — unreadable PII must not break a response
        return None
    return None


def mask_phone(phone: str | None) -> str:
    """`9876543210` -> `••••••3210`. For list views that don't need the full value."""
    if not phone:
        return ""
    tail = phone[-4:]
    return "•" * max(0, len(phone) - 4) + tail


def mask_email(email: str | None) -> str:
    """`ravi.kumar@example.com` -> `r•••••••••@example.com`."""
    if not email or "@" not in email:
        return email or ""
    local, _, domain = email.partition("@")
    if len(local) <= 1:
        return f"{local}@{domain}"
    return f"{local[0]}{'•' * (len(local) - 1)}@{domain}"
