from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

FILE_MAX_BYTES = 20 * 1024 * 1024  # 20 971 520
IMAGE_MAX_BYTES = 3 * 1024 * 1024  # 3 145 728


class Attachment(Base):
    """A stored file of a room or DM (docs/DATA_MODEL.md §2.12). `message_id IS NULL` = pending: uploaded,
    not sent yet, visible only to the uploader; GC removes pending ones after 24 h."""

    __tablename__ = "attachments"
    __table_args__ = (
        CheckConstraint("num_nonnulls(room_id, dm_id) = 1", name="one_chat"),
        CheckConstraint(
            f"size_bytes > 0 AND size_bytes <= {FILE_MAX_BYTES} "
            f"AND (NOT is_image OR size_bytes <= {IMAGE_MAX_BYTES})",
            name="size_limits",
        ),
        Index("ix_attachments_message_id", "message_id"),
        Index("ix_attachments_room_id", "room_id"),
        Index("ix_attachments_dm_id", "dm_id"),
        Index("ix_attachments_pending", "created_at", postgresql_where=text("message_id IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("messages.id", ondelete="CASCADE"))
    room_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("rooms.id", ondelete="CASCADE"))
    dm_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("direct_conversations.id", ondelete="CASCADE")
    )
    uploader_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    # Relative to UPLOAD_DIR, e.g. rooms/12/2026/11/<uuid4>; never contains user input.
    stored_path: Mapped[str] = mapped_column(Text, unique=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(127))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    is_image: Mapped[bool] = mapped_column(Boolean)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
