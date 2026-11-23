from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.user import User


class RoomVisibility(StrEnum):
    public = "public"
    private = "private"


class InvitationStatus(StrEnum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"
    revoked = "revoked"


def _pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (
        CheckConstraint("length(name) BETWEEN 3 AND 64", name="name_length"),
        CheckConstraint("member_count >= 0", name="member_count_non_negative"),
        Index("ix_rooms_owner", "owner_id"),
        Index("ix_rooms_public_name", "name", postgresql_where=text("visibility = 'public'")),
        # Catalog search: ILIKE '%q%' over name + description (migration 0003 defines the GIN index).
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(CITEXT, unique=True)
    description: Mapped[str] = mapped_column(String(1000), server_default="")
    visibility: Mapped[RoomVisibility] = mapped_column(_pg_enum(RoomVisibility, "room_visibility"))
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Denormalised for the catalog; maintained by app/services/rooms.py in the same transaction.
    member_count: Mapped[int] = mapped_column(Integer, server_default="0")

    owner: Mapped[User] = relationship(lazy="joined")


class RoomMember(Base):
    __tablename__ = "room_members"
    __table_args__ = (
        Index("ix_room_members_user", "user_id", "room_id"),
        Index("ix_room_members_admins", "room_id", postgresql_where=text("is_admin")),
    )

    room_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default="false")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(lazy="joined")


class RoomBan(Base):
    __tablename__ = "room_bans"
    __table_args__ = (Index("ix_room_bans_user_id", "user_id"),)

    room_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    banned_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    banned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str | None] = mapped_column(String(500))


class RoomInvitation(Base):
    __tablename__ = "room_invitations"
    __table_args__ = (
        Index(
            "ux_room_invitations_pending",
            "room_id",
            "invitee_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        Index("ix_room_invitations_invitee_status", "invitee_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    room_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("rooms.id", ondelete="CASCADE"))
    invitee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    inviter_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[InvitationStatus] = mapped_column(
        _pg_enum(InvitationStatus, "invitation_status"), server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    room: Mapped[Room] = relationship(lazy="joined")
    invitee: Mapped[User] = relationship(foreign_keys=[invitee_id], lazy="joined")
    inviter: Mapped[User | None] = relationship(foreign_keys=[inviter_id], lazy="joined")


class ChatReadState(Base):
    """Per-user read cursor of a chat. Stage 3 knows only rooms; migration 0005 adds `dm_id` and turns
    the one-chat CHECK into `num_nonnulls(room_id, dm_id) = 1` (docs/DATA_MODEL.md §2.13)."""

    __tablename__ = "chat_read_states"
    __table_args__ = (
        CheckConstraint("num_nonnulls(room_id) = 1", name="one_chat"),
        Index(
            "ux_chat_read_states_user_room",
            "user_id",
            "room_id",
            unique=True,
            postgresql_where=text("room_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    room_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("rooms.id", ondelete="CASCADE"))
    # No FK: the message may be deleted.
    last_read_message_id: Mapped[int] = mapped_column(BigInteger, server_default="0")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
