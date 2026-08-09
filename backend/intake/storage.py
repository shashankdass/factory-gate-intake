"""S3-compatible private object storage (Supabase Storage / Cloudflare R2 / MinIO).

This is the same storage mechanism the resume-scanner service uses, ported to
Django: one private bucket, opaque object keys, and **presigned GET URLs** as
the only way to read anything back.

The bucket is PRIVATE. Objects are never given a public ACL and no public base
URL is configured, so the only way to read a stored Aadhaar, PAN, medical PDF,
PVC or resume is a presigned URL minted by this module and valid for
``PRESIGN_EXPIRY_SECONDS`` (default 15 minutes).

Supabase Storage exposes an S3-compatible endpoint, so the same boto3 client
covers it:

    Supabase:      https://<project-ref>.supabase.co/storage/v1/s3
                   (S3_REGION must be the real project region — "auto" is rejected)
    Cloudflare R2: https://<account_id>.r2.cloudflarestorage.com
    MinIO (local): http://127.0.0.1:9000

boto3 is synchronous, so every *network* call is pushed onto a worker thread and
awaited — that is what lets a six-document unified intake upload concurrently.
URL presigning is pure local HMAC computation: no network, no thread needed,
safe to call once per row of a results page.

When ``S3_ENDPOINT_URL`` is unset the module falls back to a local filesystem
backend that keeps the same private + expiring-link contract, so ``runserver``
and the test suite work with zero configuration.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import mimetypes
import time
import uuid
from datetime import datetime, timezone

from django.conf import settings

logger = logging.getLogger(__name__)

EXTENSION_BY_MIME = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/pdf": ".pdf",
}

CONTENT_TYPE_BY_EXTENSION = {value: key for key, value in EXTENSION_BY_MIME.items()}


class StorageError(RuntimeError):
    """Raised when the object could not be persisted."""


def _build_key(prefix: str, content_type: str, original_name: str | None) -> str:
    """Opaque, unguessable key.

    The original filename is never reused — camera apps and file pickers often
    embed the worker's name in it, and the key travels in URLs.
    """
    extension = EXTENSION_BY_MIME.get((content_type or "").lower())
    if not extension and original_name:
        extension = mimetypes.guess_extension(content_type or "") or ""
    stamp = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    root = (settings.S3_KEY_PREFIX or "").strip("/")
    parts = [p for p in (root, prefix.strip("/"), stamp) if p]
    return f"{'/'.join(parts)}/{uuid.uuid4().hex}{extension or '.bin'}"


# ---------------------------------------------------------------------------
# S3-compatible backend
# ---------------------------------------------------------------------------
class ObjectStorage:
    """Private S3-compatible bucket. Reads happen only via presigned URLs."""

    def __init__(self):
        import boto3
        from botocore.client import Config as BotoConfig

        self._bucket = settings.S3_BUCKET
        self._presign_expiry = settings.PRESIGN_EXPIRY_SECONDS
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            region_name=settings.S3_REGION,
            config=BotoConfig(
                signature_version="s3v4",          # required for presigned URLs on R2
                s3={"addressing_style": "path"},   # required by R2 & Supabase
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=10,
                read_timeout=30,
            ),
        )

    @property
    def name(self) -> str:
        return "s3"

    # -- writes -------------------------------------------------------------
    def upload(self, data: bytes, content_type: str, prefix: str,
               original_name: str | None = None) -> str:
        """Store bytes under an opaque key and return that key."""
        from botocore.exceptions import BotoCoreError, ClientError

        key = _build_key(prefix, content_type, original_name)
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type or "application/octet-stream",
                # No ACL argument: the object inherits the bucket's private policy.
                CacheControl="private, no-store",
            )
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Object storage upload failed for key=%s", key)
            raise StorageError(f"Could not store the document: {exc}") from exc

        logger.info("Stored object key=%s (%d bytes)", key, len(data))
        return key

    # -- reads --------------------------------------------------------------
    def signed_url(self, key: str, expires_in: int | None = None) -> str | None:
        """Mint a short-lived, single-object GET URL.

        Local HMAC computation only — no network call, so this is safe to call
        once per row of a document table.
        """
        ttl = expires_in or self._presign_expiry
        extension = key[key.rfind("."):].lower() if "." in key else ""
        params: dict[str, str] = {"Bucket": self._bucket, "Key": key}
        content_type = CONTENT_TYPE_BY_EXTENSION.get(extension)
        if content_type:
            # Force the browser to render rather than download, and pin the type
            # so the object can never be served as executable content.
            params["ResponseContentType"] = content_type
            params["ResponseContentDisposition"] = "inline"

        try:
            return self._client.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=ttl
            )
        except Exception:  # noqa: BLE001 — a missing link must not 500 a dashboard
            logger.warning("Presign failed for %s", key, exc_info=True)
            return None

    # -- deletes ------------------------------------------------------------
    def delete(self, key: str) -> None:
        """Best-effort cleanup (used when a DB write fails after an upload)."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (ClientError, BotoCoreError):
            logger.warning("Orphaned object left in bucket: %s", key, exc_info=True)

    # -- health -------------------------------------------------------------
    def healthy(self) -> bool:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return True
        except Exception:  # noqa: BLE001 — health probe must never raise
            logger.warning("Object storage health check failed", exc_info=True)
            return False


# ---------------------------------------------------------------------------
# Local fallback backend (zero-config dev / CI)
# ---------------------------------------------------------------------------
class LocalSignedStorage:
    """Filesystem stand-in that preserves the private + expiring-link contract.

    Files land under ``MEDIA_ROOT/private/`` (never statically served) and links
    are HMAC-signed with an expiry, verified by ``StorageObjectView``. Used only
    when ``S3_ENDPOINT_URL`` is unset.
    """

    @property
    def name(self) -> str:
        return "local"

    @property
    def root(self):
        return settings.MEDIA_ROOT / "private"

    def upload(self, data: bytes, content_type: str, prefix: str,
               original_name: str | None = None) -> str:
        key = _build_key(prefix, content_type, original_name)
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        logger.info("Stored object %s (%d bytes) locally", key, len(data))
        return key

    @staticmethod
    def _signature(key: str, expires_at: int) -> str:
        message = f"{key}:{expires_at}".encode("utf-8")
        digest = hmac.new(
            settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def signed_url(self, key: str, expires_in: int | None = None) -> str | None:
        from urllib.parse import urlencode

        ttl = expires_in or settings.PRESIGN_EXPIRY_SECONDS
        expires_at = int(time.time()) + ttl
        query = urlencode({
            "key": key,
            "expires": expires_at,
            "signature": self._signature(key, expires_at),
        })
        return f"/api/storage/object/?{query}"

    def verify(self, key: str, expires_at: int, signature: str) -> bool:
        if expires_at < int(time.time()):
            return False
        return hmac.compare_digest(self._signature(key, expires_at), signature)

    def path_for(self, key: str):
        """Resolve a key to a path, refusing anything that escapes the root."""
        root = self.root.resolve()
        candidate = (root / key).resolve()
        if not str(candidate).startswith(str(root)):
            raise StorageError("Refusing to serve a path outside the storage root.")
        return candidate

    def delete(self, key: str) -> None:
        try:
            self.path_for(key).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 — best-effort cleanup
            logger.warning("Could not delete local object %s", key, exc_info=True)

    def healthy(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Backend selection + module-level helpers
# ---------------------------------------------------------------------------
def get_storage():
    """S3-compatible bucket when configured, else the local signed fallback."""
    if settings.S3_ENDPOINT_URL and settings.S3_BUCKET:
        return ObjectStorage()
    return LocalSignedStorage()


def upload(data: bytes, content_type: str, prefix: str,
           original_name: str | None = None) -> str:
    return get_storage().upload(data, content_type, prefix, original_name)


def signed_url(key: str | None, expires_in: int | None = None) -> str | None:
    """Fresh, expiring download link for a stored object key."""
    if not key:
        return None
    return get_storage().signed_url(key, expires_in)


def signed_urls(keys, expires_in: int | None = None) -> dict:
    """Batch variant — one link per key, for rendering a whole table."""
    storage = get_storage()
    return {key: storage.signed_url(key, expires_in) for key in keys if key}


def delete(key: str | None) -> None:
    if key:
        get_storage().delete(key)


# -- async fan-out ----------------------------------------------------------
async def upload_many_async(items: list[dict]) -> dict:
    """Upload several documents concurrently.

    ``items`` is a list of ``{"slot", "data", "content_type", "prefix",
    "filename"}``. Returns ``{slot: object_key}``; a slot whose upload failed is
    omitted and logged, so one bad scan never sinks the whole intake.
    """
    storage = get_storage()

    async def one(item: dict):
        try:
            key = await asyncio.to_thread(
                storage.upload,
                item["data"],
                item.get("content_type") or "application/octet-stream",
                item.get("prefix", "worker_docs"),
                item.get("filename"),
            )
            return item["slot"], key
        except Exception:  # noqa: BLE001 — reported per-slot, not fatal
            logger.exception("Upload failed for slot %s", item.get("slot"))
            return item["slot"], None

    results = await asyncio.gather(*(one(item) for item in items))
    return {slot: key for slot, key in results if key}


def upload_many(items: list[dict]) -> dict:
    """Synchronous entry point for DRF views (keeps the auth stack intact)."""
    if not items:
        return {}
    from asgiref.sync import async_to_sync

    return async_to_sync(upload_many_async)(items)
