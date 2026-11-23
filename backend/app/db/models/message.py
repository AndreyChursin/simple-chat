import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.attachment import Attachment
from app.db.models.user import User

MESSAGE_MAX_BYTES = 3072


class Message(Base):
    """A chat message of a room or a DM: exactly one of `room_id` / `dm_id` (docs/DATA_MODEL.md §2.11).

    Relationships are lazy="raise": queries state their eager loads explicitly
    (app/services/messages.py MESSAGE_LOAD) so a page of history is a single SELECT."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("num_nonnulls(room_id, dm_id) = 1", name="one_chat"),
        CheckConstraint(f"octet_length(content) <= {MESSAGE_MAX_BYTES}", name="content_max_bytes"),
        Index(
            "ix_messages_room_id", "room_id", text("id DESC"), postgresql_where=text("room_id IS NOT NULL")
        ),
        Index("ix_messages_dm_id", "dm_id", text("id DESC"), postgresql_where=text("dm_id IS NOT NULL")),
        Index(
            "ux_messages_client",
            "author_id",
            "client_msg_id",
            unique=True,
            postgresql_where=text("client_msg_id IS NOT NULL"),
        ),
        Index(
            "ix_messages_reply",
            "reply_to_message_id",
            postgresql_where=text("reply_to_message_id IS NOT NULL"),
        ),
        # ix_messages_content_trgm (GIN, history search) is defined in migration 0004.
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    room_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("rooms.id", ondelete="CASCADE"))
    dm_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("direct_conversations.id", ondelete="CASCADE")
    )
    author_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    client_msg_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    content: Mapped[str] = mapped_column(Text, server_default="")
    reply_to_message_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("messages.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))

    author: Mapped[User | None] = relationship(foreign_keys=[author_id], lazy="raise")
    deleted_by: Mapped[User | None] = relationship(foreign_keys=[deleted_by_id], lazy="raise")
    reply_to: Mapped["Message | None"] = relationship(remote_side=[id], lazy="raise")
    attachments: Mapped[list[Attachment]] = relationship(
        lazy="raise", order_by=Attachment.id, passive_deletes=True
    )
