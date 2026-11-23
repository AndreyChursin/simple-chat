from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT, INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("length(email) <= 254 AND position('@' in email) > 1", name="email_format"),
        CheckConstraint("username ~ '^[A-Za-z0-9_.-]{3,32}$'", name="username_format"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    # Immutable after registration: enforced by trigger users_username_immutable (migration 0002).
    username: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_user_sessions_user_last_seen", "user_id", "last_seen_at"),
        Index("ix_user_sessions_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    persistent: Mapped[bool] = mapped_column(Boolean)
    user_agent: Mapped[str] = mapped_column(Text, server_default="")
    ip: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(lazy="joined")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
