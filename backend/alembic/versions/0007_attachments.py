"""attachments: files of rooms and DMs, pending until bound to a message.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachments",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("room_id", sa.BigInteger(), nullable=True),
        sa.Column("dm_id", sa.BigInteger(), nullable=True),
        sa.Column("uploader_id", sa.BigInteger(), nullable=True),
        sa.Column("stored_path", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=127), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("is_image", sa.Boolean(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            name=op.f("fk_attachments_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.id"], name=op.f("fk_attachments_room_id_rooms"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["dm_id"],
            ["direct_conversations.id"],
            name=op.f("fk_attachments_dm_id_direct_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploader_id"], ["users.id"], name=op.f("fk_attachments_uploader_id_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attachments")),
        sa.UniqueConstraint("stored_path", name=op.f("uq_attachments_stored_path")),
        sa.CheckConstraint("num_nonnulls(room_id, dm_id) = 1", name=op.f("ck_attachments_one_chat")),
        sa.CheckConstraint(
            "size_bytes > 0 AND size_bytes <= 20971520 AND (NOT is_image OR size_bytes <= 3145728)",
            name=op.f("ck_attachments_size_limits"),
        ),
    )
    op.create_index("ix_attachments_message_id", "attachments", ["message_id"])
    op.create_index("ix_attachments_room_id", "attachments", ["room_id"])
    op.create_index("ix_attachments_dm_id", "attachments", ["dm_id"])
    op.create_index(
        "ix_attachments_pending",
        "attachments",
        ["created_at"],
        postgresql_where=sa.text("message_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("attachments")
