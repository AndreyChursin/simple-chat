"""Presence lookup used by API serializers. Real aggregation over WebSocket connections arrives in
stage 5 (docs/ARCHITECTURE.md §4.3); until then nobody has a connection, so everyone is offline."""

from typing import Literal

Presence = Literal["online", "AFK", "offline"]


def get_presence(user_id: int) -> Presence:
    return "offline"
