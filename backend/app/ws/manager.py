"""WebSocket connection manager (docs/ARCHITECTURE.md §4).

Stage 2 only defines the hooks services call after commit; connection tracking, fan-out and presence
are implemented in stages 4–5. Keeping the call sites now means session revocation will close sockets
as soon as sockets exist.
"""

from collections.abc import Iterable


class ConnectionManager:
    async def revoke_sessions(self, session_ids: Iterable[int]) -> None:
        """Send `session_revoked` and close (4001) every connection of the given sessions."""
        return None


manager = ConnectionManager()
