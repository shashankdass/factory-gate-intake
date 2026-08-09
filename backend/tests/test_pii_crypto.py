"""PII encryption / decryption hooks and the keyed blind index.

The contract under test:
  * name / phone / email are never persisted in plaintext,
  * ciphertext is non-deterministic (so it cannot be compared or correlated),
  * a keyed HMAC blind index restores equality + prefix lookups anyway,
  * the API never leaks ciphertext, and masks PII unless the caller owns the row.
"""
import pytest

from intake import crypto
from intake.models import CandidateProfile, CandidateSkill, Skill

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "value",
    ["Ravi Kumar", "9876543210", "ravi.kumar@example.com", "Ünïcode Nâme", "x" * 300],
)
def test_encrypt_decrypt_round_trip(value):
    assert crypto.decrypt(crypto.encrypt(value)) == value


def test_ciphertext_is_bytes_and_not_the_plaintext():
    blob = crypto.encrypt("Ravi Kumar")

    assert isinstance(blob, bytes)
    assert b"Ravi" not in blob


def test_encryption_is_non_deterministic():
    """Same input, different ciphertext — which is why we need a blind index."""
    assert crypto.encrypt("ravi@example.com") != crypto.encrypt("ravi@example.com")


def test_empty_values_encrypt_to_none():
    assert crypto.encrypt("") is None
    assert crypto.encrypt("   ") is None
    assert crypto.encrypt(None) is None


def test_decrypt_of_none_is_none():
    assert crypto.decrypt(None) is None
    assert crypto.decrypt(b"") is None


def test_decrypt_never_raises_on_garbage():
    """A rotated or wrong key degrades to None rather than 500-ing a dashboard."""
    assert crypto.decrypt(b"not-a-valid-ciphertext-blob") is None


def test_decrypt_returns_none_under_a_different_key(settings):
    blob = crypto.encrypt("Ravi Kumar")
    settings.PII_ENCRYPTION_KEY = "a-completely-different-key-0123456789abcdef"

    assert crypto.decrypt(blob) is None


# ---------------------------------------------------------------------------
# Blind index
# ---------------------------------------------------------------------------
def test_blind_index_is_deterministic_and_normalised():
    assert crypto.blind_index("  RAVI@Example.com ") == crypto.blind_index("ravi@example.com")


def test_blind_index_is_32_bytes_and_distinct_per_value():
    digest = crypto.blind_index("9876543210")

    assert len(digest) == 32
    assert digest != crypto.blind_index("9876543211")


def test_blind_index_depends_on_the_pepper(settings):
    before = crypto.blind_index("9876543210")
    settings.PII_BLIND_INDEX_KEY = "another-pepper-entirely-abcdef0123456789"

    assert crypto.blind_index("9876543210") != before


def test_name_prefix_tokens_support_prefix_search():
    stored = set(crypto.name_tokens("Rajesh Kumar"))

    # "raj" and "kum" both resolve into the stored prefix set...
    assert set(crypto.name_query_tokens("raj")) <= stored
    assert set(crypto.name_query_tokens("kumar")) <= stored
    # ...but an unrelated name does not.
    assert not set(crypto.name_query_tokens("suresh")) & stored


def test_short_query_tokens_are_dropped():
    """Two-letter fragments would match far too broadly to be useful."""
    assert crypto.name_query_tokens("ra") == []


def test_masking_helpers():
    assert crypto.mask_phone("9876543210") == "••••••3210"
    assert crypto.mask_email("ravi.kumar@example.com") == "r•••••••••@example.com"
    assert crypto.mask_phone(None) == ""


# ---------------------------------------------------------------------------
# Model hooks
# ---------------------------------------------------------------------------
@pytest.fixture
def profile(db, compliant_worker, contractor):
    profile = CandidateProfile.objects.create(
        worker=compliant_worker, contractor=contractor,
        place="Pune", stream="Mechanical", category="Technician",
        years_of_experience=6, qualification="ITI",
        resume_key="gate-intake/resumes/2026/01/01/abc.pdf",
    )
    profile.set_pii(name="Ravi Kumar", phone="9876543210",
                    email="ravi.kumar@example.com")
    profile.save()
    profile.sync_name_tokens("Ravi Kumar")
    for raw in ["Welder", "Fitter"]:
        CandidateSkill.objects.create(
            profile=profile, skill=Skill.get_or_create_normalised(raw)
        )
    return profile


def test_set_pii_encrypts_and_indexes(profile):
    profile.refresh_from_db()

    assert profile.name == "Ravi Kumar"
    assert profile.phone == "9876543210"
    assert profile.email == "ravi.kumar@example.com"
    # BinaryField comes back as memoryview from the driver — normalise to compare.
    assert bytes(profile.phone_hash) == crypto.blind_index("9876543210")
    assert bytes(profile.email_hash) == crypto.blind_index("ravi.kumar@example.com")


def test_pii_is_not_stored_in_plaintext_in_the_database(profile):
    """Read the raw column straight from the DB — no ORM decryption in the way."""
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT name_encrypted, phone_encrypted, email_encrypted "
            "FROM candidate_profiles WHERE id = %s",
            [profile.id],
        )
        row = cursor.fetchone()

    for column in row:
        raw = bytes(column) if not isinstance(column, bytes) else column
        assert b"Ravi" not in raw
        assert b"9876543210" not in raw
        assert b"example.com" not in raw


def test_name_tokens_are_written_for_search(profile):
    stored = {bytes(t.token_hash) for t in profile.name_tokens.all()}

    assert stored == set(crypto.name_tokens("Ravi Kumar"))


def test_skills_are_normalised_into_the_shared_vocabulary(profile):
    assert sorted(cs.skill.name for cs in profile.candidate_skills.all()) == [
        "fitter", "welder"
    ]
    # "WELDER" and "welder" are the same vocabulary row.
    assert Skill.get_or_create_normalised("  WELDER ").id == Skill.objects.get(
        name="welder"
    ).id


# ---------------------------------------------------------------------------
# API exposure
# ---------------------------------------------------------------------------
def test_owner_sees_decrypted_pii_and_never_ciphertext(as_contractor, profile):
    body = as_contractor.get("/api/workers/").json()
    candidate = body[0]["candidate_profile"]

    assert candidate["name"] == "Ravi Kumar"
    assert candidate["phone"] == "9876543210"
    for leaked in ("name_encrypted", "phone_encrypted", "email_hash"):
        assert leaked not in candidate


def test_gate_security_never_receives_candidate_pii(as_gate, profile, approved_list,
                                                    compliant_worker):
    body = as_gate.get("/api/gate-check/", {"aadhar": compliant_worker.aadhar_number}).json()

    assert "candidate_profile" not in body["worker"]
    assert "9876543210" not in str(body)


def test_candidate_search_matches_on_the_blind_index(as_contractor, profile):
    """Name search never decrypts — it hashes the query and probes the index."""
    hit = as_contractor.get("/api/candidates/search/", {"q": "rav"}).json()
    miss = as_contractor.get("/api/candidates/search/", {"q": "suresh"}).json()

    assert hit["count"] == 1
    assert hit["results"][0]["worker"]["name"] == "Ravi Kumar"
    assert miss["count"] == 0


def test_candidate_search_filters_on_plaintext_attributes(as_contractor, profile):
    assert as_contractor.get("/api/candidates/search/", {"place": "pun"}).json()["count"] == 1
    assert as_contractor.get("/api/candidates/search/", {"skill": "welder"}).json()["count"] == 1
    assert as_contractor.get(
        "/api/candidates/search/", {"min_experience": 10}
    ).json()["count"] == 0


def test_candidate_search_is_scoped_to_the_calling_contractor(
    api, other_contractor, profile
):
    api.force_authenticate(user=other_contractor)

    assert api.get("/api/candidates/search/", {"q": "ravi"}).json()["count"] == 0
