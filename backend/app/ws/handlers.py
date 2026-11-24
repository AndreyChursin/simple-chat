"""Incoming WebSocket events. Thin adapters over app/services — no domain logic here."""

import logging
import time
from typing import Any

from pydantic import ValidationError

from app.core.errors import AppError
from app.db.models import User
from app.db.session import SessionLocal
from app.services import messages as message_service
from app.services import unread as unread_service
from app.services.access import ChatRef
from app.ws.manager import Connection, manager
from app.ws.protocol import Activity, Heartbeat, MarkRead, SendMessage, Typing, client_event

logger = logging.getLogger(__name__)

TYPING_THROTTLE_SECONDS = 3.0
_typing_sent: dict[tuple[int, str, int], float] = {}


def _error(conn: Connection, req_id: str | None, code: str, message: str) -> None:
    manager.send_to_connection(conn, {"type": "error", "req_id": req_id, "code": code, "message": message})


async def handle(conn: Connection, username: str, payload: Any) -> None:
    req_id = payload.get("req_id") if isinstance(payload, dict) else None
    req_id = req_id if isinstance(req_id, str) else None
    try:
        event = client_event.validate_python(payload)
    except ValidationError:
        _error(conn, req_id, "validation_error", "Invalid event")
        return
    try:
        if isinstance(event, Heartbeat):
            return
        if isinstance(event, Activity):
            await manager.activity(conn)
            return
        if isinstance(event, Typing):
            await _typing(conn, username, event)
            return
        if isinstance(event, SendMessage):
            await _send_message(conn, event)
            return
        if isinstance(event, MarkRead):
            async with SessionLocal() as db:
                await unread_service.mark_read(
                    db,
                    user_id=conn.user_id,
                    chat=ChatRef(event.chat.type, event.chat.id),
                    last_read_message_id=event.last_read_message_id,
                )
    except AppError as exc:
        _error(conn, req_id, exc.code, exc.message)
    except Exception:
        logger.exception("WS event %s failed", type(event).__name__)
        _error(conn, req_id, "internal_error", "Internal server error")


async def _typing(conn: Connection, username: str, event: Typing) -> None:
    chat = ChatRef(event.chat.type, event.chat.id)
    if not manager.is_subscribed(conn.user_id, chat.key):
        raise AppError(403, "forbidden", "You are not a member of this chat")
    key = (conn.user_id, chat.type, chat.id)
    now = time.monotonic()
    if now - _typing_sent.get(key, 0.0) < TYPING_THROTTLE_SECONDS:
        return
    _typing_sent[key] = now
    await manager.publish(
        chat.key,
        {
            "type": "typing",
            "chat": {"type": chat.type, "id": chat.id},
            "user": {"id": conn.user_id, "username": username},
        },
        exclude_user=conn.user_id,
    )


async def _send_message(conn: Connection, event: SendMessage) -> None:
    async with SessionLocal() as db:
        author = await db.get(User, conn.user_id)
        if author is None:  # account deleted meanwhile
            await manager.revoke_sessions([conn.session_id])
            return
        message, _ = await message_service.send_message(
            db,
            author=author,
            chat=ChatRef(event.chat.type, event.chat.id),
            content=event.content,
            reply_to_message_id=event.reply_to_message_id,
            client_msg_id=event.client_msg_id,
            attachment_ids=event.attachment_ids,
        )
        manager.send_to_connection(
            conn, {"type": "ack", "req_id": event.req_id, "message": message_service.serialize(message)}
        )
        # Clients hide the typist on their message; typing for the next one must not be throttled.
        _typing_sent.pop((conn.user_id, event.chat.type, event.chat.id), None)
