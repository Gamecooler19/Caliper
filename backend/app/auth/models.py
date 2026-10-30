"""User account ORM model (Module 6)."""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class UserRole(enum.StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    AUDITOR = "auditor"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class User(Base):
    """One person with access to Caliper.

    ``email`` is stored lowercased (enforced both by the code that
    writes it and by a CHECK constraint) and uniquely indexed.
    ``totp_secret_encrypted`` holds the Fernet-encrypted TOTP secret
    (see ``app.security.crypto``); it is NULL until enrollment begins,
    and ``totp_enabled`` only becomes true once the user has proved
    possession of the secret with a valid code.
    """

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("email = lower(email)", name="ck_users_email_lowercase"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_login_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
