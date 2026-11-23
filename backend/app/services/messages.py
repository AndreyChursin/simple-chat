"""Messages: send, history (cursor pagination only), search, edit, delete (docs/API_AND_WS_SPEC.md §9).

All message domain logic lives here — the WebSocket handler and the REST router are thin adapters —
so another transport can reuse it later (CLAUDE.md §6).
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.api.schemas import MessageOut
from app.core.errors import AppError
from app.core.ratelimit import rate_limiter
from app.db.models import MESSAGE_MAX_BYTES, Attachment, Message, User
from app.services import access, files
from app.services.access import ChatAccess, ChatRef
from app.ws.manager import manager

SEND_RATE_LIMIT = (5, 1.0)  # per user, shared by WS and REST
SEARCH_MIN_CHARS = 2

# One SELECT per page: author + quoted message + its author.
MESSAGE_LOAD = (
    joinedload(Message.author),
    joinedload(Message.reply_to).joinedload(Message.author),
    selectinload(Message.attachments),  # one extra SELECT per page, not per message
)
MAX_ATTACHMENTS = 10


def utcnow() -> datetime:
    return datetime.now(UTC)


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def message_not_found() -> AppError:
    return AppError(404, "message_not_found", "Message not found")


def normalize_content(content: str) -> str:
    """CRLF → LF; leading/trailing whitespace is kept. Raises 413 above 3072 UTF-8 bytes."""
    content = content.replace("\r\n", "\n")
    if len(content.encode("utf-8")) > MESSAGE_MAX_BYTES:
        raise AppError(
            413,
            "message_too_large",
            f"Message exceeds {MESSAGE_MAX_BYTES} bytes",
            {"max_bytes": MESSAGE_MAX_BYTES},
        )
    return content


def _require_content(content: str, has_attachments: bool) -> None:
    if not content.strip() and not has_attachments:
        raise AppError(400, "empty_message", "Message is empty")


def _in_chat(chat: ChatRef) -> Any:
    return Message.room_id == chat.id if chat.type == "room" else Message.dm_id == chat.id


def chat_of(message: Message) -> ChatRef:
    if message.room_id is not None:
        return ChatRef("room", message.room_id)
    assert message.dm_id is not None
    return ChatRef("dm", message.dm_id)


def serialize(message: Message) -> dict[str, Any]:
    return MessageOut.of(message).model_dump(mode="json")


async def _load(db: AsyncSession, message_id: int) -> Message | None:
    message: Message | None = await db.scalar(
        select(Message).options(*MESSAGE_LOAD).where(Message.id == message_id)
    )
    return message


async def _reload(db: AsyncSession, message_id: int) -> Message:
    message = await db.scalar(
        select(Message)
        .options(*MESSAGE_LOAD)
        .where(Message.id == message_id)
        .execution_options(populate_existing=True)
    )
    assert message is not None
    return message


# --- send -------------------------------------------------------------------------------------------


async def send_message(
    db: AsyncSession,
    *,
    author: User,
    chat: ChatRef,
    content: str,
    reply_to_message_id: int | None = None,
    client_msg_id: uuid.UUID | None = None,
    attachment_ids: list[int] | None = None,
) -> tuple[Message, bool]:
    """Returns (message, created). A repeated `client_msg_id` of the same author returns the existing
    message without publishing it again (idempotent resend after a reconnect)."""
    await access.require_chat_writable(db, chat, author.id)
    if client_msg_id is not None:
        existing = await _existing_by_client_id(db, author.id, client_msg_id)
        if existing is not None:
            return existing, False
    rate_limiter.hit(f"send:{author.id}", *SEND_RATE_LIMIT)
    content = normalize_content(content)
    ids = list(dict.fromkeys(attachment_ids or []))
    if len(ids) > MAX_ATTACHMENTS:
        raise AppError(
            413,
            "too_many_attachments",
            f"At most {MAX_ATTACHMENTS} attachments per message",
            {"max": MAX_ATTACHMENTS},
        )
    _require_content(content, bool(ids))
    attachments = await _pending_attachments(db, author.id, chat, ids)
    if reply_to_message_id is not None:
        target = await db.scalar(select(Message.id).where(Message.id == reply_to_message_id, _in_chat(chat)))
        if target is None:
            raise AppError(400, "invalid_reply_target", "You can only reply to a message of the same chat")

    message = Message(
        room_id=chat.id if chat.type == "room" else None,
        dm_id=chat.id if chat.type == "dm" else None,
        author_id=author.id,
        client_msg_id=client_msg_id,
        content=content,
        reply_to_message_id=reply_to_message_id,
        created_at=utcnow(),
    )
    db.add(message)
    try:
        await db.flush()
        for attachment in attachments:
            attachment.message_id = message.id
        await db.commit()
    except IntegrityError as exc:  # concurrent resend with the same client_msg_id
        await db.rollback()
        if client_msg_id is not None and "ux_messages_client" in str(exc.orig):
            existing = await _existing_by_client_id(db, author.id, client_msg_id)
            if existing is not None:
                return existing, False
        raise
    message = await _reload(db, message.id)
    await manager.publish(chat.key, {"type": "new_message", "message": serialize(message)})
    return message, True


async def _pending_attachments(
    db: AsyncSession, author_id: int, chat: ChatRef, ids: list[int]
) -> list[Attachment]:
    """The author's own pending uploads to this very chat; locked so two sends cannot both take one."""
    if not ids:
        return []
    rows = list(
        await db.scalars(
            select(Attachment).where(Attachment.id.in_(ids)).with_for_update().order_by(Attachment.id)
        )
    )

    def in_chat(a: Attachment) -> bool:
        return (a.room_id if chat.type == "room" else a.dm_id) == chat.id

    if len(rows) != len(ids) or any(
        a.uploader_id != author_id or a.message_id is not None or not in_chat(a) for a in rows
    ):
        raise AppError(400, "invalid_attachment", "Attachments must be your own unsent uploads to this chat")
    return rows


async def _existing_by_client_id(
    db: AsyncSession, author_id: int, client_msg_id: uuid.UUID
) -> Message | None:
    message: Message | None = await db.scalar(
        select(Message)
        .options(*MESSAGE_LOAD)
        .where(Message.author_id == author_id, Message.client_msg_id == client_msg_id)
    )
    return message


# --- history ----------------------------------------------------------------------------------------


def _history(chat: ChatRef) -> Select[tuple[Message]]:
    return select(Message).options(*MESSAGE_LOAD).where(_in_chat(chat))


async def _page(
    db: AsyncSession, chat: ChatRef, *conds: Any, newest_first: bool, limit: int
) -> list[Message]:
    """Two steps: the page's ids, then the rows. The id-only query is an Index Only Scan on
    ix_messages_room_id (room_id, id DESC) whatever the room's share of traffic or where its history
    lies; asked for whole rows at once, the planner may instead walk the primary key backwards and
    filter by room, which degrades for rooms whose messages lie far back."""
    return list(await db.scalars(page_query(chat, *conds, newest_first=newest_first, limit=limit)))


def page_query(chat: ChatRef, *conds: Any, newest_first: bool, limit: int) -> Select[tuple[Message]]:
    order = Message.id.desc() if newest_first else Message.id.asc()
    ids = select(Message.id).where(_in_chat(chat), *conds).order_by(order).limit(limit).subquery()
    return select(Message).options(*MESSAGE_LOAD).where(Message.id.in_(select(ids.c.id))).order_by(order)


async def list_messages(
    db: AsyncSession,
    *,
    user_id: int,
    chat: ChatRef,
    before_id: int | None,
    after_id: int | None,
    limit: int,
) -> tuple[list[Message], bool, int | None]:
    """Items always ascending by id. Without a cursor / with `before_id`: the newest `limit` messages
    before it (`next_cursor` = smallest id, for scrolling up). With `after_id`: the next `limit` after
    it (`next_cursor` = largest id, for catching up after a reconnect)."""
    if before_id is not None and after_id is not None:
        raise AppError(400, "validation_error", "Use either before_id or after_id, not both")
    await access.require_chat_member(db, chat, user_id)
    if after_id is not None:
        rows = await _page(db, chat, Message.id > after_id, newest_first=False, limit=limit + 1)
        has_more = len(rows) > limit
        items = rows[:limit]
        return items, has_more, items[-1].id if has_more else None
    conds = [Message.id < before_id] if before_id is not None else []
    rows = await _page(db, chat, *conds, newest_first=True, limit=limit + 1)
    has_more = len(rows) > limit
    items = rows[:limit][::-1]
    return items, has_more, items[0].id if has_more else None


async def messages_around(
    db: AsyncSession, *, user_id: int, chat: ChatRef, message_id: int, limit: int
) -> tuple[list[Message], bool, bool]:
    """A window centred on `message_id` (jump to a quoted message / search hit)."""
    await access.require_chat_member(db, chat, user_id)
    if await db.scalar(select(Message.id).where(Message.id == message_id, _in_chat(chat))) is None:
        raise message_not_found()
    before_n = limit // 2
    after_n = limit - before_n
    before = await _page(db, chat, Message.id < message_id, newest_first=True, limit=before_n + 1)
    after = await _page(db, chat, Message.id >= message_id, newest_first=False, limit=after_n + 1)
    items = before[:before_n][::-1] + after[:after_n]
    return items, len(before) > before_n, len(after) > after_n


async def search_messages(
    db: AsyncSession, *, user_id: int, chat: ChatRef, q: str, before_id: int | None, limit: int
) -> tuple[list[Message], bool, int | None]:
    """Substring search (trgm index), newest first; `next_cursor` = smallest id of the page."""
    q = q.strip()
    if len(q) < SEARCH_MIN_CHARS:
        raise AppError(400, "validation_error", f"Search needs at least {SEARCH_MIN_CHARS} characters")
    await access.require_chat_member(db, chat, user_id)
    query = _history(chat).where(
        Message.deleted_at.is_(None), Message.content.ilike(f"%{escape_like(q)}%", escape="\\")
    )
    if before_id is not None:
        query = query.where(Message.id < before_id)
    rows = list(await db.scalars(query.order_by(Message.id.desc()).limit(limit + 1)))
    has_more = len(rows) > limit
    items = rows[:limit]
    return items, has_more, items[-1].id if has_more else None


# --- edit / delete ----------------------------------------------------------------------------------


async def _message_with_access(db: AsyncSession, message_id: int, user_id: int) -> tuple[Message, ChatAccess]:
    """Editing and deleting need write access now: a frozen DM is read-only for both sides."""
    message = await _load(db, message_id)
    if message is None:
        raise message_not_found()
    acc = await access.require_chat_writable(db, chat_of(message), user_id)
    return message, acc


async def edit_message(db: AsyncSession, *, user: User, message_id: int, content: str) -> Message:
    message, acc = await _message_with_access(db, message_id, user.id)
    if message.deleted_at is not None:
        raise AppError(400, "message_deleted", "A deleted message cannot be edited")
    if message.author_id != user.id:
        raise AppError(403, "not_message_author", "Only the author can edit a message")
    content = normalize_content(content)
    _require_content(content, bool(message.attachments))
    if content == message.content:
        return message
    message.content = content
    message.edited_at = utcnow()
    await db.commit()
    message = await _reload(db, message.id)
    await manager.publish(acc.chat.key, {"type": "message_edited", "message": serialize(message)})
    return message


async def delete_message(db: AsyncSession, *, user: User, message_id: int) -> None:
    """Soft delete: content is wiped, the row stays as a "Message deleted" placeholder. Author, or a
    room admin/owner. Deleting an already deleted message is a no-op."""
    message, acc = await _message_with_access(db, message_id, user.id)
    if message.author_id != user.id and not acc.can_moderate:
        raise AppError(403, "forbidden", "You can only delete your own messages")
    if message.deleted_at is not None:
        return
    message.content = ""
    message.deleted_at = utcnow()
    message.deleted_by_id = user.id
    # Its files go too: rows now, the files on disk after the commit.
    stored = list(
        await db.scalars(
            delete(Attachment).where(Attachment.message_id == message.id).returning(Attachment.stored_path)
        )
    )
    await db.commit()
    await files.remove_files(stored)
    await manager.publish(
        acc.chat.key,
        {
            "type": "message_deleted",
            "chat": {"type": acc.chat.type, "id": acc.chat.id},
            "message_id": message.id,
            "deleted_by": {"id": user.id, "username": user.username},
        },
    )
