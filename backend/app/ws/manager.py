"""WebSocket connection manager (docs/ARCHITECTURE.md §4).

Stages 2–3 only define the hooks services call after commit; connection tracking, fan-out and presence
are implemented in stages 4–5. Keeping the call sites now means sessions, membership changes and room
events reach sockets as soon as sockets exist.
"""

from collections.abc import Iterable
from typing import Any, Literal

ChatKey = tuple[Literal["room", "dm"], int]
Event = dict[str, Any]


class ConnectionManager:
    async def revoke_sessions(self, session_ids: Iterable[int]) -> None:
        """Send `session_revoked` and close (4001) every connection of the given sessions."""
        return None

    async def send_to_users(self, user_ids: Iterable[int], event: Event) -> None:
        """Queue `event` to every open connection (all tabs, all sessions) of the given users."""
        return None

    async def on_membership_changed(self, user_id: int, chat: ChatKey, *, joined: bool) -> None:
        """Update `user_chats` / `chat_subscribers` indexes after a join, leave, ban or room deletion."""
        return None


manager = ConnectionManager()
