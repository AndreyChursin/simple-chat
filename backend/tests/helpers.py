from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# (recipient user ids, event) pairs handed to the WebSocket manager; see the `events` fixture.
Events = list[tuple[list[int], dict[str, Any]]]

PASSWORD = "correct-horse-1"


async def register(
    client: httpx.AsyncClient, username: str = "alice", email: str | None = None, password: str = PASSWORD
) -> dict[str, object]:
    resp = await client.post(
        "/api/auth/register",
        json={"email": email or f"{username}@example.com", "username": username, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["user"]  # type: ignore[no-any-return]


async def login(
    client: httpx.AsyncClient, email: str, password: str = PASSWORD, keep: bool = False
) -> httpx.Response:
    return await client.post(
        "/api/auth/login", json={"email": email, "password": password, "keep_signed_in": keep}
    )


def error_code(resp: httpx.Response) -> str:
    return resp.json()["error"]["code"]  # type: ignore[no-any-return]


async def create_room(
    client: httpx.AsyncClient, name: str = "general", visibility: str = "public", description: str = ""
) -> dict[str, object]:
    resp = await client.post(
        "/api/rooms", json={"name": name, "description": description, "visibility": visibility}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


async def user(make_client: Callable[..., Awaitable[httpx.AsyncClient]], name: str) -> httpx.AsyncClient:
    """A new "browser" signed in as a freshly registered user."""
    c = await make_client()
    await register(c, name)
    return c


async def ban(db: AsyncSession, room_id: int, user_id: int, by: int | None = None) -> None:
    """Room bans arrive with moderation (stage 8); tests insert them directly."""
    await db.execute(
        text("INSERT INTO room_bans (room_id, user_id, banned_by_id) VALUES (:r, :u, :b)"),
        {"r": room_id, "u": user_id, "b": by},
    )
    await db.execute(
        text("DELETE FROM room_members WHERE room_id = :r AND user_id = :u"), {"r": room_id, "u": user_id}
    )
    await db.commit()


async def make_admin(db: AsyncSession, room_id: int, user_id: int) -> None:
    await db.execute(
        text("UPDATE room_members SET is_admin = true WHERE room_id = :r AND user_id = :u"),
        {"r": room_id, "u": user_id},
    )
    await db.commit()


async def send(client: httpx.AsyncClient, room_id: object, content: str, **extra: object) -> httpx.Response:
    return await client.post(f"/api/rooms/{room_id}/messages", json={"content": content, **extra})


async def befriend(a: httpx.AsyncClient, b: httpx.AsyncClient, b_username: str) -> int:
    """Make a and b friends; returns the DM id."""
    req = await a.post("/api/friend-requests", json={"username": b_username})
    assert req.status_code == 201, req.text
    acc = await b.post(f"/api/friend-requests/{req.json()['request']['id']}/accept")
    assert acc.status_code == 200, acc.text
    return int(acc.json()["dm_id"])
