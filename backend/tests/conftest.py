"""Test setup: runs against the separate `chat_test` database (created by db/init/01-create-test-db.sql).

Environment must be configured before `app` is imported, because settings and the engine are
module-level singletons.
"""

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
if not _TEST_DB_URL:
    raise RuntimeError("TEST_DATABASE_URL is not set; run tests via `docker compose exec backend pytest`")
if os.environ.get("DATABASE_URL") == _TEST_DB_URL:
    raise RuntimeError("TEST_DATABASE_URL must differ from DATABASE_URL (tests truncate every table)")

os.environ["DATABASE_URL"] = _TEST_DB_URL
# A real connection pool like production; it is disposed after every test because each test runs in
# its own event loop and asyncpg connections are bound to a loop.
os.environ["DB_DISABLE_POOL"] = "false"
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="chat-test-uploads-")
os.environ["APP_ORIGIN"] = "http://testserver"
os.environ["MAIL_BACKEND"] = "memory"

import httpx  # noqa: E402
import pytest  # noqa: E402
import uvicorn  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core import mail  # noqa: E402
from app.core.ratelimit import rate_limiter  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.ws import handlers as ws_handlers  # noqa: E402
from app.ws.manager import manager  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.attributes["database_url"] = _TEST_DB_URL
    cfg.attributes["configure_logger"] = False
    return cfg


@pytest.fixture(scope="session", autouse=True)
def _migrated_database() -> Iterator[None]:
    """Recreate the schema from migrations once per test session (also exercises downgrade)."""
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
async def _clean_state() -> AsyncIterator[None]:
    rate_limiter.reset()
    mail.outbox.clear()
    manager.reset()
    ws_handlers._typing_sent.clear()
    yield
    async with engine.begin() as conn:
        tables = (
            (
                await conn.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
                    )
                )
            )
            .scalars()
            .all()
        )
        if tables:
            quoted = ", ".join(f'"{t}"' for t in tables)
            await conn.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest.fixture
def app():  # type: ignore[no-untyped-def]
    return create_app()


ClientFactory = Callable[..., Awaitable[httpx.AsyncClient]]


@pytest.fixture
async def make_client(app) -> AsyncIterator[ClientFactory]:  # type: ignore[no-untyped-def]
    """Each client is an independent "browser" with its own cookie jar."""
    clients: list[httpx.AsyncClient] = []

    async def factory(user_agent: str = "pytest-browser/1.0") -> httpx.AsyncClient:
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"User-Agent": user_agent},
        )
        clients.append(c)
        return c

    yield factory
    for c in clients:
        await c.aclose()


@pytest.fixture
async def client(make_client: ClientFactory) -> httpx.AsyncClient:
    return await make_client()


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[int], dict[str, Any]]]:
    """Captures (recipient user ids, event) that services hand to the WebSocket manager after commit."""
    captured: list[tuple[list[int], dict[str, Any]]] = []

    async def send_to_users(user_ids: Any, event: dict[str, Any]) -> None:
        captured.append((sorted(user_ids), event))

    monkeypatch.setattr(manager, "send_to_users", send_to_users)
    return captured


@pytest.fixture
async def live_server(app) -> AsyncIterator[str]:  # type: ignore[no-untyped-def]
    """The app served by a real uvicorn on a random port, inside the test's event loop, so REST calls
    through `make_client` (ASGI transport) and WebSocket clients share one ConnectionManager."""
    config = uvicorn.Config(
        app, host="127.0.0.1", port=0, lifespan="off", log_level="warning", ws_max_size=16384
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    ticker = asyncio.create_task(manager.run_presence_ticker())  # lifespan is off: start it here
    yield f"127.0.0.1:{port}"
    ticker.cancel()
    await manager.close_all()
    server.should_exit = True
    await task
