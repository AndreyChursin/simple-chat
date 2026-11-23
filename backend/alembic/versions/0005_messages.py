"""messages (rooms only; the contacts/DM migration adds dm_id and widens the one-chat CHECK).

Attachments get their own migration with stage 7.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("room_id", sa.BigInteger(), nullable=True),
        sa.Column("author_id", sa.BigInteger(), nullable=True),
        sa.Column("client_msg_id", sa.Uuid(), nullable=True),
        sa.Column("content", sa.Text(), server_default="", nullable=False),
        sa.Column("reply_to_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.id"], name="fk_messages_room_id_rooms", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name="fk_messages_author_id_users", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["reply_to_message_id"],
            ["messages.id"],
            name="fk_messages_reply_to_message_id_messages",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["deleted_by_id"], ["users.id"], name="fk_messages_deleted_by_id_users", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_messages"),
        # op.f(): the name is final; without it the ck naming convention is applied a second time.
        sa.CheckConstraint("num_nonnulls(room_id) = 1", name=op.f("ck_messages_one_chat")),
        sa.CheckConstraint("octet_length(content) <= 3072", name=op.f("ck_messages_content_max_bytes")),
    )
    op.create_index(
        "ix_messages_room_id",
        "messages",
        ["room_id", sa.text("id DESC")],
        postgresql_where=sa.text("room_id IS NOT NULL"),
    )
    op.create_index(
        "ux_messages_client",
        "messages",
        ["author_id", "client_msg_id"],
        unique=True,
        postgresql_where=sa.text("client_msg_id IS NOT NULL"),
    )
    op.create_index(
        "ix_messages_reply",
        "messages",
        ["reply_to_message_id"],
        postgresql_where=sa.text("reply_to_message_id IS NOT NULL"),
    )
    op.execute(
        "CREATE INDEX ix_messages_content_trgm ON messages USING gin (content gin_trgm_ops) "
        "WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.drop_table("messages")
