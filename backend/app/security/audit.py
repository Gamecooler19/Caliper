"""Append-only audit logging.

Per architecture.md's Security posture: "An append-only audit log
recording who ran a classification, what was submitted, and what was
returned." This module writes :class:`AuditLogEntry` records via the
async database session; it is the only writer of that table in the
codebase.

This module performs I/O (database writes) and is therefore kept
strictly separate from ``app.classification.engine``, which must not.
Nothing here makes an external network call.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.classification.models import AuditLogEntry
from app.db import AdminAuditLogRecord, AuditLogRecord


def build_audit_entry(
    actor: str,
    action: str,
    classification_result_id: str | None = None,
    timestamp: dt.datetime | None = None,
) -> AuditLogEntry:
    """Construct an :class:`AuditLogEntry` for a classification run.

    Pure construction, no I/O -- callers persist it separately via
    :func:`record_audit_entry`. Kept as a standalone function so tests can
    assert on the entry's shape without touching a database.
    """

    if not actor or not actor.strip():
        raise ValueError("actor is required for an audit log entry.")
    if not action or not action.strip():
        raise ValueError("action is required for an audit log entry.")

    effective_timestamp = timestamp or dt.datetime.now(dt.UTC)

    return AuditLogEntry(
        actor=actor,
        action=action,
        timestamp=effective_timestamp.isoformat(),
        classification_result_id=classification_result_id,
    )


async def record_audit_entry(
    session: AsyncSession,
    entry: AuditLogEntry,
    user_id: uuid.UUID | None = None,
) -> AuditLogRecord:
    """Persist an :class:`AuditLogEntry` to the append-only audit_log table.

    Called on every classification run. Only inserts; never updates or
    deletes an existing row.

    ``user_id`` (Module 6) is the acting account for session-authenticated
    requests and stays ``None`` for API-key calls. It is a parameter here,
    not a field on ``AuditLogEntry``, because that model lives in
    ``app.classification`` (Module 1), which this module does not touch.
    """

    record = AuditLogRecord(
        actor=entry.actor,
        action=entry.action,
        timestamp=dt.datetime.fromisoformat(entry.timestamp),
        classification_result_id=entry.classification_result_id,
        user_id=user_id,
    )
    session.add(record)
    await session.flush()
    return record


#: Valid ``action`` values for :func:`record_admin_audit_entry` (Module 6
#: fix #3). Kept as a plain tuple of strings, not an enum, since this is
#: the audit record's own vocabulary, independent of any ORM enum.
ADMIN_AUDIT_ACTIONS = (
    "user_created",
    "role_changed",
    "user_deactivated",
    "user_reactivated",
    "totp_reset",
)


async def record_admin_audit_entry(
    session: AsyncSession,
    *,
    actor_user_id: uuid.UUID,
    action: str,
    target_user_id: uuid.UUID | None = None,
    details: dict | None = None,
) -> AdminAuditLogRecord:
    """Persist one append-only entry to ``admin_audit_log`` (Module 6 fix
    #3), the generic account-administration audit trail distinct from
    :class:`AuditLogRecord` (Module 1's classification-run audit log,
    which this function never touches).

    Called from every mutating ``/admin/users*`` endpoint, including the
    reset-totp action (migrated here for consistency so all admin
    actions share one audit trail). Only inserts; never updates or
    deletes an existing row.
    """

    if action not in ADMIN_AUDIT_ACTIONS:
        raise ValueError(f"Unknown admin audit action: {action!r}")

    record = AdminAuditLogRecord(
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        action=action,
        details=details or {},
    )
    session.add(record)
    await session.flush()
    return record
