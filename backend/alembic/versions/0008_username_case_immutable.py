"""username immutability also covers case changes.

`users.username` is citext, so `NEW.username IS DISTINCT FROM OLD.username` was false for 'abc' → 'ABC'
and the trigger from 0002 let such updates through. Compare the text values instead.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTION = """
    CREATE OR REPLACE FUNCTION users_username_immutable() RETURNS trigger AS $$
    BEGIN
        IF NEW.username::text IS DISTINCT FROM OLD.username::text THEN
            RAISE EXCEPTION 'username is immutable' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(_FUNCTION)


def downgrade() -> None:
    op.execute(_FUNCTION.replace("::text", ""))
