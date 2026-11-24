"""Unread counters (docs/API_AND_WS_SPEC.md §9, docs/DATA_MODEL.md §2.13)."""

from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import rate_limiter
from tests.api.test_messages import bulk_messages
from tests.conftest import ClientFactory
from tests.helpers import Events, befriend, create_room, error_code, send, user


async def unread(c: httpx.AsyncClient) -> dict[tuple[str, int], dict[str, Any]]:
    resp = await c.get("/api/me/unread")
    assert resp.status_code == 200
    return {(i["chat"]["type"], i["chat"]["id"]): i for i in resp.json()["items"]}


async def test_room_unread_counts_others_messages_after_join(
    make_client: ClientFactory, db: AsyncSession
) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    rid = int((await create_room(alice, "general"))["id"])  # type: ignore[call-overload]
    await bulk_messages(db, rid, 1, 5, "old")  # history before bob joins is not unread
    await bob.post(f"/api/rooms/{rid}/join")
    assert (await unread(bob))[("room", rid)]["unread"] == 0

    for i in range(3):
        await send(alice, rid, f"new {i}")
    rate_limiter.reset()
    await send(bob, rid, "mine")  # own messages never count
    deleted = (await send(alice, rid, "oops")).json()
    await alice.delete(f"/api/messages/{deleted['id']}")  # deleted ones neither

    item = (await unread(bob))[("room", rid)]
    assert item["unread"] == 3 and item["last_message_id"] == deleted["id"]
    assert (await unread(alice))[("room", rid)]["unread"] == 1  # bob's message
    mine = (await bob.get("/api/rooms/mine")).json()
    assert mine["public"][0]["unread"] == 3


async def test_unread_is_capped_at_100(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    rid = int((await create_room(alice, "general"))["id"])  # type: ignore[call-overload]
    await bob.post(f"/api/rooms/{rid}/join")
    await bulk_messages(db, rid, 1, 150)
    assert (await unread(bob))[("room", rid)]["unread"] == 100


async def test_mark_read_moves_forward_only_and_notifies_all_tabs(
    make_client: ClientFactory, events: Events
) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    rid = int((await create_room(alice, "general"))["id"])  # type: ignore[call-overload]
    await bob.post(f"/api/rooms/{rid}/join")
    ids = [(await send(alice, rid, f"m{i}")).json()["id"] for i in range(4)]

    events.clear()
    resp = await bob.post(f"/api/rooms/{rid}/read", json={"last_read_message_id": ids[1]})
    assert resp.status_code == 204
    assert events == [
        (
            [2],
            {
                "type": "unread_update",
                "chat": {"type": "room", "id": rid},
                "unread": 2,
                "last_read_message_id": ids[1],
            },
        )
    ]
    # never backwards; ids beyond the newest message are clamped
    await bob.post(f"/api/rooms/{rid}/read", json={"last_read_message_id": ids[0]})
    assert (await unread(bob))[("room", rid)]["last_read_message_id"] == ids[1]
    await bob.post(f"/api/rooms/{rid}/read", json={"last_read_message_id": 10**12})
    item = (await unread(bob))[("room", rid)]
    assert item["unread"] == 0 and item["last_read_message_id"] == ids[-1]


async def test_mark_read_errors(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    rid = int((await create_room(alice, "general"))["id"])  # type: ignore[call-overload]
    secret = int((await create_room(alice, "secret", "private"))["id"])  # type: ignore[call-overload]
    resp = await bob.post(f"/api/rooms/{rid}/read", json={"last_read_message_id": 1})
    assert resp.status_code == 403 and error_code(resp) == "forbidden"
    resp = await bob.post(f"/api/rooms/{secret}/read", json={"last_read_message_id": 1})
    assert resp.status_code == 404
    resp = await bob.post("/api/dms/77/read", json={"last_read_message_id": 1})
    assert resp.status_code == 404
    for body in ({}, {"last_read_message_id": -1}, {"last_read_message_id": "x"}):
        resp = await alice.post(f"/api/rooms/{rid}/read", json=body)
        assert resp.status_code == 400 and error_code(resp) == "validation_error"
    assert (await (await make_client()).get("/api/me/unread")).status_code == 401


async def test_dm_unread_and_contacts(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    dm_id = await befriend(alice, bob, "bob")
    for i in range(2):
        await alice.post(f"/api/dms/{dm_id}/messages", json={"content": f"hi {i}"})
    assert (await unread(bob))[("dm", dm_id)]["unread"] == 2  # no cursor yet: the whole DM counts
    assert (await unread(alice))[("dm", dm_id)]["unread"] == 0
    friends = (await bob.get("/api/contacts")).json()["friends"]
    assert friends[0]["dm_id"] == dm_id and friends[0]["unread"] == 2

    await bob.post(f"/api/dms/{dm_id}/read", json={"last_read_message_id": 10**9})
    assert (await unread(bob))[("dm", dm_id)]["unread"] == 0
    rows = await db.scalar(text("SELECT count(*) FROM chat_read_states WHERE dm_id = :d"), {"d": dm_id})
    assert rows == 1

    # a frozen DM keeps its counter and can still be marked read
    await alice.post(f"/api/dms/{dm_id}/messages", json={"content": "bye"})
    await alice.delete("/api/friends/2")
    past = (await bob.get("/api/contacts")).json()["past_dms"]
    assert past[0]["unread"] == 1
    assert (await bob.post(f"/api/dms/{dm_id}/read", json={"last_read_message_id": 10**9})).status_code == 204


async def test_leaving_drops_the_counter(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    rid = int((await create_room(alice, "general"))["id"])  # type: ignore[call-overload]
    await bob.post(f"/api/rooms/{rid}/join")
    await send(alice, rid, "x")
    await bob.post(f"/api/rooms/{rid}/leave")
    assert ("room", rid) not in await unread(bob)
