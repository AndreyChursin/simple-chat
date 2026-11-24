import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import (
    attachments,
    auth,
    contacts,
    health,
    invitations,
    messages,
    moderation,
    rooms,
    sessions,
    unread,
    users,
)
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging, request_context_middleware
from app.core.security import origin_check_middleware
from app.db.session import engine
from app.services import maintenance
from app.ws import endpoint as ws_endpoint
from app.ws.manager import manager

UPLOAD_SUBDIRS = ("rooms", "dms", "tmp")


def _configure_logging() -> None:
    configure_logging(get_settings().log_level)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    for sub in UPLOAD_SUBDIRS:
        (settings.upload_dir / sub).mkdir(parents=True, exist_ok=True)
    housekeeping = asyncio.create_task(maintenance.run_forever(), name="housekeeping")
    presence = asyncio.create_task(manager.run_presence_ticker(), name="presence-ticker")
    metrics = asyncio.create_task(maintenance.log_metrics_forever(), name="metrics")
    yield
    await manager.close_all()
    for task in (metrics, presence, housekeeping):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await engine.dispose()


def create_app() -> FastAPI:
    _configure_logging()
    app = FastAPI(
        title="Online Chat Server",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    register_error_handlers(app)
    app.middleware("http")(origin_check_middleware)
    # added last = outermost: the request id and the access line cover everything, 403s included
    app.middleware("http")(request_context_middleware)
    for module in (
        health,
        auth,
        sessions,
        users,
        rooms,
        moderation,
        invitations,
        unread,
        messages,
        contacts,
        attachments,
    ):
        app.include_router(module.router, prefix="/api")
    app.include_router(ws_endpoint.router)
    return app


app = create_app()
