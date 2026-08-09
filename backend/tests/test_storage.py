"""Private object storage — keys, expiring links, and the S3 presign contract.

The S3 backend is exercised against a stubbed boto3 client rather than a live
bucket: what matters here is that we ask for a *presigned* GET with a bounded
TTL and never hand out a public URL.
"""
import time
from unittest.mock import MagicMock, patch

import pytest

from intake import storage

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
def test_keys_are_opaque_and_never_reuse_the_filename():
    key = storage._build_key("worker_docs", "application/pdf", "ravi-kumar-aadhaar.pdf")

    assert "ravi" not in key.lower()
    assert key.startswith("gate-intake/worker_docs/")
    assert key.endswith(".pdf")


def test_keys_are_unique_per_upload():
    args = ("worker_docs", "image/jpeg", "scan.jpg")
    assert storage._build_key(*args) != storage._build_key(*args)


@pytest.mark.parametrize(
    "content_type,extension",
    [("application/pdf", ".pdf"), ("image/png", ".png"), ("image/jpeg", ".jpg")],
)
def test_extension_follows_the_content_type(content_type, extension):
    assert storage._build_key("resumes", content_type, None).endswith(extension)


# ---------------------------------------------------------------------------
# Local fallback backend
# ---------------------------------------------------------------------------
def test_local_backend_is_selected_without_an_s3_endpoint(settings):
    settings.S3_ENDPOINT_URL = ""

    assert isinstance(storage.get_storage(), storage.LocalSignedStorage)


def test_local_links_are_signed_and_expire(settings):
    backend = storage.LocalSignedStorage()
    key = backend.upload(b"scan bytes", "application/pdf", "worker_docs")

    url = backend.signed_url(key)

    assert "signature=" in url and "expires=" in url
    # A tampered key fails verification.
    expires = int(time.time()) + 900
    signature = backend._signature(key, expires)
    assert backend.verify(key, expires, signature) is True
    assert backend.verify(key + "x", expires, signature) is False
    # An expired link fails even with a correct signature.
    stale = int(time.time()) - 1
    assert backend.verify(key, stale, backend._signature(key, stale)) is False


def test_local_backend_refuses_path_traversal():
    backend = storage.LocalSignedStorage()

    with pytest.raises(storage.StorageError):
        backend.path_for("../../../../etc/passwd")


def test_signed_object_view_serves_only_valid_links(api, settings):
    settings.S3_ENDPOINT_URL = ""
    backend = storage.LocalSignedStorage()
    key = backend.upload(b"private scan", "application/pdf", "worker_docs")

    ok = api.get(backend.signed_url(key))
    assert ok.status_code == 200
    assert b"".join(ok.streaming_content) == b"private scan"

    tampered = api.get(
        "/api/storage/object/", {"key": key, "expires": 99999999999, "signature": "nope"}
    )
    assert tampered.status_code == 403


def test_object_view_requires_no_auth_because_the_link_is_the_capability(api, settings):
    """Same model as an S3 presigned URL: possession of the link is the grant."""
    settings.S3_ENDPOINT_URL = ""
    backend = storage.LocalSignedStorage()
    key = backend.upload(b"x", "application/pdf", "worker_docs")

    assert api.get(backend.signed_url(key)).status_code == 200


# ---------------------------------------------------------------------------
# S3 backend
# ---------------------------------------------------------------------------
@pytest.fixture
def s3_settings(settings):
    settings.S3_ENDPOINT_URL = "https://project.supabase.co/storage/v1/s3"
    settings.S3_BUCKET = "gate-intake-docs"
    settings.S3_ACCESS_KEY_ID = "key"
    settings.S3_SECRET_ACCESS_KEY = "secret"
    settings.S3_REGION = "ap-south-1"
    return settings


def test_s3_backend_is_selected_when_configured(s3_settings):
    with patch("boto3.client", return_value=MagicMock()):
        assert isinstance(storage.get_storage(), storage.ObjectStorage)


def test_upload_never_sets_a_public_acl(s3_settings):
    client = MagicMock()
    with patch("boto3.client", return_value=client):
        storage.ObjectStorage().upload(b"bytes", "application/pdf", "worker_docs")

    kwargs = client.put_object.call_args.kwargs
    assert "ACL" not in kwargs                      # inherits the private bucket policy
    assert kwargs["CacheControl"] == "private, no-store"
    assert kwargs["Bucket"] == "gate-intake-docs"


def test_reads_go_through_a_bounded_presigned_url(s3_settings):
    client = MagicMock()
    client.generate_presigned_url.return_value = "https://signed.example/obj?X-Amz-..."
    with patch("boto3.client", return_value=client):
        url = storage.ObjectStorage().signed_url("gate-intake/worker_docs/x.pdf")

    assert url.startswith("https://signed.example/")
    args, kwargs = client.generate_presigned_url.call_args
    assert args[0] == "get_object"
    assert kwargs["ExpiresIn"] == s3_settings.PRESIGN_EXPIRY_SECONDS
    # Content type is pinned so an object can never be served as executable.
    assert kwargs["Params"]["ResponseContentType"] == "application/pdf"
    assert kwargs["Params"]["ResponseContentDisposition"] == "inline"


def test_presign_ttl_is_clamped_to_the_s3_signature_limit(settings):
    """S3 rejects signatures beyond 7 days; we cap far tighter than that."""
    assert 60 <= settings.PRESIGN_EXPIRY_SECONDS <= 3600


def test_a_failed_presign_degrades_to_none_instead_of_500ing(s3_settings):
    client = MagicMock()
    client.generate_presigned_url.side_effect = RuntimeError("network down")
    with patch("boto3.client", return_value=client):
        assert storage.ObjectStorage().signed_url("some/key.pdf") is None


def test_upload_failure_raises_storage_error(s3_settings):
    from botocore.exceptions import BotoCoreError

    client = MagicMock()
    client.put_object.side_effect = BotoCoreError()
    with patch("boto3.client", return_value=client):
        with pytest.raises(storage.StorageError):
            storage.ObjectStorage().upload(b"x", "application/pdf", "worker_docs")


# ---------------------------------------------------------------------------
# Concurrent fan-out
# ---------------------------------------------------------------------------
def test_upload_many_stores_every_slot(settings):
    settings.S3_ENDPOINT_URL = ""
    items = [
        {"slot": slot, "data": b"bytes", "content_type": "application/pdf",
         "prefix": "worker_docs", "filename": f"{slot}.pdf"}
        for slot in ("aadhaar", "pan", "medical", "pvc", "resume")
    ]

    keys = storage.upload_many(items)

    assert set(keys) == {"aadhaar", "pan", "medical", "pvc", "resume"}
    assert len(set(keys.values())) == 5      # distinct keys


def test_one_failed_slot_does_not_sink_the_others(settings):
    settings.S3_ENDPOINT_URL = ""
    real_upload = storage.LocalSignedStorage.upload

    def flaky(self, data, content_type, prefix, original_name=None):
        if original_name == "pan.pdf":
            raise storage.StorageError("boom")
        return real_upload(self, data, content_type, prefix, original_name)

    items = [
        {"slot": "aadhaar", "data": b"a", "content_type": "application/pdf",
         "prefix": "worker_docs", "filename": "aadhaar.pdf"},
        {"slot": "pan", "data": b"b", "content_type": "application/pdf",
         "prefix": "worker_docs", "filename": "pan.pdf"},
    ]

    with patch.object(storage.LocalSignedStorage, "upload", flaky):
        keys = storage.upload_many(items)

    assert "aadhaar" in keys
    assert "pan" not in keys


def test_upload_many_of_nothing_is_a_no_op():
    assert storage.upload_many([]) == {}
