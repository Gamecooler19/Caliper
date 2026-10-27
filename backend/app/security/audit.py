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

from sqlalchemy.ext.asyncio import AsyncSession

from app.classification.models import AuditLogEntry
from app.db import AuditLogRecord


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
    session: AsyncSession, entry: AuditLogEntry
) -> AuditLogRecord:
    """Persist an :class:`AuditLogEntry` to the append-only audit_log table.

    Called on every classification run. Only inserts; never updates or
    deletes an existing row.
    """

    record = AuditLogRecord(
        actor=entry.actor,
        action=entry.action,
        timestamp=dt.datetime.fromisoformat(entry.timestamp),
        classification_result_id=entry.classification_result_id,
    )
    session.add(record)
    await session.flush()
    return record
