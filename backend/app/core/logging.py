"""Structured JSON logs (docs/ARCHITECTURE.md §7): one JSON object per line, with the request id and the
user id of the request being served, and an access line per HTTP request with its latency."""

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, Response

# A mutable dict per request: downstream code runs in a copied context (BaseHTTPMiddleware), so it fills
# the dict in place (`set_user`) instead of setting a context variable the middleware would not see.
request_ctx: ContextVar[dict[str, Any] | None] = ContextVar("request_ctx", default=None)


def set_user(user_id: int) -> None:
    ctx = request_ctx.get()
    if ctx is not None:
        ctx["user_id"] = user_id


_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "color_message"}
QUIET_PATHS = frozenset({"/api/health"})  # docker healthcheck every few seconds


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update(request_ctx.get() or {})
        # fields passed with `extra=` (e.g. method, path, status, latency_ms)
        entry.update(
            {k: v for k, v in record.__dict__.items() if k not in _STANDARD and not k.startswith("_")}
        )
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):  # uvicorn installs its own text handlers
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True  # replaced by the access line below


access_logger = logging.getLogger("app.access")


async def request_context_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """A request id per HTTP request (echoed as `X-Request-ID`), then one access line."""
    rid = uuid.uuid4().hex[:16]
    token = request_ctx.set({"request_id": rid})  # user_id is added by the auth dependency
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        latency_ms = round((time.perf_counter() - start) * 1000, 1)
        level = logging.DEBUG if request.url.path in QUIET_PATHS else logging.INFO
        access_logger.log(
            level,
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "latency_ms": latency_ms,
            },
        )
        request_ctx.reset(token)
