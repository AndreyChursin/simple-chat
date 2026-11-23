"""Test setup: runs against the separate `chat_test` database (created by db/init/01-create-test-db.sql).

Environment must be configured before `app` is imported, because settings and the engine are
module-level singletons.
"""

import os
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path

_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
if not _TEST_DB_URL:
    raise RuntimeError("TEST_DATABASE_URL is not set; run tests via `docker compose exec backend pytest`")
if os.environ.get("DATABASE_URL") == _TEST_DB_URL:
    raise RuntimeError("TEST_DATABASE_URL must differ from DATABASE_URL (tests truncate every table)")

os.environ["DATABASE_URL"] = _TEST_DB_URL
os.environ["DB_DISABLE_POOL"] = "true"  # each test gets its own event loop
os.environ["UPLOAD_DIR"] = tempfile.mkdtemp(prefix="chat-test-uploads-")
os.environ["APP_ORIGIN"] = "http://testserver"
os.environ["MAIL_BACKEND"] = "memory"

import httpx  # noqa: E402
import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core import mail  # noqa: E402
from app.core.ratelimit import rate_limiter  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import create_app  # noqa: E402

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
