"""`GET /ws?tab_id=` — one connection per browser tab (docs/ARCHITECTURE.md §4.1)."""

import json
import logging
import time
import uuid
from collections import deque
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket
from sqlalchemy import and_, or_, select

from app.core.deps import SESSION_COOKIE
from app.core.security import is_origin_allowed
from app.db.models import DirectConversation, Friendship, FriendshipStatus, RoomMember, UserSession
from app.db.session import SessionLocal
from app.services import auth as auth_service
from app.ws import handlers
from app.ws.manager import (
    CLOSE_ORIGIN,
    CLOSE_UNAUTHENTICATED,
    ChatKey,
    Connection,
    manager,
)

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_FRAME_BYTES = 16 * 1024
CLOSE_TOO_BIG = 1009
CLOSE_UNSUPPORTED = 1003
EVENTS_PER_SECOND = 20
HEARTBEAT_INTERVAL_MS = 25_000


async def load_user_chats(user_id: int) -> set[ChatKey]:
    """Rooms the user is in and the DMs that are writable (the peer is a friend). Frozen DMs are only
    read through REST, so they carry no live events — and do not share presence with ex-friends."""
    async with SessionLocal() as db:
        room_ids = await db.scalars(select(RoomMember.room_id).where(RoomMember.user_id == user_id))
        chats: set[ChatKey] = {("room", rid) for rid in room_ids}
        dm = DirectConversation
        dm_ids = await db.scalars(
            select(dm.id)
            .join(
                Friendship,
                and_(
                    Friendship.status == FriendshipStatus.accepted,
                    or_(
                        and_(
                            Friendship.requester_id == dm.user_a_id, Friendship.addressee_id == dm.user_b_id
                        ),
                        and_(
                            Friendship.requester_id == dm.user_b_id, Friendship.addressee_id == dm.user_a_id
                        ),
                    ),
                ),
            )
            .where(or_(dm.user_a_id == user_id, dm.user_b_id == user_id))
        )
        chats |= {("dm", did) for did in dm_ids}
        return chats


async def _session_alive(session_id: int) -> bool:
    async with SessionLocal() as db:
        return await db.scalar(select(UserSession.id).where(UserSession.id == session_id)) is not None


async def _reject(ws: WebSocket, code: int) -> None:
    # Accept first so the browser sees the application close code instead of a failed handshake.
    await ws.accept()
    await ws.close(code)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    if not is_origin_allowed(ws.headers.get("origin")):
        await _reject(ws, CLOSE_ORIGIN)
        return
    token = ws.cookies.get(SESSION_COOKIE)
    session = None
    if token:
        async with SessionLocal() as db:
            session = await auth_service.resolve_session(
                db, token=token, ip=ws.client.host if ws.client else None
            )
    if session is None:
        await _reject(ws, CLOSE_UNAUTHENTICATED)
        return
    user_id, session_id, username = session.user_id, session.id, session.user.username
    tab_id = (ws.query_params.get("tab_id") or uuid.uuid4().hex)[:64]

    await ws.accept()
    hello = {
        "type": "hello",
        "user_id": user_id,
        "session_id": session_id,
        "server_time": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "heartbeat_interval_ms": HEARTBEAT_INTERVAL_MS,
        "afk_timeout_ms": int(manager.afk_timeout * 1000),
    }
    conn = await manager.connect(
        ws, user_id=user_id, session_id=session_id, tab_id=tab_id, load_chats=load_user_chats, hello=hello
    )
    try:
        # The session may have been revoked between the handshake check and registration.
        if not await _session_alive(session_id):
            await manager.revoke_sessions([session_id])
            return
        await manager.announce(conn)
        await _read_loop(ws, conn, username)
    finally:
        await manager.disconnect(conn)


async def _read_loop(ws: WebSocket, conn: Connection, username: str) -> None:
    recent: deque[float] = deque()
    while True:
        frame = await ws.receive()
        if frame["type"] == "websocket.disconnect":
            return
        text = frame.get("text")
        if text is None:
            await ws.close(CLOSE_UNSUPPORTED)
            return
        if len(text.encode("utf-8")) > MAX_FRAME_BYTES:
            await ws.close(CLOSE_TOO_BIG)
            return
        now = time.monotonic()
        while recent and now - recent[0] >= 1.0:
            recent.popleft()
        if len(recent) >= EVENTS_PER_SECOND:
            manager.send_to_connection(
                conn, {"type": "error", "req_id": None, "code": "rate_limited", "message": "Too many events"}
            )
            continue
        recent.append(now)
        try:
            payload = json.loads(text)
        except ValueError:
            manager.send_to_connection(
                conn, {"type": "error", "req_id": None, "code": "validation_error", "message": "Invalid JSON"}
            )
            continue
        await handlers.handle(conn, username, payload)
