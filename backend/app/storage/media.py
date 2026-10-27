"""S3-compatible object storage access for uploaded device media.

Owns the two functions this module is responsible for: uploading raw
media bytes and fetching them back by key. Nothing outside this module
constructs an aioboto3 client or talks to object storage directly.

Credentials and endpoint configuration come exclusively from the secrets
layer (``app.security.secrets``), never hardcoded or read from a
committed file.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import aioboto3

from app.security.secrets import (
    load_storage_access_key,
    load_storage_bucket,
    load_storage_endpoint_url,
    load_storage_secret_key,
)


class MediaStorageError(RuntimeError):
    """Raised when object storage upload or fetch fails.

    Wraps the underlying aioboto3/botocore error so callers depending on
    this module don't need to import botocore exception types themselves.
    """


@asynccontextmanager
async def _s3_client():
    """Build a session-scoped aioboto3 S3 client from secrets-layer config."""

    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=load_storage_endpoint_url(),
        aws_access_key_id=load_storage_access_key(),
        aws_secret_access_key=load_storage_secret_key(),
    ) as client:
        yield client


def _new_storage_key(content_type: str) -> str:
    """Generate a storage key for a new upload.

    Keys are opaque UUIDs with a best-effort extension derived from the
    content type, under a fixed ``media/`` prefix so this bucket can
    later hold other object kinds without key collisions.
    """

    extension_by_content_type = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "video/mp4": "mp4",
        "video/quicktime": "mov",
    }
    extension = extension_by_content_type.get(content_type)
    suffix = f".{extension}" if extension else ""
    return f"media/{uuid.uuid4()}{suffix}"


async def upload_media(data: bytes, content_type: str) -> str:
    """Upload raw media bytes to object storage and return its storage key.

    Raises :class:`MediaStorageError` on any failure (auth, network,
    bucket misconfiguration) rather than returning a partial or guessed
    key.
    """

    key = _new_storage_key(content_type)
    bucket = load_storage_bucket()

    try:
        async with _s3_client() as client:
            await client.put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
            )
    except Exception as exc:  # noqa: BLE001 - wrap any backend error uniformly
        raise MediaStorageError(
            f"Failed to upload media to bucket '{bucket}' at key '{key}': {exc}"
        ) from exc

    return key


async def fetch_media(key: str) -> bytes:
    """Fetch raw media bytes from object storage by storage key.

    Raises :class:`MediaStorageError` on any failure, including a missing
    key, rather than returning empty or placeholder bytes.
    """

    bucket = load_storage_bucket()

    try:
        async with _s3_client() as client:
            response = await client.get_object(Bucket=bucket, Key=key)
            body = await response["Body"].read()
    except Exception as exc:  # noqa: BLE001 - wrap any backend error uniformly
        raise MediaStorageError(
            f"Failed to fetch media from bucket '{bucket}' at key '{key}': {exc}"
        ) from exc

    return body
