"""Async SQLAlchemy engine, session, and ORM models.

This is the one module allowed to construct a database engine/session.
It reads its connection string through the secrets layer
(``app.security.secrets.load_database_url``), never from a hardcoded
value or a committed file.

Only one table exists so far: the append-only audit log described in
architecture.md's Security posture ("An append-only audit log recording
who ran a classification, what was submitted, and what was returned").
Business data (devices, classification history) is out of scope for
Module 1 -- see architecture.md's Build sequence.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.security.secrets import load_database_url


class Base(DeclarativeBase):
    """Declarative base for all Caliper ORM models."""


class AuditLogRecord(Base):
    """ORM persistence model for :class:`app.classification.models.AuditLogEntry`.

    Append-only by convention: nothing in this codebase issues an UPDATE
    or DELETE against this table. ``id`` is a server-independent UUID so
    log entries can be generated and referenced before a transaction
    commits.
    """

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    classification_result_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    #: Module 6: the account that performed the action. NULL for
    #: CALIPER_API_KEY-authenticated calls (no per-key identity).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=True
    )


def build_engine(database_url: str | None = None):
    """Create the async SQLAlchemy engine.

    ``database_url`` is accepted explicitly (useful for tests against a
    throwaway SQLite/Postgres URL); when omitted, it is loaded from the
    secrets layer, which in turn reads it from the environment.
    """

    url = database_url if database_url is not None else load_database_url()
    return create_async_engine(url, future=True)


def build_session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """Build an async session factory bound to ``engine``."""

    return async_sessionmaker(engine, expire_on_commit=False)


# Register the Module 6 ``users`` table on ``Base.metadata`` wherever
# ``Base`` is used (Alembic, ``Base.metadata.create_all`` in tests), since
# ``audit_log.user_id`` is a foreign key to it. Imported last because
# ``app.auth.models`` imports ``Base`` from this module.
import app.auth.models  # noqa: E402,F401
