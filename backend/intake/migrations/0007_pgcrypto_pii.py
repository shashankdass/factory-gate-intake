"""Install pgcrypto + pg_trgm and the PII crypto helper functions.

Everything here is PostgreSQL-only and guarded on ``connection.vendor``, so the
documented ``USE_SQLITE_FALLBACK`` escape hatch (and the test suite) still
migrates cleanly — on SQLite the application falls back to an equivalent
in-process AES-256-GCM implementation (see ``intake/crypto.py``).

Creating an extension requires database-owner or superuser rights. On Render's
managed Postgres and on Supabase the app user has them; if yours does not, run
``CREATE EXTENSION pgcrypto; CREATE EXTENSION pg_trgm;`` once as an admin and
re-run migrate — the statements are all ``IF NOT EXISTS``.
"""
from django.db import migrations

# ---------------------------------------------------------------------------
# Crypto helpers. Keeping them in SQL means application queries never spell out
# cipher options, and the options can be changed in exactly one place.
#
# s2k-count is the string-to-key iteration count, paid on EVERY encrypt AND
# decrypt. A large count only defends a LOW-ENTROPY passphrase against offline
# brute force; PII_ENCRYPTION_KEY is meant to be long random bytes, so extra
# iterations buy nothing while making every read slow. 1048576 keeps a healthy
# margin in case someone deploys a weaker key (~0.5 ms/op).
#
# Safe to change later: the parameters live in each PGP message header, so
# re-running this never invalidates rows encrypted under older options.
# ---------------------------------------------------------------------------
FORWARD_SQL = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- VOLATILE on purpose: the output is randomised, so the planner must never
-- constant-fold or cache it.
CREATE OR REPLACE FUNCTION app_encrypt(plain TEXT, passphrase TEXT)
RETURNS BYTEA
LANGUAGE sql VOLATILE STRICT
AS $$
    SELECT pgp_sym_encrypt(
        plain,
        passphrase,
        'cipher-algo=aes256, s2k-mode=3, s2k-digest-algo=sha256, s2k-count=1048576, compress-algo=0'
    )
$$;

CREATE OR REPLACE FUNCTION app_decrypt(ciphertext BYTEA, passphrase TEXT)
RETURNS TEXT
LANGUAGE sql IMMUTABLE STRICT
AS $$
    SELECT pgp_sym_decrypt(ciphertext, passphrase)
$$;

-- Deterministic, keyed, one-way. Equality/uniqueness only. Must stay
-- byte-for-byte compatible with intake.crypto.blind_index().
CREATE OR REPLACE FUNCTION app_blind_index(plain TEXT, pepper TEXT)
RETURNS BYTEA
LANGUAGE sql IMMUTABLE STRICT
AS $$
    SELECT hmac(lower(btrim(plain)), pepper, 'sha256')
$$;

-- Trigram indexes power both ILIKE '%x%' and the `%` similarity operator used
-- by the contractor's multi-attribute fuzzy candidate filter.
CREATE INDEX IF NOT EXISTS idx_profiles_place_trgm
    ON candidate_profiles USING GIN (place gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_profiles_stream_trgm
    ON candidate_profiles USING GIN (stream gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_profiles_category_trgm
    ON candidate_profiles USING GIN (category gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_profiles_qual_trgm
    ON candidate_profiles USING GIN (qualification gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_skills_name_trgm
    ON skills USING GIN (name gin_trgm_ops);

-- The blind-index lookup path: token_hash first so a name search is a plain
-- indexed equality probe.
CREATE INDEX IF NOT EXISTS idx_name_tokens_hash
    ON candidate_name_tokens (token_hash, profile_id);
"""

REVERSE_SQL = """
DROP INDEX IF EXISTS idx_name_tokens_hash;
DROP INDEX IF EXISTS idx_skills_name_trgm;
DROP INDEX IF EXISTS idx_profiles_qual_trgm;
DROP INDEX IF EXISTS idx_profiles_category_trgm;
DROP INDEX IF EXISTS idx_profiles_stream_trgm;
DROP INDEX IF EXISTS idx_profiles_place_trgm;
DROP FUNCTION IF EXISTS app_blind_index(TEXT, TEXT);
DROP FUNCTION IF EXISTS app_decrypt(BYTEA, TEXT);
DROP FUNCTION IF EXISTS app_encrypt(TEXT, TEXT);
-- Extensions are deliberately NOT dropped: other schemas may depend on them.
"""


def _run(sql):
    """Execute SQL only on PostgreSQL; no-op elsewhere."""

    def inner(apps, schema_editor):
        if schema_editor.connection.vendor != "postgresql":
            return
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(sql)

    return inner


class Migration(migrations.Migration):

    dependencies = [
        ("intake", "0006_skill_intakemedicalrecord_storage_key_and_more"),
    ]

    operations = [
        migrations.RunPython(_run(FORWARD_SQL), _run(REVERSE_SQL)),
    ]
