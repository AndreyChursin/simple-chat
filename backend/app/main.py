import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.security import origin_check_middleware
from app.db.session import engine

UPLOAD_SUBDIRS = ("rooms", "dms", "tmp")


def _configure_logging() -> None:
    logging.basicConfig(
        level=get_settings().log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    for sub in UPLOAD_SUBDIRS:
        (settings.upload_dir / sub).mkdir(parents=True, exist_ok=True)
    yield
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
    app.include_router(health.router, prefix="/api")
    return app


app = create_app()
