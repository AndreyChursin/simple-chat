"""Fix doubled CHECK constraint names created by 0002/0003.

op.create_table applies the metadata naming convention "ck_%(table_name)s_%(constraint_name)s" on top
of names that were already complete, producing e.g. ck_users_ck_users_email_format. Rename them to the
names the models declare. New migrations wrap full names in op.f().

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RENAMES = [
    ("users", "email_format"),
    ("users", "username_format"),
    ("rooms", "name_length"),
    ("rooms", "member_count_non_negative"),
    ("chat_read_states", "one_chat"),
]


def upgrade() -> None:
    for table, name in RENAMES:
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT ck_{table}_ck_{table}_{name} TO ck_{table}_{name}")


def downgrade() -> None:
    for table, name in RENAMES:
        op.execute(f"ALTER TABLE {table} RENAME CONSTRAINT ck_{table}_{name} TO ck_{table}_ck_{table}_{name}")
