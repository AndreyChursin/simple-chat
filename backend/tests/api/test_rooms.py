from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from tests.conftest import ClientFactory
from tests.helpers import Events, ban, create_room, error_code, make_admin, register, user

# --- create ------------------------------------------------------------------------------------------


async def test_create_room_makes_creator_owner(make_client: ClientFactory, events: Events) -> None:
    alice = await user(make_client, "alice")
    resp = await alice.post(
        "/api/rooms", json={"name": "  general ", "description": "Talk", "visibility": "public"}
    )
    assert resp.status_code == 201
    room = resp.json()
    assert room["name"] == "general" and room["description"] == "Talk"
    assert room["visibility"] == "public" and room["member_count"] == 1
    assert room["my_role"] == "owner" and room["owner"]["username"] == "alice" and room["unread"] == 0
    assert events == [([1], {"type": "room_joined", "room": room})]

    detail = (await alice.get(f"/api/rooms/{room['id']}")).json()
    assert [a["username"] for a in detail["admins"]] == ["alice"]


async def test_room_name_unique_case_insensitive(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    await create_room(alice, "General")
    resp = await alice.post("/api/rooms", json={"name": "gENERAL", "visibility": "private"})
    assert resp.status_code == 409 and error_code(resp) == "room_name_taken"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "ab", "visibility": "public"},
        {"name": "x" * 65, "visibility": "public"},
        {"name": "ok name", "visibility": "secret"},
        {"name": "ok name"},
        {"name": "bad\nname", "visibility": "public"},
        {"name": "ok name", "visibility": "public", "description": "d" * 1001},
        {"name": "ok name", "visibility": "public", "owner_id": 5},
    ],
)
async def test_create_room_validation(client: httpx.AsyncClient, body: dict[str, Any]) -> None:
    await register(client)
    resp = await client.post("/api/rooms", json=body)
    assert resp.status_code == 400 and error_code(resp) == "validation_error"


async def test_room_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/rooms/catalog")).status_code == 401
    assert (await client.get("/api/rooms/mine")).status_code == 401
    assert (await client.post("/api/rooms", json={"name": "abc", "visibility": "public"})).status_code == 401
    assert (await client.get("/api/invitations")).status_code == 401


# --- catalog / mine / get ----------------------------------------------------------------------------


async def test_catalog_lists_only_public_rooms_with_counts(
    make_client: ClientFactory, db: AsyncSession
) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    small = await create_room(alice, "small", description="tiny room")
    big = await create_room(alice, "big")
    await create_room(alice, "secret", "private")
    assert (await bob.post(f"/api/rooms/{big['id']}/join")).status_code == 200

    resp = await bob.get("/api/rooms/catalog")
    assert resp.status_code == 200
    page = resp.json()
    assert [(r["name"], r["member_count"], r["my_role"]) for r in page["items"]] == [
        ("big", 2, "member"),
        ("small", 1, None),
    ]
    assert page["items"][1]["description"] == "tiny room"
    assert page["has_more"] is False and page["next_cursor"] is None
    assert all(r["is_banned"] is False for r in page["items"])

    await ban(db, small["id"], 2)
    items = (await bob.get("/api/rooms/catalog")).json()["items"]
    assert [r["is_banned"] for r in items] == [False, True]


async def test_catalog_search_and_cursor(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    for i in range(5):
        await create_room(alice, f"python-{i}", description="snakes")
    await create_room(alice, "rust", description="crabs and 100% safety")
    await create_room(alice, "hidden-python", "private")

    found = (await alice.get("/api/rooms/catalog", params={"search": "PYTHON"})).json()["items"]
    assert [r["name"] for r in found] == [f"python-{i}" for i in range(5)]
    by_description = (await alice.get("/api/rooms/catalog", params={"search": "crab"})).json()["items"]
    assert [r["name"] for r in by_description] == ["rust"]
    # LIKE wildcards are literal
    assert [
        r["name"] for r in (await alice.get("/api/rooms/catalog", params={"search": "0%"})).json()["items"]
    ] == ["rust"]
    assert (await alice.get("/api/rooms/catalog", params={"search": "_"})).json()["items"] == []

    seen: list[str] = []
    cursor = None
    while True:
        params: dict[str, Any] = {"search": "python", "limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (await alice.get("/api/rooms/catalog", params=params)).json()
        seen += [r["name"] for r in page["items"]]
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert seen == [f"python-{i}" for i in range(5)]


async def test_my_rooms_split_by_visibility(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    await create_room(alice, "zeta")
    await create_room(alice, "alpha")
    await create_room(alice, "vault", "private")
    other = await create_room(bob, "bobs")
    await alice.post(f"/api/rooms/{other['id']}/join")

    mine = (await alice.get("/api/rooms/mine")).json()
    assert [(r["name"], r["my_role"]) for r in mine["public"]] == [
        ("alpha", "owner"),
        ("bobs", "member"),
        ("zeta", "owner"),
    ]
    assert [r["name"] for r in mine["private"]] == ["vault"]


async def test_private_room_is_hidden_from_non_members(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "vault", "private")
    rid = room["id"]
    for resp in (
        await bob.get(f"/api/rooms/{rid}"),
        await bob.post(f"/api/rooms/{rid}/join"),
        await bob.get(f"/api/rooms/{rid}/members"),
        await bob.patch(f"/api/rooms/{rid}", json={"description": "x"}),
        await bob.delete(f"/api/rooms/{rid}"),
        await bob.post(f"/api/rooms/{rid}/invitations", json={"username": "alice"}),
        await bob.get("/api/rooms/999"),
    ):
        assert resp.status_code == 404 and error_code(resp) == "room_not_found", resp.text


async def test_get_public_room_as_non_member_and_banned(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    resp = await bob.get(f"/api/rooms/{room['id']}")
    assert resp.status_code == 200 and resp.json()["my_role"] is None

    await ban(db, room["id"], 2, by=1)
    resp = await bob.get(f"/api/rooms/{room['id']}")
    assert resp.status_code == 403 and error_code(resp) == "room_banned"


# --- join / leave ------------------------------------------------------------------------------------


async def test_join_and_leave_public_room(
    make_client: ClientFactory, db: AsyncSession, events: Events
) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    rid = room["id"]
    events.clear()

    resp = await bob.post(f"/api/rooms/{rid}/join")
    assert resp.status_code == 200
    assert resp.json()["my_role"] == "member" and resp.json()["member_count"] == 2
    assert [(u, e["type"]) for u, e in events] == [([2], "room_joined"), ([1], "member_joined")]
    assert events[1][1]["user"]["username"] == "bob" and events[1][1]["member_count"] == 2
    read_state = await db.scalar(
        text("SELECT last_read_message_id FROM chat_read_states WHERE user_id = 2 AND room_id = :r"),
        {"r": rid},
    )
    assert read_state == 0

    again = await bob.post(f"/api/rooms/{rid}/join")
    assert again.status_code == 409 and error_code(again) == "already_member"

    events.clear()
    assert (await bob.post(f"/api/rooms/{rid}/leave")).status_code == 204
    assert events == [
        ([2], {"type": "room_access_revoked", "room_id": rid, "reason": "left"}),
        ([1], {"type": "member_removed", "room_id": rid, "user_id": 2, "reason": "left", "member_count": 1}),
    ]
    assert (await alice.get(f"/api/rooms/{rid}")).json()["member_count"] == 1
    assert await db.scalar(text("SELECT count(*) FROM chat_read_states WHERE user_id = 2")) == 0

    not_member = await bob.post(f"/api/rooms/{rid}/leave")
    assert not_member.status_code == 404
    assert (await bob.get(f"/api/rooms/{rid}/members")).status_code == 403


async def test_banned_user_cannot_join_public_room(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    await ban(db, room["id"], 2, by=1)
    resp = await bob.post(f"/api/rooms/{room['id']}/join")
    assert resp.status_code == 403 and error_code(resp) == "room_banned"


async def test_owner_cannot_leave(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    resp = await alice.post(f"/api/rooms/{room['id']}/leave")
    assert resp.status_code == 400 and error_code(resp) == "owner_cannot_leave"


# --- members -----------------------------------------------------------------------------------------


async def test_members_ordered_by_role_then_name(make_client: ClientFactory, db: AsyncSession) -> None:
    zed = await user(make_client, "zed")
    room = await create_room(zed, "general")
    rid = room["id"]
    for name in ("carol", "Bob", "dave", "amy"):
        c = await user(make_client, name)
        await c.post(f"/api/rooms/{rid}/join")
    await make_admin(db, rid, 4)  # dave

    resp = await zed.get(f"/api/rooms/{rid}/members")
    assert resp.status_code == 200
    page = resp.json()
    assert [(m["username"], m["role"]) for m in page["items"]] == [
        ("zed", "owner"),
        ("dave", "admin"),
        ("amy", "member"),
        ("Bob", "member"),
        ("carol", "member"),
    ]
    assert page["items"][0]["presence"] == "offline" and page["items"][0]["joined_at"]

    first = (await zed.get(f"/api/rooms/{rid}/members", params={"limit": 2})).json()
    assert [m["username"] for m in first["items"]] == ["zed", "dave"] and first["has_more"]
    rest = (await zed.get(f"/api/rooms/{rid}/members", params={"cursor": first["next_cursor"]})).json()
    assert [m["username"] for m in rest["items"]] == ["amy", "Bob", "carol"] and not rest["has_more"]

    found = (await zed.get(f"/api/rooms/{rid}/members", params={"search": "B"})).json()["items"]
    assert [m["username"] for m in found] == ["Bob"]


async def test_members_of_public_room_forbidden_for_non_member(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    resp = await bob.get(f"/api/rooms/{room['id']}/members")
    assert resp.status_code == 403 and error_code(resp) == "forbidden"


# --- update ------------------------------------------------------------------------------------------


async def test_owner_updates_settings(make_client: ClientFactory, events: Events) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    rid = room["id"]
    await bob.post(f"/api/rooms/{rid}/join")
    events.clear()

    resp = await alice.patch(
        f"/api/rooms/{rid}", json={"name": "lobby", "description": "new", "visibility": "private"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["name"], body["description"], body["visibility"], body["my_role"]) == (
        "lobby",
        "new",
        "private",
        "owner",
    )
    assert sorted((u, e["room"]["my_role"]) for u, e in events) == [([1], "owner"), ([2], "member")]
    assert all(e["type"] == "room_updated" and e["room"]["name"] == "lobby" for _, e in events)
    assert (await bob.get("/api/rooms/catalog")).json()["items"] == []

    # Changing only the letter case of the own name is not a conflict
    assert (await alice.patch(f"/api/rooms/{rid}", json={"name": "Lobby"})).json()["name"] == "Lobby"


async def test_update_room_errors(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    carol = await user(make_client, "carol")
    room = await create_room(alice, "general")
    await create_room(alice, "taken")
    rid = room["id"]
    await bob.post(f"/api/rooms/{rid}/join")
    await make_admin(db, rid, 2)

    for c in (bob, carol):  # admin and non-member of a public room
        resp = await c.patch(f"/api/rooms/{rid}", json={"description": "hack"})
        assert resp.status_code == 403 and error_code(resp) == "not_room_owner"
    resp = await alice.patch(f"/api/rooms/{rid}", json={"name": "TAKEN"})
    assert resp.status_code == 409 and error_code(resp) == "room_name_taken"
    resp = await alice.patch(f"/api/rooms/{rid}", json={"owner_id": 2})
    assert resp.status_code == 400 and error_code(resp) == "validation_error"
    resp = await alice.patch(f"/api/rooms/{rid}", json={"name": "no"})
    assert resp.status_code == 400


# --- delete ------------------------------------------------------------------------------------------


async def test_owner_deletes_room_with_cascade_and_files(
    make_client: ClientFactory, db: AsyncSession, events: Events
) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    await user(make_client, "carol")
    room = await create_room(alice, "general")
    rid = room["id"]
    await bob.post(f"/api/rooms/{rid}/join")
    await alice.post(f"/api/rooms/{rid}/invitations", json={"username": "carol"})
    await ban(db, rid, 2, by=1)
    upload_dir = get_settings().upload_dir / "rooms" / str(rid) / "2026" / "11"
    upload_dir.mkdir(parents=True)
    (upload_dir / "f00d").write_bytes(b"data")
    events.clear()

    resp = await alice.delete(f"/api/rooms/{rid}")
    assert resp.status_code == 204
    assert events == [([1], {"type": "room_access_revoked", "room_id": rid, "reason": "room_deleted"})]
    for table in ("rooms", "room_members", "room_bans", "room_invitations", "chat_read_states"):
        column = "id" if table == "rooms" else "room_id"
        count = await db.scalar(text(f"SELECT count(*) FROM {table} WHERE {column} = :r"), {"r": rid})
        assert count == 0, table
    assert not Path(get_settings().upload_dir / "rooms" / str(rid)).exists()
    assert (await alice.get(f"/api/rooms/{rid}")).status_code == 404


async def test_only_owner_deletes_room(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    await bob.post(f"/api/rooms/{room['id']}/join")
    await make_admin(db, room["id"], 2)
    resp = await bob.delete(f"/api/rooms/{room['id']}")
    assert resp.status_code == 403 and error_code(resp) == "not_room_owner"
    assert (await alice.delete("/api/rooms/12345")).status_code == 404


# --- database invariants -----------------------------------------------------------------------------


async def test_owner_must_stay_admin_member(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    with pytest.raises(DBAPIError, match="owner must be an admin member"):
        await db.execute(
            text("UPDATE room_members SET is_admin = false WHERE room_id = :r"), {"r": room["id"]}
        )
        await db.commit()
    await db.rollback()
    with pytest.raises(DBAPIError, match="owner must be an admin member"):
        await db.execute(text("DELETE FROM room_members WHERE room_id = :r"), {"r": room["id"]})
        await db.commit()
