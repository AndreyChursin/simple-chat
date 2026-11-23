"""Presence aggregation over tabs and sessions (docs/ARCHITECTURE.md §4.3, ROADMAP stage 5)."""

import json
import time

import pytest

from app.ws.manager import manager
from tests.conftest import ClientFactory
from tests.helpers import create_room, login, user
from tests.ws.helpers import close_code, recv_type, ws_open


class FakeClock:
    """Monotonic clock that tests can move forward."""

    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.monotonic() + self.offset

    def advance(self, seconds: float) -> None:
        self.offset += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(manager, "clock", fake)
    return fake


async def presence_of(ws, user_id: int, wait: float = 3.0) -> str:  # type: ignore[no-untyped-def]
    while True:
        event = await recv_type(ws, "presence_update", wait)
        if event["user_id"] == user_id:
            return str(event["presence"])


async def processed(ws) -> None:  # type: ignore[no-untyped-def]
    """Round trip: events of one socket are handled in order, so earlier ones are done after this."""
    await ws.send(json.dumps({"type": "sync-probe"}))
    await recv_type(ws, "error")


async def no_presence_of(ws, user_id: int, wait: float = 0.5) -> bool:  # type: ignore[no-untyped-def]
    try:
        await presence_of(ws, user_id, wait)
    except TimeoutError:
        return True
    return False


async def room_pair(make_client: ClientFactory):  # type: ignore[no-untyped-def]
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    await bob.post(f"/api/rooms/{room['id']}/join")
    return alice, bob, room


async def test_connect_announces_online_and_snapshot(live_server: str, make_client: ClientFactory) -> None:
    alice, bob, room = await room_pair(make_client)
    carol = await user(make_client, "carol")  # shares no room
    wb = await ws_open(live_server, bob)
    wc = await ws_open(live_server, carol)

    wa = await ws_open(live_server, alice)
    snapshot = await recv_type(wa, "presence_snapshot")
    assert snapshot["users"] == [{"user_id": 2, "presence": "online"}]
    assert await presence_of(wa, 1) == "online"  # own tabs learn it too
    assert await presence_of(wb, 1) == "online"
    assert await no_presence_of(wc, 1)

    members = (await bob.get(f"/api/rooms/{room['id']}/members")).json()["items"]
    assert {m["username"]: m["presence"] for m in members} == {"alice": "online", "bob": "online"}
    assert (await carol.get("/api/users/alice")).json()["presence"] == "online"


async def test_afk_when_no_tab_is_active(
    live_server: str, make_client: ClientFactory, clock: FakeClock
) -> None:
    alice, bob, _ = await room_pair(make_client)
    wb = await ws_open(live_server, bob)
    a1 = await ws_open(live_server, alice, tab="a1")
    a2 = await ws_open(live_server, alice, tab="a2")
    assert await presence_of(wb, 1) == "online"

    clock.advance(30)
    await a2.send(json.dumps({"type": "activity"}))  # one active tab keeps the user online
    await processed(a2)
    clock.advance(31)  # a1 idle 61 s, a2 idle 31 s
    await manager.tick()
    assert await no_presence_of(wb, 1)
    assert manager.get_presence(1) == "online"

    clock.advance(30)  # every tab idle > 60 s
    start = time.perf_counter()
    assert await presence_of(wb, 1) == "AFK"  # picked up by the 1 s ticker
    assert time.perf_counter() - start < 2.0

    await a1.send(json.dumps({"type": "activity"}))
    assert await presence_of(wb, 1) == "online"  # immediately, not on the next tick
    await a1.send(json.dumps({"type": "heartbeat"}))  # keeps the socket, not activity
    await processed(a1)
    clock.advance(61)
    assert await presence_of(wb, 1) == "AFK"
    assert a2.state.name == "OPEN"


async def test_offline_only_after_every_tab_of_every_session_closes(
    live_server: str, make_client: ClientFactory
) -> None:
    alice, bob, _ = await room_pair(make_client)
    alice_phone = await make_client()
    await login(alice_phone, "alice@example.com")
    wb = await ws_open(live_server, bob)
    tabs = [
        await ws_open(live_server, alice, tab="a1"),
        await ws_open(live_server, alice, tab="a2"),
        await ws_open(live_server, alice_phone, tab="p1"),
    ]
    assert await presence_of(wb, 1) == "online"

    for ws in tabs[:-1]:
        await ws.close()
    assert await no_presence_of(wb, 1, 1.5)
    assert manager.get_presence(1) == "online"

    start = time.perf_counter()
    await tabs[-1].close()
    assert await presence_of(wb, 1) == "offline"
    elapsed = time.perf_counter() - start
    assert 0.9 < elapsed < 2.0, elapsed  # 1 s grace, then at once
    assert (await bob.get("/api/users/alice")).json()["presence"] == "offline"


async def test_reload_does_not_blink_offline(live_server: str, make_client: ClientFactory) -> None:
    alice, bob, _ = await room_pair(make_client)
    wb = await ws_open(live_server, bob)
    wa = await ws_open(live_server, alice)
    assert await presence_of(wb, 1) == "online"

    await wa.close()  # page reload: the new socket comes within the grace period
    wa = await ws_open(live_server, alice)
    snapshot = await recv_type(wa, "presence_snapshot")
    assert {"user_id": 1, "presence": "online"} in snapshot["users"]
    assert await no_presence_of(wb, 1, 1.5)


async def test_revoked_session_goes_offline_other_session_keeps_user_online(
    live_server: str, make_client: ClientFactory
) -> None:
    alice, bob, _ = await room_pair(make_client)
    alice_phone = await make_client()
    await login(alice_phone, "alice@example.com")
    wb = await ws_open(live_server, bob)
    laptop = await ws_open(live_server, alice)
    phone = await ws_open(live_server, alice_phone)
    assert await presence_of(wb, 1) == "online"

    await alice_phone.post("/api/auth/logout")
    assert await close_code(phone) == 4001
    assert await no_presence_of(wb, 1, 1.5)
    await alice.post("/api/auth/logout")
    assert await close_code(laptop) == 4001
    assert await presence_of(wb, 1) == "offline"


async def test_new_room_member_gets_presence_updates(live_server: str, make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    wa = await ws_open(live_server, alice)
    wb = await ws_open(live_server, bob)
    assert await no_presence_of(wa, 2)  # nothing shared yet

    await bob.post(f"/api/rooms/{room['id']}/join")
    await wb.close()
    assert await presence_of(wa, 2) == "offline"
