"""Unread counters (docs/DATA_MODEL.md §2.13, docs/API_AND_WS_SPEC.md §9, §12).

A chat's unread count = messages after the user's read cursor that are not deleted and not the user's own,
capped at 100 (the UI shows "99+"). Room cursors exist for every member (created on join with the room's
latest id); a DM cursor is created on the first `mark_read`, until then the whole DM history counts.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ChatReadState, Message
from app.services import access
from app.services.access import ChatRef
from app.ws.manager import manager

UNREAD_CAP = 100


@dataclass
class UnreadItem:
    chat: ChatRef
    unread: int
    last_read_message_id: int
    last_message_id: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "chat": {"type": self.chat.type, "id": self.chat.id},
            "unread": self.unread,
            "last_read_message_id": self.last_read_message_id,
            "last_message_id": self.last_message_id,
        }


# `cursor` is the read cursor expression, `chat_col` the messages column of the chat.
_COUNT = """
    (SELECT count(*) FROM (
        SELECT 1 FROM messages m
        WHERE m.{chat_col} = {chat_id} AND m.id > {cursor}
          AND m.deleted_at IS NULL AND m.author_id IS DISTINCT FROM :uid
        LIMIT {cap}) t)
"""
_LAST = "(SELECT m.id FROM messages m WHERE m.{chat_col} = {chat_id} ORDER BY m.id DESC LIMIT 1)"

_ROOMS_SQL = f"""
    SELECT crs.room_id, crs.last_read_message_id,
        {_COUNT.format(
            chat_col="room_id", chat_id="crs.room_id", cursor="crs.last_read_message_id", cap=UNREAD_CAP
        )},
        {_LAST.format(chat_col="room_id", chat_id="crs.room_id")}
    FROM chat_read_states crs
    WHERE crs.user_id = :uid AND crs.room_id IS NOT NULL
"""

_DM_CURSOR = "coalesce(crs.last_read_message_id, 0)"
_DMS_SQL = f"""
    SELECT d.id, {_DM_CURSOR},
        {_COUNT.format(chat_col="dm_id", chat_id="d.id", cursor=_DM_CURSOR, cap=UNREAD_CAP)},
        {_LAST.format(chat_col="dm_id", chat_id="d.id")}
    FROM direct_conversations d
    LEFT JOIN chat_read_states crs ON crs.dm_id = d.id AND crs.user_id = :uid
    WHERE d.user_a_id = :uid OR d.user_b_id = :uid
"""


async def list_unread(db: AsyncSession, *, user_id: int, chat: ChatRef | None = None) -> list[UnreadItem]:
    """Every chat of the user (rooms they are in + their DMs, frozen ones included), or just `chat`."""
    items: list[UnreadItem] = []
    for chat_type, sql, column in (("room", _ROOMS_SQL, "crs.room_id"), ("dm", _DMS_SQL, "d.id")):
        if chat is not None:
            if chat.type != chat_type:
                continue
            sql += f" AND {column} = :chat_id"
        params = {"uid": user_id} | ({"chat_id": chat.id} if chat is not None else {})
        for chat_id, last_read, unread, last_id in (await db.execute(text(sql), params)).all():
            items.append(UnreadItem(ChatRef(chat_type, chat_id), unread, last_read, last_id))  # type: ignore[arg-type]
    return items


async def unread_map(db: AsyncSession, *, user_id: int) -> dict[tuple[str, int], int]:
    return {item.chat.key: item.unread for item in await list_unread(db, user_id=user_id)}


async def mark_read(
    db: AsyncSession, *, user_id: int, chat: ChatRef, last_read_message_id: int
) -> UnreadItem:
    """Move the cursor forward (never back), then tell every tab of the user (`unread_update`)."""
    await access.require_chat_member(db, chat, user_id)
    chat_col = Message.room_id if chat.type == "room" else Message.dm_id
    latest = await db.scalar(select(func.max(Message.id)).where(chat_col == chat.id)) or 0
    target = min(last_read_message_id, latest)
    key_col = "room_id" if chat.type == "room" else "dm_id"
    stmt = pg_insert(ChatReadState).values(
        user_id=user_id, last_read_message_id=target, updated_at=datetime.now(UTC), **{key_col: chat.id}
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["user_id", key_col],
            index_where=text(f"{key_col} IS NOT NULL"),
            set_={
                "last_read_message_id": func.greatest(
                    ChatReadState.last_read_message_id, stmt.excluded.last_read_message_id
                ),
                "updated_at": stmt.excluded.updated_at,
            },
        )
    )
    await db.commit()
    (item,) = await list_unread(db, user_id=user_id, chat=chat)
    await manager.send_to_users(
        [user_id],
        {
            "type": "unread_update",
            "chat": {"type": chat.type, "id": chat.id},
            "unread": item.unread,
            "last_read_message_id": item.last_read_message_id,
        },
    )
    return item
