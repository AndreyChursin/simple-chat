"""WebSocket connection manager (docs/ARCHITECTURE.md §4).

Lives in the single uvicorn process (CLAUDE.md: `--workers 1`). Every connection has its own bounded
send queue drained by a writer task, so fan-out is a `put_nowait` per recipient connection and a slow
client never blocks the publisher.

Presence (§4.3) is aggregated here over all connections of a user (every tab of every session):
online — some connection had activity within `afk_timeout`; AFK — connections exist, none active;
offline — no connections, `grace` seconds after the last one closed (a page reload does not blink).
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from starlette.websockets import WebSocket, WebSocketState

logger = logging.getLogger(__name__)

ChatKey = tuple[Literal["room", "dm"], int]
Event = dict[str, Any]

SEND_QUEUE_SIZE = 1000
CLOSE_UNAUTHENTICATED = 4001
CLOSE_ORIGIN = 4003
CLOSE_SLOW_CONSUMER = 4008
CLOSE_GOING_AWAY = 1001

ChatLoader = Callable[[int], Awaitable[set[ChatKey]]]
Presence = Literal["online", "AFK", "offline"]

AFK_TIMEOUT_SECONDS = 60.0
DISCONNECT_GRACE_SECONDS = 1.0
PRESENCE_TICK_SECONDS = 1.0


@dataclass(eq=False)
class Connection:
    user_id: int
    session_id: int
    tab_id: str
    ws: WebSocket
    connected_at: float
    last_activity: float  # a new connection counts as active
    conn_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    send_queue: asyncio.Queue[str | None] = field(default_factory=lambda: asyncio.Queue(SEND_QUEUE_SIZE))
    writer: asyncio.Task[None] | None = None
    closing: bool = False


class ConnectionManager:
    def __init__(self) -> None:
        # Replaceable in tests ("подмена часов"); monotonic seconds.
        self.clock: Callable[[], float] = time.monotonic
        self.afk_timeout = AFK_TIMEOUT_SECONDS
        self.grace = DISCONNECT_GRACE_SECONDS
        self._grace: dict[int, asyncio.TimerHandle] = {}
        self.reset()

    def reset(self) -> None:
        for handle in getattr(self, "_grace", {}).values():
            handle.cancel()
        self._grace = {}
        # Last broadcast state; users absent from it are offline.
        self.presence: dict[int, Presence] = {}
        self.conns: dict[str, Connection] = {}
        self.by_user: dict[int, set[str]] = {}
        self.by_session: dict[int, set[str]] = {}
        self.chat_subscribers: dict[ChatKey, set[int]] = {}
        self.user_chats: dict[int, set[ChatKey]] = {}
        self._background: set[asyncio.Task[None]] = set()

    # --- lifecycle --------------------------------------------------------------------------------

    async def connect(
        self, ws: WebSocket, *, user_id: int, session_id: int, tab_id: str, load_chats: ChatLoader
    ) -> Connection:
        """Register an accepted socket and start its writer. The user's chats are loaded from the DB
        on their first connection and dropped once they are offline. Call `announce` after `hello`."""
        now = self.clock()
        conn = Connection(
            user_id=user_id, session_id=session_id, tab_id=tab_id, ws=ws, connected_at=now, last_activity=now
        )
        grace = self._grace.pop(user_id, None)
        if grace is not None:  # back within the grace period (page reload): nobody saw them leave
            grace.cancel()
        first = user_id not in self.user_chats
        self.conns[conn.conn_id] = conn
        self.by_user.setdefault(user_id, set()).add(conn.conn_id)
        self.by_session.setdefault(session_id, set()).add(conn.conn_id)
        conn.writer = asyncio.create_task(self._writer(conn), name=f"ws-writer-{conn.conn_id}")
        if first:
            self.user_chats[user_id] = set()
            chats = await load_chats(user_id)
            # Membership changes that raced with the load were applied to user_chats already.
            if user_id in self.user_chats:
                for chat in chats:
                    self._subscribe(user_id, chat)
        return conn

    async def disconnect(self, conn: Connection) -> None:
        if self.conns.pop(conn.conn_id, None) is None:
            return
        for index, key in ((self.by_user, conn.user_id), (self.by_session, conn.session_id)):
            ids = index.get(key)
            if ids is not None:
                ids.discard(conn.conn_id)
                if not ids:
                    del index[key]
        if conn.user_id not in self.by_user:
            self._start_grace(conn.user_id)
        else:
            await self._refresh(conn.user_id)  # the closed tab may have been the only active one
        # Stop the writer once it has flushed what is queued.
        if conn.writer is not None and not conn.writer.done():
            if conn.send_queue.full():
                conn.writer.cancel()
            else:
                conn.send_queue.put_nowait(None)

    async def close_all(self) -> None:
        """Server shutdown: close every socket; nobody is left to tell about presence."""
        for conn in list(self.conns.values()):
            await self._close(conn, CLOSE_GOING_AWAY)
        for handle in self._grace.values():
            handle.cancel()
        self._grace.clear()

    async def _writer(self, conn: Connection) -> None:
        try:
            while True:
                data = await conn.send_queue.get()
                if data is None:
                    return
                await conn.ws.send_text(data)
        except asyncio.CancelledError:
            raise
        except Exception:  # socket already gone: the reader loop will notice and disconnect
            logger.debug("ws writer stopped for %s", conn.conn_id, exc_info=True)

    async def _close(self, conn: Connection, code: int) -> None:
        if conn.closing:
            return
        conn.closing = True
        await self.disconnect(conn)
        if conn.writer is not None and conn.writer is not asyncio.current_task():
            # Let already queued events (e.g. session_revoked) reach the client before closing.
            await asyncio.wait({conn.writer}, timeout=1.0)
        if conn.ws.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await conn.ws.close(code)

    # --- sending ----------------------------------------------------------------------------------

    def _enqueue(self, conn: Connection, data: str) -> None:
        if conn.closing:
            return
        try:
            conn.send_queue.put_nowait(data)
        except asyncio.QueueFull:
            logger.warning("ws slow consumer %s (user %s), closing", conn.conn_id, conn.user_id)
            if conn.writer is not None:
                conn.writer.cancel()
            task = asyncio.get_running_loop().create_task(self._close(conn, CLOSE_SLOW_CONSUMER))
            self._background.add(task)
            task.add_done_callback(self._background.discard)

    def send_to_connection(self, conn: Connection, event: Event) -> None:
        self._enqueue(conn, json.dumps(event, separators=(",", ":")))

    async def send_to_users(self, user_ids: Iterable[int], event: Event) -> None:
        """Queue `event` to every open connection (all tabs, all sessions) of the given users."""
        data: str | None = None
        for user_id in user_ids:
            for conn_id in tuple(self.by_user.get(user_id, ())):
                data = data or json.dumps(event, separators=(",", ":"))
                self._enqueue(self.conns[conn_id], data)

    async def publish(self, chat: ChatKey, event: Event, *, exclude_user: int | None = None) -> None:
        """Queue `event` to every connection of every online member of `chat`."""
        subscribers = self.chat_subscribers.get(chat, ())
        await self.send_to_users((u for u in tuple(subscribers) if u != exclude_user), event)

    # --- membership / sessions --------------------------------------------------------------------

    def _subscribe(self, user_id: int, chat: ChatKey) -> None:
        self.user_chats.setdefault(user_id, set()).add(chat)
        self.chat_subscribers.setdefault(chat, set()).add(user_id)

    async def on_membership_changed(self, user_id: int, chat: ChatKey, *, joined: bool) -> None:
        """Update `user_chats` / `chat_subscribers` after a join, leave, ban or room deletion.
        Only users with open connections are tracked."""
        if user_id not in self.user_chats:
            return
        if joined:
            self._subscribe(user_id, chat)
            return
        self.user_chats[user_id].discard(chat)
        subs = self.chat_subscribers.get(chat)
        if subs is not None:
            subs.discard(user_id)
            if not subs:
                del self.chat_subscribers[chat]

    def is_subscribed(self, user_id: int, chat: ChatKey) -> bool:
        return chat in self.user_chats.get(user_id, ())

    async def revoke_sessions(self, session_ids: Iterable[int]) -> None:
        """Send `session_revoked` and close (4001) every connection of the given sessions."""
        targets = [self.conns[c] for s in session_ids for c in tuple(self.by_session.get(s, ()))]
        for conn in targets:
            self.send_to_connection(conn, {"type": "session_revoked"})
        await asyncio.gather(*(self._close(conn, CLOSE_UNAUTHENTICATED) for conn in targets))

    # --- presence ---------------------------------------------------------------------------------

    def aggregate(self, user_id: int) -> Presence:
        conn_ids = self.by_user.get(user_id)
        if not conn_ids:
            return "offline"
        now = self.clock()
        if any(now - self.conns[c].last_activity < self.afk_timeout for c in conn_ids):
            return "online"
        return "AFK"

    def get_presence(self, user_id: int) -> Presence:
        return self.presence.get(user_id, "offline")

    def audience(self, user_id: int) -> set[int]:
        """Users who see `user_id`'s presence: everyone sharing a chat with them — room co-members and
        friends (their writable DM) — the user included. Only users with connections are subscribers."""
        result = {user_id}
        for chat in self.user_chats.get(user_id, ()):
            result |= self.chat_subscribers.get(chat, set())
        return result

    async def announce(self, conn: Connection) -> None:
        """After `hello`: the presence of everyone this user can see, then their own (offline→online)."""
        visible = [
            {"user_id": uid, "presence": self.presence[uid]}
            for uid in sorted(self.audience(conn.user_id))
            if uid in self.presence
        ]
        self.send_to_connection(conn, {"type": "presence_snapshot", "users": visible})
        await self._refresh(conn.user_id)

    async def activity(self, conn: Connection) -> None:
        conn.last_activity = self.clock()
        if self.presence.get(conn.user_id) == "AFK":  # AFK → online at once, not on the next tick
            await self._refresh(conn.user_id)

    async def tick(self) -> None:
        """online → AFK transitions (everything else is event driven)."""
        for user_id in tuple(self.by_user):
            await self._refresh(user_id)

    async def run_presence_ticker(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("presence tick failed")
            await asyncio.sleep(PRESENCE_TICK_SECONDS)

    async def _refresh(self, user_id: int) -> None:
        await self._set_presence(user_id, self.aggregate(user_id))

    async def _set_presence(self, user_id: int, value: Presence) -> None:
        if self.presence.get(user_id, "offline") == value:
            return
        if value == "offline":
            self.presence.pop(user_id, None)
        else:
            self.presence[user_id] = value
        await self.send_to_users(
            self.audience(user_id), {"type": "presence_update", "user_id": user_id, "presence": value}
        )

    def _start_grace(self, user_id: int) -> None:
        def expire() -> None:
            self._grace.pop(user_id, None)
            task = asyncio.get_running_loop().create_task(self._went_offline(user_id))
            self._background.add(task)
            task.add_done_callback(self._background.discard)

        old = self._grace.pop(user_id, None)
        if old is not None:
            old.cancel()
        self._grace[user_id] = asyncio.get_running_loop().call_later(self.grace, expire)

    async def _went_offline(self, user_id: int) -> None:
        if user_id in self.by_user:
            return
        # Audience is computed from the user's chats, so broadcast before dropping them.
        await self._set_presence(user_id, "offline")
        for chat in self.user_chats.pop(user_id, set()):
            subs = self.chat_subscribers.get(chat)
            if subs is not None:
                subs.discard(user_id)
                if not subs:
                    del self.chat_subscribers[chat]

    def connection_count(self, user_id: int | None = None) -> int:
        return len(self.conns) if user_id is None else len(self.by_user.get(user_id, ()))


manager = ConnectionManager()
