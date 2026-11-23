"""DMs and friendship over real WebSockets."""

import json

from tests.conftest import ClientFactory
from tests.helpers import befriend, user
from tests.ws.helpers import recv_type, recv_types, ws_open
from tests.ws.test_presence import no_presence_of, presence_of


async def test_dm_delivery_and_friend_presence(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    wa = await ws_open(live_server, alice)
    wb = await ws_open(live_server, bob)
    assert await no_presence_of(wa, 2)  # strangers do not see each other

    req = (await alice.post("/api/friend-requests", json={"username": "bob"})).json()["request"]
    assert (await recv_type(wb, "friend_request_received"))["request"]["id"] == req["id"]
    await bob.post(f"/api/friend-requests/{req['id']}/accept")
    added = await recv_type(wa, "friend_added")
    assert added["user"]["username"] == "bob" and added["user"]["presence"] == "online"
    dm_id = added["dm_id"]

    await wa.send(
        json.dumps(
            {"type": "send_message", "req_id": "1", "chat": {"type": "dm", "id": dm_id}, "content": "yo"}
        )
    )
    got = await recv_type(wb, "new_message")
    assert got["message"]["chat"] == {"type": "dm", "id": dm_id} and got["message"]["content"] == "yo"
    await wb.send(json.dumps({"type": "typing", "chat": {"type": "dm", "id": dm_id}}))
    assert (await recv_type(wa, "typing"))["user"]["username"] == "bob"

    await wb.close()
    assert await presence_of(wa, 2) == "offline"  # friends see each other's presence


async def test_ban_freezes_dm_live(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    dm_id = await befriend(alice, bob, "bob")
    wa = await ws_open(live_server, alice)
    wb = await ws_open(live_server, bob)
    assert await presence_of(wa, 2) == "online"  # friends: bob's connect is visible

    await alice.post("/api/user-bans", json={"user_id": 2})
    got = await recv_types(wb, "friend_removed", "dm_frozen")
    assert got["dm_frozen"] == {"type": "dm_frozen", "dm_id": dm_id, "reason": "user_banned"}

    await wb.send(
        json.dumps(
            {"type": "send_message", "req_id": "x", "chat": {"type": "dm", "id": dm_id}, "content": "hi"}
        )
    )
    assert (await recv_type(wb, "error"))["code"] == "dm_frozen"
    await wb.send(json.dumps({"type": "typing", "chat": {"type": "dm", "id": dm_id}}))
    assert (await recv_type(wb, "error"))["code"] == "forbidden"
    await wb.close()
    assert await no_presence_of(wa, 2, 1.5)  # no longer in the audience
