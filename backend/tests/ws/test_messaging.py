"""Real WebSocket tests against a live uvicorn (see the `live_server` fixture)."""

import asyncio
import json
import time
import uuid

from tests.conftest import ClientFactory
from tests.helpers import create_room, login, send, user
from tests.ws.helpers import close_code, nothing_of_type, recv_type, recv_types, ws_connect, ws_open


async def test_handshake_requires_session_and_origin(live_server: str, make_client: ClientFactory) -> None:
    anon = await ws_connect(live_server, None)
    assert await close_code(anon) == 4001
    alice = await user(make_client, "alice")
    evil = await ws_connect(live_server, alice, origin="http://evil.example")
    assert await close_code(evil) == 4003
    no_origin = await ws_connect(live_server, alice, origin=None)
    assert await close_code(no_origin) == 4003


async def test_hello(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    ws = await ws_connect(live_server, alice)
    hello = json.loads(await ws.recv())
    assert hello["type"] == "hello" and hello["user_id"] == 1 and hello["session_id"] == 1
    assert hello["heartbeat_interval_ms"] == 25000 and hello["afk_timeout_ms"] == 60000
    await ws.send(json.dumps({"type": "heartbeat"}))
    await ws.send(json.dumps({"type": "activity"}))
    await ws.send(json.dumps({"type": "bogus"}))
    error = await recv_type(ws, "error")
    assert error["code"] == "validation_error"
    await ws.close()


async def test_message_reaches_every_tab_of_every_member_fast(
    live_server: str, make_client: ClientFactory
) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    carol = await user(make_client, "carol")
    room = await create_room(alice, "general")
    await bob.post(f"/api/rooms/{room['id']}/join")
    # alice: two tabs in one browser + a second browser session
    alice_phone = await make_client()
    await login(alice_phone, "alice@example.com")
    a1 = await ws_open(live_server, alice, tab="a1")
    a2 = await ws_open(live_server, alice, tab="a2")
    a3 = await ws_open(live_server, alice_phone, tab="a3")
    b1 = await ws_open(live_server, bob, tab="b1")
    c1 = await ws_open(live_server, carol, tab="c1")  # not a member

    cid = str(uuid.uuid4())
    start = time.perf_counter()
    await b1.send(
        json.dumps(
            {
                "type": "send_message",
                "req_id": "r-1",
                "chat": {"type": "room", "id": room["id"]},
                "client_msg_id": cid,
                "content": "Hello\nworld 👋",
            }
        )
    )
    own = await recv_types(b1, "ack", "new_message")  # the sender's tabs get the broadcast too
    ack = own["ack"]
    assert ack["req_id"] == "r-1" and ack["message"]["content"] == "Hello\nworld 👋"
    assert ack["message"]["client_msg_id"] == cid
    assert own["new_message"]["message"]["id"] == ack["message"]["id"]
    for ws in (a1, a2, a3):
        event = await recv_type(ws, "new_message")
        assert event["message"]["id"] == ack["message"]["id"]
    assert time.perf_counter() - start < 3.0
    assert await nothing_of_type(c1, "new_message")

    # resend after a "reconnect": same message, no second fan-out
    await b1.send(
        json.dumps(
            {
                "type": "send_message",
                "req_id": "r-2",
                "chat": {"type": "room", "id": room["id"]},
                "client_msg_id": cid,
                "content": "Hello\nworld 👋",
            }
        )
    )
    assert (await recv_type(b1, "ack"))["message"]["id"] == ack["message"]["id"]
    assert await nothing_of_type(a1, "new_message")


async def test_ws_send_errors(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    other = await create_room(bob, "other")
    foreign = (await send(bob, other["id"], "x")).json()
    ws = await ws_open(live_server, alice)

    async def attempt(chat_id: int, content: str, **extra: object) -> str:
        await ws.send(
            json.dumps(
                {
                    "type": "send_message",
                    "req_id": "e",
                    "chat": {"type": "room", "id": chat_id},
                    "content": content,
                    **extra,
                }
            )
        )
        error = await recv_type(ws, "error")
        assert error["req_id"] == "e"
        return str(error["code"])

    assert await attempt(room["id"], "x" * 3073) == "message_too_large"
    assert await attempt(room["id"], " \n ") == "empty_message"
    assert await attempt(other["id"], "hi") == "forbidden"
    assert await attempt(999, "hi") == "room_not_found"
    assert await attempt(room["id"], "re", reply_to_message_id=foreign["id"]) == "invalid_reply_target"
    assert await attempt(room["id"], "hi", client_msg_id="not-a-uuid") == "validation_error"


async def test_rest_changes_are_broadcast(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    await bob.post(f"/api/rooms/{room['id']}/join")
    wb = await ws_open(live_server, bob)

    msg = (await send(alice, room["id"], "first")).json()
    assert (await recv_type(wb, "new_message"))["message"]["id"] == msg["id"]
    await alice.patch(f"/api/messages/{msg['id']}", json={"content": "edited"})
    edited = await recv_type(wb, "message_edited")
    assert edited["message"]["content"] == "edited" and edited["message"]["edited_at"]
    await alice.delete(f"/api/messages/{msg['id']}")
    deleted = await recv_type(wb, "message_deleted")
    assert deleted == {
        "type": "message_deleted",
        "chat": {"type": "room", "id": room["id"]},
        "message_id": msg["id"],
        "deleted_by": {"id": 1, "username": "alice"},
    }


async def test_membership_changes_update_delivery(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    wb = await ws_open(live_server, bob)

    await send(alice, room["id"], "before join")
    assert await nothing_of_type(wb, "new_message")

    await bob.post(f"/api/rooms/{room['id']}/join")
    assert (await recv_type(wb, "room_joined"))["room"]["id"] == room["id"]
    await send(alice, room["id"], "after join")
    assert (await recv_type(wb, "new_message"))["message"]["content"] == "after join"

    await bob.post(f"/api/rooms/{room['id']}/leave")
    revoked = await recv_type(wb, "room_access_revoked")
    assert revoked == {"type": "room_access_revoked", "room_id": room["id"], "reason": "left"}
    await send(alice, room["id"], "after leave")
    assert await nothing_of_type(wb, "new_message")


async def test_room_deletion_stops_delivery(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    await bob.post(f"/api/rooms/{room['id']}/join")
    wb = await ws_open(live_server, bob)
    await alice.delete(f"/api/rooms/{room['id']}")
    assert (await recv_type(wb, "room_access_revoked"))["reason"] == "room_deleted"


async def test_typing(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    other = await create_room(bob, "other")
    await bob.post(f"/api/rooms/{room['id']}/join")
    wa = await ws_open(live_server, alice)
    wb = await ws_open(live_server, bob)

    typing = json.dumps({"type": "typing", "chat": {"type": "room", "id": room["id"]}})
    await wa.send(typing)
    event = await recv_type(wb, "typing")
    assert event == {
        "type": "typing",
        "chat": {"type": "room", "id": room["id"]},
        "user": {"id": 1, "username": "alice"},
    }
    assert await nothing_of_type(wa, "typing")  # not echoed to the typist
    await wa.send(typing)  # throttled (3 s)
    assert await nothing_of_type(wb, "typing")
    # ...but sending a message resets the throttle: typing for the next one shows at once
    await wa.send(
        json.dumps(
            {
                "type": "send_message",
                "req_id": "m",
                "chat": {"type": "room", "id": room["id"]},
                "content": "x",
            }
        )
    )
    await recv_type(wa, "ack")
    await wa.send(typing)
    assert (await recv_type(wb, "typing"))["user"]["username"] == "alice"

    await wa.send(json.dumps({"type": "typing", "chat": {"type": "room", "id": other["id"]}}))
    assert (await recv_type(wa, "error"))["code"] == "forbidden"


async def test_revoking_a_session_closes_only_its_sockets(
    live_server: str, make_client: ClientFactory
) -> None:
    laptop = await user(make_client, "alice")
    phone = await make_client()
    await login(phone, "alice@example.com")
    w_laptop = await ws_open(live_server, laptop)
    w_phone = await ws_open(live_server, phone)

    assert (await phone.post("/api/auth/logout")).status_code == 204
    assert (await recv_type(w_phone, "session_revoked")) == {"type": "session_revoked"}
    assert await close_code(w_phone) == 4001

    await w_laptop.send(
        json.dumps(
            {"type": "send_message", "req_id": "x", "chat": {"type": "room", "id": 1}, "content": "hi"}
        )
    )
    assert (await recv_type(w_laptop, "error"))["code"] == "room_not_found"  # still connected


async def test_oversized_frame_closes_connection(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    ws = await ws_open(live_server, alice)
    await ws.send(json.dumps({"type": "heartbeat", "pad": "x" * 20_000}))
    assert await close_code(ws) == 1009


async def test_ws_send_rate_limit(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    ws = await ws_open(live_server, alice)
    for i in range(6):
        await ws.send(
            json.dumps(
                {
                    "type": "send_message",
                    "req_id": f"r{i}",
                    "chat": {"type": "room", "id": room["id"]},
                    "content": f"{i}",
                }
            )
        )
    results = []
    async with asyncio.timeout(3):
        while len(results) < 6:
            event = json.loads(await ws.recv())
            if event["type"] in ("ack", "error"):
                results.append(event.get("code", "ack"))
    assert results == ["ack"] * 5 + ["rate_limited"]
