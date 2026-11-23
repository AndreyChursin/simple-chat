"""rooms, room_members, room_bans, room_invitations, chat_read_states; owner-is-admin trigger.

chat_read_states is created here with room_id only; 0005 (contacts/DMs) adds dm_id and widens the
one-chat CHECK to num_nonnulls(room_id, dm_id) = 1.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

room_visibility = postgresql.ENUM("public", "private", name="room_visibility", create_type=False)
invitation_status = postgresql.ENUM(
    "pending", "accepted", "declined", "revoked", name="invitation_status", create_type=False
)


def upgrade() -> None:
    room_visibility.create(op.get_bind())
    invitation_status.create(op.get_bind())

    op.create_table(
        "rooms",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("name", postgresql.CITEXT(), nullable=False),
        sa.Column("description", sa.String(length=1000), server_default="", nullable=False),
        sa.Column("visibility", room_visibility, nullable=False),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("member_count", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_rooms_owner_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_rooms"),
        sa.UniqueConstraint("name", name="uq_rooms_name"),
        sa.CheckConstraint("length(name) BETWEEN 3 AND 64", name="ck_rooms_name_length"),
        sa.CheckConstraint("member_count >= 0", name="ck_rooms_member_count_non_negative"),
    )
    op.create_index("ix_rooms_owner", "rooms", ["owner_id"])
    op.create_index(
        "ix_rooms_public_name", "rooms", ["name"], postgresql_where=sa.text("visibility = 'public'")
    )
    op.execute(
        "CREATE INDEX ix_rooms_search_trgm ON rooms "
        "USING gin ((name::text || ' ' || description) gin_trgm_ops) WHERE visibility = 'public'"
    )

    op.create_table(
        "room_members",
        sa.Column("room_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.id"], name="fk_room_members_room_id_rooms", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_room_members_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("room_id", "user_id", name="pk_room_members"),
    )
    op.create_index("ix_room_members_user", "room_members", ["user_id", "room_id"])
    op.create_index(
        "ix_room_members_admins", "room_members", ["room_id"], postgresql_where=sa.text("is_admin")
    )

    op.create_table(
        "room_bans",
        sa.Column("room_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("banned_by_id", sa.BigInteger(), nullable=True),
        sa.Column("banned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.id"], name="fk_room_bans_room_id_rooms", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_room_bans_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["banned_by_id"], ["users.id"], name="fk_room_bans_banned_by_id_users", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("room_id", "user_id", name="pk_room_bans"),
    )
    op.create_index("ix_room_bans_user_id", "room_bans", ["user_id"])

    op.create_table(
        "room_invitations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("room_id", sa.BigInteger(), nullable=False),
        sa.Column("invitee_id", sa.BigInteger(), nullable=False),
        sa.Column("inviter_id", sa.BigInteger(), nullable=True),
        sa.Column("status", invitation_status, server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.id"], name="fk_room_invitations_room_id_rooms", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["invitee_id"], ["users.id"], name="fk_room_invitations_invitee_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["inviter_id"], ["users.id"], name="fk_room_invitations_inviter_id_users", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_room_invitations"),
    )
    op.create_index(
        "ux_room_invitations_pending",
        "room_invitations",
        ["room_id", "invitee_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index("ix_room_invitations_invitee_status", "room_invitations", ["invitee_id", "status"])

    op.create_table(
        "chat_read_states",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("room_id", sa.BigInteger(), nullable=True),
        sa.Column("last_read_message_id", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_chat_read_states_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["room_id"], ["rooms.id"], name="fk_chat_read_states_room_id_rooms", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chat_read_states"),
        sa.CheckConstraint("num_nonnulls(room_id) = 1", name="ck_chat_read_states_one_chat"),
    )
    op.create_index(
        "ux_chat_read_states_user_room",
        "chat_read_states",
        ["user_id", "room_id"],
        unique=True,
        postgresql_where=sa.text("room_id IS NOT NULL"),
    )

    # Safety net for the owner invariant (services enforce it with 403 cannot_modify_owner): at commit,
    # every touched room that still exists must have its owner as an admin member.
    op.execute(
        """
        CREATE FUNCTION room_owner_is_admin_member() RETURNS trigger AS $$
        DECLARE
            rid BIGINT;
        BEGIN
            IF TG_TABLE_NAME = 'rooms' THEN
                rid := NEW.id;
            ELSIF TG_OP = 'DELETE' THEN
                rid := OLD.room_id;
            ELSE
                rid := NEW.room_id;
            END IF;
            IF EXISTS (
                SELECT 1 FROM rooms r
                WHERE r.id = rid
                  AND NOT EXISTS (
                      SELECT 1 FROM room_members m
                      WHERE m.room_id = r.id AND m.user_id = r.owner_id AND m.is_admin
                  )
            ) THEN
                RAISE EXCEPTION 'room % owner must be an admin member', rid
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER room_owner_is_admin_member
        AFTER INSERT OR UPDATE OF owner_id ON rooms
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION room_owner_is_admin_member();
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER room_owner_is_admin_member
        AFTER UPDATE OR DELETE ON room_members
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION room_owner_is_admin_member();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS room_owner_is_admin_member ON room_members")
    op.execute("DROP TRIGGER IF EXISTS room_owner_is_admin_member ON rooms")
    op.execute("DROP FUNCTION IF EXISTS room_owner_is_admin_member()")
    op.drop_table("chat_read_states")
    op.drop_table("room_invitations")
    op.drop_table("room_bans")
    op.drop_table("room_members")
    op.drop_table("rooms")
    invitation_status.drop(op.get_bind())
    room_visibility.drop(op.get_bind())
