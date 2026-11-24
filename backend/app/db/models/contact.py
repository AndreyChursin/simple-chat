from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.user import User


class FriendshipStatus(StrEnum):
    pending = "pending"
    accepted = "accepted"


class Friendship(Base):
    """One row per unordered pair: a pending request or an accepted friendship (docs/DATA_MODEL.md §2.4).
    Declining a request or removing a friend deletes the row."""

    __tablename__ = "friendships"
    __table_args__ = (
        CheckConstraint("requester_id <> addressee_id", name="not_self"),
        Index(
            "ux_friendships_pair",
            text("LEAST(requester_id, addressee_id)"),
            text("GREATEST(requester_id, addressee_id)"),
            unique=True,
        ),
        Index("ix_friendships_addressee_status", "addressee_id", "status"),
        Index("ix_friendships_requester_status", "requester_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    requester_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    addressee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[FriendshipStatus] = mapped_column(
        Enum(FriendshipStatus, name="friendship_status", values_callable=lambda e: [m.value for m in e]),
        server_default="pending",
    )
    message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    requester: Mapped[User] = relationship(foreign_keys=[requester_id], lazy="joined")
    addressee: Mapped[User] = relationship(foreign_keys=[addressee_id], lazy="joined")

    def other(self, user_id: int) -> User:
        return self.addressee if self.requester_id == user_id else self.requester


class UserBan(Base):
    """`banner` blocked `banned` (docs/DATA_MODEL.md §2.5). Either direction blocks the pair."""

    __tablename__ = "user_bans"
    __table_args__ = (
        CheckConstraint("banner_id <> banned_id", name="not_self"),
        Index("ix_user_bans_banned_id", "banned_id"),
    )

    banner_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    banned_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    banned: Mapped[User] = relationship(foreign_keys=[banned_id], lazy="joined")


class DirectConversation(Base):
    """A DM: a chat of two fixed participants without admins (docs/DATA_MODEL.md §2.10). Whether it is
    writable is not stored: both sides exist, are friends and neither banned the other."""

    __tablename__ = "direct_conversations"
    __table_args__ = (
        CheckConstraint("user_a_orig_id < user_b_orig_id", name="canonical_order"),
        Index("ux_direct_conversations_pair", "user_a_orig_id", "user_b_orig_id", unique=True),
        Index("ix_direct_conversations_user_a_id", "user_a_id"),
        Index("ix_direct_conversations_user_b_id", "user_b_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # NULL once that user deleted their account; the DM stays (frozen) for the other side.
    user_a_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    user_b_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    user_a_orig_id: Mapped[int] = mapped_column(BigInteger)
    user_b_orig_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user_a: Mapped[User | None] = relationship(foreign_keys=[user_a_id], lazy="joined")
    user_b: Mapped[User | None] = relationship(foreign_keys=[user_b_id], lazy="joined")

    def has_side(self, user_id: int) -> bool:
        return user_id in (self.user_a_id, self.user_b_id)

    def peer_orig_id(self, user_id: int) -> int:
        return self.user_b_orig_id if self.user_a_orig_id == user_id else self.user_a_orig_id

    def peer(self, user_id: int) -> User | None:
        """The other side, None if they deleted their account."""
        return self.user_b if self.user_a_orig_id == user_id else self.user_a
