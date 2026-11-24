"""mark_read over WS syncs every tab; deleting an account closes every tab and goes offline at once."""

import asyncio
import contextlib
import json
import time

from tests.conftest import ClientFactory
from tests.helpers import PASSWORD, befriend, create_room, login, send, user
from tests.ws.helpers import close_code, recv_type, ws_open


async def test_mark_read_over_ws_reaches_every_tab(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    rid = (await create_room(alice, "general"))["id"]
    await bob.post(f"/api/rooms/{rid}/join")
    last = (await send(alice, rid, "hello")).json()["id"]
    b1 = await ws_open(live_server, bob, tab="b1")
    b2 = await ws_open(live_server, bob, tab="b2")
    await b1.send(
        json.dumps({"type": "mark_read", "chat": {"type": "room", "id": rid}, "last_read_message_id": last})
    )
    for ws in (b1, b2):
        event = await recv_type(ws, "unread_update")
        assert event == {
            "type": "unread_update",
            "chat": {"type": "room", "id": rid},
            "unread": 0,
            "last_read_message_id": last,
        }
    await b1.send(
        json.dumps({"type": "mark_read", "chat": {"type": "dm", "id": 99}, "last_read_message_id": 1})
    )
    assert (await recv_type(b1, "error"))["code"] == "not_found"
    for ws in (b1, b2):
        await ws.close()


async def test_deleted_account_tabs_close_and_peers_see_offline(
    live_server: str, make_client: ClientFactory
) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    dm_id = await befriend(alice, bob, "bob")
    alice_phone = await make_client()
    await login(alice_phone, "alice@example.com")
    a1 = await ws_open(live_server, alice, tab="a1")
    a2 = await ws_open(live_server, alice_phone, tab="a2")
    b = await ws_open(live_server, bob, tab="b")
    snapshot = await recv_type(b, "presence_snapshot")
    assert {"user_id": 1, "presence": "online"} in snapshot["users"]
    assert (await recv_type(b, "presence_update"))["user_id"] == 2  # bob's own

    resp = await alice.request("DELETE", "/api/me", json={"password": PASSWORD})
    assert resp.status_code == 204
    for ws in (a1, a2):
        assert await close_code(ws) == 4001
    # exactly one presence change, offline, without the 1 s grace period and without a flicker back to
    # online while the two tabs close one after the other
    seen: list[dict[str, object]] = []
    frozen: dict[str, object] | None = None
    start = time.monotonic()
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(1.5):
            while True:
                event = json.loads(await b.recv())
                if event["type"] == "presence_update":
                    seen.append({**event, "after": time.monotonic() - start})
                elif event["type"] == "dm_frozen":
                    frozen = event
    assert [(e["user_id"], e["presence"]) for e in seen] == [(1, "offline")]
    assert float(seen[0]["after"]) < 0.9  # type: ignore[arg-type]
    assert frozen == {"type": "dm_frozen", "dm_id": dm_id, "reason": "user_deleted"}
    await b.close()
