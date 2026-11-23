"""friendships, user_bans, direct_conversations; dm_id in messages and chat_read_states.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

friendship_status = postgresql.ENUM("pending", "accepted", name="friendship_status", create_type=False)


def upgrade() -> None:
    friendship_status.create(op.get_bind())

    op.create_table(
        "friendships",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("requester_id", sa.BigInteger(), nullable=False),
        sa.Column("addressee_id", sa.BigInteger(), nullable=False),
        sa.Column("status", friendship_status, server_default="pending", nullable=False),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["requester_id"], ["users.id"], name=op.f("fk_friendships_requester_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["addressee_id"], ["users.id"], name=op.f("fk_friendships_addressee_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_friendships")),
        sa.CheckConstraint("requester_id <> addressee_id", name=op.f("ck_friendships_not_self")),
    )
    # One row per unordered pair: a request "back" is never a second row.
    op.execute(
        "CREATE UNIQUE INDEX ux_friendships_pair ON friendships "
        "(LEAST(requester_id, addressee_id), GREATEST(requester_id, addressee_id))"
    )
    op.create_index("ix_friendships_addressee_status", "friendships", ["addressee_id", "status"])
    op.create_index("ix_friendships_requester_status", "friendships", ["requester_id", "status"])

    op.create_table(
        "user_bans",
        sa.Column("banner_id", sa.BigInteger(), nullable=False),
        sa.Column("banned_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["banner_id"], ["users.id"], name=op.f("fk_user_bans_banner_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["banned_id"], ["users.id"], name=op.f("fk_user_bans_banned_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("banner_id", "banned_id", name=op.f("pk_user_bans")),
        sa.CheckConstraint("banner_id <> banned_id", name=op.f("ck_user_bans_not_self")),
    )
    op.create_index("ix_user_bans_banned_id", "user_bans", ["banned_id"])

    op.create_table(
        "direct_conversations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_a_id", sa.BigInteger(), nullable=True),
        sa.Column("user_b_id", sa.BigInteger(), nullable=True),
        sa.Column("user_a_orig_id", sa.BigInteger(), nullable=False),
        sa.Column("user_b_orig_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_a_id"],
            ["users.id"],
            name=op.f("fk_direct_conversations_user_a_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_b_id"],
            ["users.id"],
            name=op.f("fk_direct_conversations_user_b_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_direct_conversations")),
        sa.CheckConstraint(
            "user_a_orig_id < user_b_orig_id", name=op.f("ck_direct_conversations_canonical_order")
        ),
    )
    op.create_index(
        "ux_direct_conversations_pair",
        "direct_conversations",
        ["user_a_orig_id", "user_b_orig_id"],
        unique=True,
    )
    op.create_index("ix_direct_conversations_user_a_id", "direct_conversations", ["user_a_id"])
    op.create_index("ix_direct_conversations_user_b_id", "direct_conversations", ["user_b_id"])

    for table in ("messages", "chat_read_states"):
        op.add_column(table, sa.Column("dm_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            op.f(f"fk_{table}_dm_id_direct_conversations"),
            table,
            "direct_conversations",
            ["dm_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.drop_constraint(op.f(f"ck_{table}_one_chat"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_one_chat"), table, "num_nonnulls(room_id, dm_id) = 1")
    op.create_index(
        "ix_messages_dm_id",
        "messages",
        ["dm_id", sa.text("id DESC")],
        postgresql_where=sa.text("dm_id IS NOT NULL"),
    )
    op.create_index(
        "ux_chat_read_states_user_dm",
        "chat_read_states",
        ["user_id", "dm_id"],
        unique=True,
        postgresql_where=sa.text("dm_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_chat_read_states_user_dm", table_name="chat_read_states")
    op.drop_index("ix_messages_dm_id", table_name="messages")
    for table in ("messages", "chat_read_states"):
        op.execute(f"DELETE FROM {table} WHERE dm_id IS NOT NULL")
        op.drop_constraint(op.f(f"ck_{table}_one_chat"), table, type_="check")
        op.create_check_constraint(op.f(f"ck_{table}_one_chat"), table, "num_nonnulls(room_id) = 1")
        op.drop_column(table, "dm_id")
    op.drop_table("direct_conversations")
    op.drop_table("user_bans")
    op.drop_table("friendships")
    friendship_status.drop(op.get_bind())
