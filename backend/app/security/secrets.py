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
    variable name and whether the deployment can run without it.
    """

    env_var: str
    required: bool = True
    description: str = ""


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
    required=False,
    description="Bring-your-own-key credential for the Meta Model API. "
    "Not required by Module 1: no caller in this module invokes any "
    "external model API. Required once Modules 2/3 (vision, intake "
    "parsing, report generation) are wired in.",
)


def load_secret(spec: SecretSpec) -> str | None:
    """Read one secret from the environment according to its
    :class:`SecretSpec`.

    Returns the value, or ``None`` if the spec is not required and the
    variable is unset. Raises :class:`MissingSecretError` if the spec is
    required and the variable is unset or empty.
    """

    value = os.environ.get(spec.env_var)

    if value is None or value == "":
        if spec.required:
            raise MissingSecretError(
                f"Required secret '{spec.env_var}' is not set in the "
                f"environment. {spec.description}".strip()
            )
        return None

    return value


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


def load_meta_model_api_key() -> str | None:
    """Load the BYOK Meta Model API key, if this deployment has configured
    one. Returns ``None`` when unset -- this module never calls the Meta
    Model API itself, so an unset key is not an error at this layer.
    """

    return load_secret(META_MODEL_API_KEY)
