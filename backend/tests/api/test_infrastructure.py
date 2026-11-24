import httpx
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from tests.conftest import ClientFactory


async def test_health_ok(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok"}


async def test_migrations_applied_and_extensions_present(db: AsyncSession) -> None:
    version = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    assert version
    exts = set((await db.execute(text("SELECT extname FROM pg_extension"))).scalars().all())
    assert {"citext", "pg_trgm"} <= exts


async def test_no_seed_data(db: AsyncSession) -> None:
    query = text(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
    )
    tables = (await db.execute(query)).scalars().all()
    for table in tables:
        count = (await db.execute(text(f'SELECT count(*) FROM "{table}"'))).scalar_one()
        assert count == 0, f"table {table} must be empty after migrations"


async def test_unknown_route_uses_error_format(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "Not Found", "details": {}}}


class _Body(BaseModel):
    name: str


def _add_probe_routes(app: FastAPI) -> None:
    @app.post("/api/_probe/validate")
    async def validate(body: _Body) -> dict[str, str]:
        return {"name": body.name}

    @app.get("/api/_probe/app-error")
    async def app_error() -> None:
        raise AppError(409, "room_name_taken", "Room name is already taken", {"name": "x"})


async def test_validation_error_is_400(app: FastAPI, client: httpx.AsyncClient) -> None:
    _add_probe_routes(app)
    resp = await client.post("/api/_probe/validate", json={"wrong": 1})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "validation_error"
    assert err["details"]["errors"][0]["loc"] == ["body", "name"]


async def test_app_error_format(app: FastAPI, client: httpx.AsyncClient) -> None:
    _add_probe_routes(app)
    resp = await client.get("/api/_probe/app-error")
    assert resp.status_code == 409
    assert resp.json()["error"] == {
        "code": "room_name_taken",
        "message": "Room name is already taken",
        "details": {"name": "x"},
    }


async def test_foreign_origin_rejected_for_unsafe_methods(app: FastAPI, client: httpx.AsyncClient) -> None:
    _add_probe_routes(app)
    resp = await client.post(
        "/api/_probe/validate", json={"name": "a"}, headers={"Origin": "http://evil.example"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "origin_mismatch"


async def test_missing_origin_rejected_for_unsafe_methods(app: FastAPI, make_client: ClientFactory) -> None:
    _add_probe_routes(app)
    c = await make_client()
    del c.headers["Origin"]
    resp = await c.post("/api/_probe/validate", json={"name": "a"})
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "origin_mismatch"
    assert (await c.get("/api/health")).status_code == 200


async def test_same_origin_allowed(app: FastAPI, client: httpx.AsyncClient) -> None:
    _add_probe_routes(app)
    resp = await client.post(
        "/api/_probe/validate", json={"name": "a"}, headers={"Origin": "http://testserver"}
    )
    assert resp.status_code == 200


async def test_safe_methods_ignore_origin(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 200


async def test_every_response_has_a_request_id(client: httpx.AsyncClient) -> None:
    first = await client.get("/api/health")
    second = await client.post("/api/auth/login", json={"email": "x@example.com", "password": "nope-nope"})
    ids = {first.headers["X-Request-ID"], second.headers["X-Request-ID"]}
    assert len(ids) == 2 and all(len(i) == 16 for i in ids)
