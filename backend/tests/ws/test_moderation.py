"""A ban takes effect on live connections at once (docs/API_AND_WS_SPEC.md §7, §12.3)."""

import json
import uuid

from tests.conftest import ClientFactory
from tests.helpers import create_room, user
from tests.ws.helpers import nothing_of_type, recv_type, recv_types, ws_open


async def test_banned_member_is_cut_off_live(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    carol = await user(make_client, "carol")
    room = await create_room(alice, "general")
    rid = room["id"]
    for c in (bob, carol):
        await c.post(f"/api/rooms/{rid}/join")
    a = await ws_open(live_server, alice, tab="a")
    b = await ws_open(live_server, bob, tab="b")
    c1 = await ws_open(live_server, carol, tab="c1")
    c2 = await ws_open(live_server, carol, tab="c2")

    assert (await alice.post(f"/api/rooms/{rid}/admins/2")).status_code == 200
    for ws in (a, b, c1):
        role = await recv_type(ws, "member_role_changed")
        assert role == {"type": "member_role_changed", "room_id": rid, "user_id": 2, "role": "admin"}

    assert (await bob.post(f"/api/rooms/{rid}/bans", json={"user_id": 3})).status_code == 201
    for ws in (c1, c2):  # every tab of the banned user
        revoked = await recv_type(ws, "room_access_revoked")
        assert revoked == {"type": "room_access_revoked", "room_id": rid, "reason": "banned"}
    removed = await recv_type(a, "member_removed")
    assert removed["user_id"] == 3 and removed["reason"] == "banned" and removed["member_count"] == 2

    # nothing from the room reaches carol any more, and she cannot write over WS
    await b.send(
        json.dumps(
            {
                "type": "send_message",
                "req_id": "r-1",
                "chat": {"type": "room", "id": rid},
                "client_msg_id": str(uuid.uuid4()),
                "content": "after the ban",
            }
        )
    )
    await recv_types(b, "ack", "new_message")
    assert await nothing_of_type(c1, "new_message")
    await c1.send(
        json.dumps(
            {
                "type": "send_message",
                "req_id": "r-2",
                "chat": {"type": "room", "id": rid},
                "client_msg_id": str(uuid.uuid4()),
                "content": "let me in",
            }
        )
    )
    error = await recv_type(c1, "error")
    assert error["req_id"] == "r-2" and error["code"] in ("forbidden", "room_banned")
    await c1.send(json.dumps({"type": "typing", "chat": {"type": "room", "id": rid}}))
    assert (await recv_type(c1, "error"))["code"] in ("forbidden", "room_banned")
    assert await nothing_of_type(a, "typing")
    for ws in (a, b, c1, c2):
        await ws.close()
