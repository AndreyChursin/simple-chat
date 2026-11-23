"""Presence lookup used by API serializers: the state last broadcast by the WebSocket manager
(docs/ARCHITECTURE.md §4.3), so REST and WS never disagree."""

from app.ws.manager import Presence, manager

__all__ = ["Presence", "get_presence"]


def get_presence(user_id: int) -> Presence:
    return manager.get_presence(user_id)
