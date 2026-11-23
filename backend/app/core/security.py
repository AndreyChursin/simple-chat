"""Request-level security: Origin check for state-changing requests (CSRF defence together with
SameSite=Lax cookies, see docs/ARCHITECTURE.md §6)."""

from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from app.core.config import get_settings
from app.core.errors import error_response

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def is_origin_allowed(origin: str | None) -> bool:
    """A missing Origin is allowed (non-browser clients, tests); browsers always send it on
    cross-site unsafe requests and on WebSocket handshakes, so a mismatch is what we reject."""
    if origin is None:
        return True
    return origin.rstrip("/") in get_settings().allowed_origins


async def origin_check_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    if request.method in UNSAFE_METHODS and not is_origin_allowed(request.headers.get("origin")):
        return error_response(403, "origin_mismatch", "Request origin is not allowed")
    return await call_next(request)
