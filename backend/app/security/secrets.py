"""Secrets loading layer.

Per architecture.md's Security posture: "Secrets -- including each
deployment's Meta Model API key -- held in a dedicated secrets layer,
never in environment files committed to the repo or hardcoded." This
module is the single place in the codebase permitted to read credential
values, and it reads them only from the process environment at call
time -- values that the deployment environment (container orchestrator,
systemd unit, CI secrets store, etc.) injects. Nothing is read from a
committed file, and nothing is cached to disk.

Nothing in this module makes a network call. It only reads
``os.environ``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class MissingSecretError(RuntimeError):
    """Raised when a required secret is not present in the environment."""


@dataclass(frozen=True)
class SecretSpec:
    """Declares one secret this deployment may need: its environment
    variable name, whether the deployment can run without it, and an
    optional default value.

    ``default`` is for values that have a sane, non-sensitive out-of-the-box
    choice (such as a model ID) that a deployment may still want to
    override -- e.g. SAM_MODEL_ID defaults to "sam-3.1" but can be pointed
    at a different model without a code change. A spec with a default is
    always resolvable, regardless of ``required``.
    """

    env_var: str
    required: bool = True
    description: str = ""
    default: str | None = None


# ---------------------------------------------------------------------------
# Known secrets. Add new entries here as new modules need credentials --
# never read os.environ directly outside this file.
# ---------------------------------------------------------------------------

DATABASE_URL = SecretSpec(
    env_var="DATABASE_URL",
    required=True,
    description="PostgreSQL 16 async connection string (SQLAlchemy 2 driver).",
)

VALKEY_URL = SecretSpec(
    env_var="VALKEY_URL",
    required=True,
    description="Valkey connection string, used as the Celery broker and "
    "the API rate limiter's backing store.",
)

META_MODEL_API_KEY = SecretSpec(
    env_var="META_MODEL_API_KEY",
    required=True,
    description="Bring-your-own-key credential for the Meta Model API. "
    "Required starting with Module 2 (vision pipeline): this is the first "
    "module that actually calls the Meta Model API, and a missing key "
    "must fail fast at startup rather than fail silently mid-request.",
)

SAM_MODEL_ID = SecretSpec(
    env_var="SAM_MODEL_ID",
    required=True,
    description="Model ID used for SAM media-segmentation calls over the "
    "Meta Model API Responses endpoint.",
    default="sam-3.1",
)

MUSE_SPARK_MODEL_ID = SecretSpec(
    env_var="MUSE_SPARK_MODEL_ID",
    required=True,
    description="Model ID used for Muse Spark multimodal (image "
    "understanding) calls over the Meta Model API Responses endpoint.",
    default="muse-spark-1.3",
)

STORAGE_ENDPOINT_URL = SecretSpec(
    env_var="STORAGE_ENDPOINT_URL",
    required=True,
    description="S3-compatible object storage endpoint URL for uploaded "
    "device media.",
)

STORAGE_ACCESS_KEY = SecretSpec(
    env_var="STORAGE_ACCESS_KEY",
    required=True,
    description="S3-compatible object storage access key.",
)

STORAGE_SECRET_KEY = SecretSpec(
    env_var="STORAGE_SECRET_KEY",
    required=True,
    description="S3-compatible object storage secret key.",
)

STORAGE_BUCKET = SecretSpec(
    env_var="STORAGE_BUCKET",
    required=True,
    description="S3-compatible object storage bucket name for uploaded "
    "device media.",
)


def load_secret(spec: SecretSpec) -> str | None:
    """Read one secret from the environment according to its
    :class:`SecretSpec`.

    Resolution order: the environment variable, then ``spec.default`` if
    set, then (if still unresolved) a :class:`MissingSecretError` when
    ``required`` is True or ``None`` when it is False.
    """

    value = os.environ.get(spec.env_var)

    if value is not None and value != "":
        return value

    if spec.default is not None:
        return spec.default

    if spec.required:
        raise MissingSecretError(
            f"Required secret '{spec.env_var}' is not set in the "
            f"environment. {spec.description}".strip()
        )

    return None


def load_database_url() -> str:
    """Load the PostgreSQL connection string. Raises if unset."""

    value = load_secret(DATABASE_URL)
    assert value is not None  # required=True guarantees this or a raise above
    return value


def load_valkey_url() -> str:
    """Load the Valkey connection string. Raises if unset."""

    value = load_secret(VALKEY_URL)
    assert value is not None  # required=True guarantees this or a raise above
    return value


def load_meta_model_api_key() -> str:
    """Load the BYOK Meta Model API key. Raises :class:`MissingSecretError`
    if unset -- this is now a required secret (see META_MODEL_API_KEY),
    since Module 2 is the first module that actually calls the Meta Model
    API and a missing key must fail fast at startup, not mid-request.
    """

    value = load_secret(META_MODEL_API_KEY)
    assert value is not None  # required=True guarantees this or a raise above
    return value


def load_sam_model_id() -> str:
    """Load the SAM segmentation model ID. Defaults to ``"sam-3.1"``."""

    value = load_secret(SAM_MODEL_ID)
    assert value is not None  # has a default, so always resolvable
    return value


def load_muse_spark_model_id() -> str:
    """Load the Muse Spark multimodal model ID. Defaults to
    ``"muse-spark-1.3"``.
    """

    value = load_secret(MUSE_SPARK_MODEL_ID)
    assert value is not None  # has a default, so always resolvable
    return value


def load_storage_endpoint_url() -> str:
    """Load the S3-compatible object storage endpoint URL. Raises if unset."""

    value = load_secret(STORAGE_ENDPOINT_URL)
    assert value is not None  # required=True guarantees this or a raise above
    return value


def load_storage_access_key() -> str:
    """Load the S3-compatible object storage access key. Raises if unset."""

    value = load_secret(STORAGE_ACCESS_KEY)
    assert value is not None  # required=True guarantees this or a raise above
    return value


def load_storage_secret_key() -> str:
    """Load the S3-compatible object storage secret key. Raises if unset."""

    value = load_secret(STORAGE_SECRET_KEY)
    assert value is not None  # required=True guarantees this or a raise above
    return value


def load_storage_bucket() -> str:
    """Load the S3-compatible object storage bucket name. Raises if unset."""

    value = load_secret(STORAGE_BUCKET)
    assert value is not None  # required=True guarantees this or a raise above
    return value
