import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import ClientFactory
from tests.helpers import Events, befriend, create_room, error_code, user


async def contacts(c: httpx.AsyncClient) -> dict:  # type: ignore[type-arg]
    resp = await c.get("/api/contacts")
    assert resp.status_code == 200
    return resp.json()  # type: ignore[no-any-return]


# --- friend requests -----------------------------------------------------------------------------------


async def test_request_and_accept(make_client: ClientFactory, events: Events) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    events.clear()

    resp = await alice.post("/api/friend-requests", json={"username": "BOB", "message": "  Hi Bob!  "})
    assert resp.status_code == 201
    req = resp.json()["request"]
    assert req["to"]["username"] == "bob" and req["message"] == "Hi Bob!"
    assert events[-1][0] == [2] and events[-1][1]["type"] == "friend_request_received"
    assert events[-1][1]["request"]["from"]["username"] == "alice"

    assert [r["to"]["username"] for r in (await contacts(alice))["outgoing"]] == ["bob"]
    incoming = (await contacts(bob))["incoming"]
    assert [(r["from"]["username"], r["message"]) for r in incoming] == [("alice", "Hi Bob!")]
    assert (await alice.get("/api/users/bob")).json()["friendship"] == "pending_out"
    assert (await bob.get("/api/users/alice")).json()["friendship"] == "pending_in"
    # not a friend until accepted
    assert (await alice.post("/api/dms", json={"user_id": 2})).status_code == 403

    events.clear()
    resp = await bob.post(f"/api/friend-requests/{req['id']}/accept")
    assert resp.status_code == 200
    body = resp.json()
    assert body["friend"]["username"] == "alice" and body["dm_id"] == 1
    assert {(tuple(u), e["type"]) for u, e in events} == {
        ((1,), "friend_request_resolved"),
        ((1,), "friend_added"),
        ((2,), "friend_added"),
    }
    friends = (await contacts(alice))["friends"]
    assert [(f["username"], f["dm_id"]) for f in friends] == [("bob", 1)]
    assert (await contacts(alice))["outgoing"] == [] and (await contacts(bob))["incoming"] == []
    assert (await alice.get("/api/users/bob")).json()["friendship"] == "friends"

    again = await bob.post(f"/api/friend-requests/{req['id']}/accept")
    assert again.status_code in (404, 409)


async def test_request_errors(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    resp = await alice.post("/api/friend-requests", json={"username": "nobody"})
    assert resp.status_code == 404 and error_code(resp) == "user_not_found"
    resp = await alice.post("/api/friend-requests", json={"username": "alice"})
    assert resp.status_code == 400
    resp = await alice.post("/api/friend-requests", json={"username": "bob", "message": "x" * 501})
    assert resp.status_code == 400 and error_code(resp) == "validation_error"
    assert (await alice.post("/api/friend-requests", json={"username": "bob"})).status_code == 201
    resp = await alice.post("/api/friend-requests", json={"username": "bob"})
    assert resp.status_code == 409 and error_code(resp) == "friend_request_exists"
    await befriend_existing(bob)
    resp = await alice.post("/api/friend-requests", json={"username": "bob"})
    assert resp.status_code == 409 and error_code(resp) == "already_friends"


async def befriend_existing(addressee: httpx.AsyncClient) -> None:
    incoming = (await contacts(addressee))["incoming"]
    assert (await addressee.post(f"/api/friend-requests/{incoming[0]['id']}/accept")).status_code == 200


async def test_request_back_accepts(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    await alice.post("/api/friend-requests", json={"username": "bob"})
    resp = await bob.post("/api/friend-requests", json={"username": "alice"})
    assert resp.status_code == 200
    assert resp.json()["friendship"] == "accepted" and resp.json()["friend"]["username"] == "alice"
    assert [f["username"] for f in (await contacts(bob))["friends"]] == ["alice"]


async def test_decline_and_cancel(make_client: ClientFactory, events: Events) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    req = (await alice.post("/api/friend-requests", json={"username": "bob"})).json()["request"]
    assert (await alice.post(f"/api/friend-requests/{req['id']}/decline")).status_code == 404  # not addressee
    assert (await alice.post(f"/api/friend-requests/{req['id']}/accept")).status_code == 404
    events.clear()
    assert (await bob.post(f"/api/friend-requests/{req['id']}/decline")).status_code == 204
    assert events == [
        ([1], {"type": "friend_request_resolved", "request_id": req["id"], "result": "declined"})
    ]
    assert (await contacts(alice))["outgoing"] == []

    req = (await alice.post("/api/friend-requests", json={"username": "bob"})).json()["request"]
    assert (await bob.delete(f"/api/friend-requests/{req['id']}")).status_code == 404  # not requester
    events.clear()
    assert (await alice.delete(f"/api/friend-requests/{req['id']}")).status_code == 204
    assert events == [
        ([2], {"type": "friend_request_resolved", "request_id": req["id"], "result": "cancelled"})
    ]
    assert (await contacts(bob))["incoming"] == []
    assert (await alice.delete(f"/api/friend-requests/{req['id']}")).status_code == 404


async def test_remove_friend_freezes_dm(make_client: ClientFactory, events: Events) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    dm_id = await befriend(alice, bob, "bob")
    msg = (await alice.post(f"/api/dms/{dm_id}/messages", json={"content": "hey"})).json()
    events.clear()

    assert (await bob.delete("/api/friends/1")).status_code == 204
    assert sorted((u, e["type"]) for u, e in events) == [
        ([1], "friend_removed"),
        ([1, 2], "dm_frozen"),
        ([2], "friend_removed"),
    ]
    assert next(e for _, e in events if e["type"] == "dm_frozen")["reason"] == "not_friends"
    assert (await bob.delete("/api/friends/1")).status_code == 404

    dm = (await alice.get(f"/api/dms/{dm_id}")).json()
    assert dm["frozen"] is True and dm["frozen_reason"] == "not_friends" and dm["peer"]["username"] == "bob"
    past = (await contacts(alice))["past_dms"]
    assert [(p["dm_id"], p["peer"]["username"], p["frozen"]) for p in past] == [(dm_id, "bob", True)]
    # history stays readable, but read-only for both
    assert [m["content"] for m in (await bob.get(f"/api/dms/{dm_id}/messages")).json()["items"]] == ["hey"]
    for resp in (
        await bob.post(f"/api/dms/{dm_id}/messages", json={"content": "x"}),
        await alice.patch(f"/api/messages/{msg['id']}", json={"content": "edit"}),
        await alice.delete(f"/api/messages/{msg['id']}"),
    ):
        assert resp.status_code == 403 and error_code(resp) == "dm_frozen", resp.text

    # friends again: same DM, writable again
    events.clear()
    assert await befriend(bob, alice, "alice") == dm_id
    assert ([1, 2], {"type": "dm_unfrozen", "dm_id": dm_id}) in events
    assert (await bob.post(f"/api/dms/{dm_id}/messages", json={"content": "back"})).status_code == 201


# --- DMs -----------------------------------------------------------------------------------------------


async def test_dm_works_like_a_room(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    carol = await user(make_client, "carol")
    dm_id = await befriend(alice, bob, "bob")
    opened = await alice.post("/api/dms", json={"user_id": 2})
    assert opened.status_code == 200
    assert opened.json() == {
        "dm_id": dm_id,
        "peer": {"id": 2, "username": "bob", "display_name": None, "presence": "offline"},
        "frozen": False,
        "frozen_reason": None,
    }
    url = f"/api/dms/{dm_id}/messages"
    first = (await alice.post(url, json={"content": "hello 👋"})).json()
    assert first["chat"] == {"type": "dm", "id": dm_id}
    reply = await bob.post(url, json={"content": "hi", "reply_to_message_id": first["id"]})
    assert reply.status_code == 201 and reply.json()["reply_to"]["excerpt"] == "hello 👋"
    assert (await bob.patch(f"/api/messages/{first['id']}", json={"content": "x"})).status_code == 403
    assert (await bob.delete(f"/api/messages/{first['id']}")).status_code == 403  # no moderators in a DM
    assert (await alice.patch(f"/api/messages/{first['id']}", json={"content": "hello!"})).json()["edited_at"]
    assert (await bob.delete(f"/api/messages/{reply.json()['id']}")).status_code == 204
    page = (await bob.get(url)).json()
    assert [(m["content"], m["deleted"]) for m in page["items"]] == [("hello!", False), ("", True)]
    found = (await alice.get(f"{url}/search", params={"q": "hell"})).json()["items"]
    assert [m["id"] for m in found] == [first["id"]]
    assert (await alice.get(f"{url}/around/{first['id']}")).status_code == 200
    resp = await alice.post(url, json={"content": "x" * 3073})
    assert resp.status_code == 413

    # a third user sees nothing
    for resp in (
        await carol.get(url),
        await carol.post(url, json={"content": "x"}),
        await carol.get(f"/api/dms/{dm_id}"),
        await carol.patch(f"/api/messages/{first['id']}", json={"content": "x"}),
    ):
        assert resp.status_code == 404, resp.text
    # replies only within the same chat
    room = await create_room(alice, "general")
    room_msg = (await alice.post(f"/api/rooms/{room['id']}/messages", json={"content": "r"})).json()
    resp = await alice.post(url, json={"content": "x", "reply_to_message_id": room_msg["id"]})
    assert resp.status_code == 400 and error_code(resp) == "invalid_reply_target"


async def test_open_dm_errors(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    await user(make_client, "bob")
    resp = await alice.post("/api/dms", json={"user_id": 2})
    assert resp.status_code == 403 and error_code(resp) == "not_friends"
    resp = await alice.post("/api/dms", json={"user_id": 99})
    assert resp.status_code == 404
    assert (await alice.get("/api/dms/99")).status_code == 404


# --- personal bans -------------------------------------------------------------------------------------


async def test_ban_ends_friendship_and_freezes_dm(
    make_client: ClientFactory, db: AsyncSession, events: Events
) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    dm_id = await befriend(alice, bob, "bob")
    await bob.post(f"/api/dms/{dm_id}/messages", json={"content": "before"})
    room = await create_room(alice, "general")
    events.clear()

    resp = await alice.post("/api/user-bans", json={"user_id": 2})
    assert resp.status_code == 201 and resp.json()["user"]["username"] == "bob" and resp.json()["created_at"]
    assert sorted((u, e["type"]) for u, e in events) == [
        ([1], "friend_removed"),
        ([1, 2], "dm_frozen"),
        ([2], "friend_removed"),
    ]
    assert next(e for _, e in events if e["type"] == "dm_frozen")["reason"] == "user_banned"
    assert await db.scalar(text("SELECT count(*) FROM friendships")) == 0
    assert (await contacts(alice))["friends"] == [] and (await contacts(bob))["friends"] == []

    # no contact from either side
    for resp in (
        await bob.post("/api/friend-requests", json={"username": "alice"}),
        await alice.post("/api/friend-requests", json={"username": "bob"}),
        await bob.post("/api/dms", json={"user_id": 1}),
    ):
        assert resp.status_code == 403 and error_code(resp) == "user_banned", resp.text
    resp = await bob.post(f"/api/dms/{dm_id}/messages", json={"content": "x"})
    assert resp.status_code == 403 and error_code(resp) == "dm_frozen"
    resp = await alice.post(f"/api/rooms/{room['id']}/invitations", json={"username": "bob"})
    assert resp.status_code == 403 and error_code(resp) == "user_banned"
    # history stays visible to both
    assert len((await bob.get(f"/api/dms/{dm_id}/messages")).json()["items"]) == 1
    assert (await bob.get(f"/api/dms/{dm_id}")).json()["frozen_reason"] == "user_banned"
    assert (await bob.get("/api/users/alice")).json()["has_banned_me"] is True
    assert (await alice.get("/api/users/bob")).json()["banned_by_me"] is True

    bans = (await alice.get("/api/user-bans")).json()["items"]
    assert [b["user"]["username"] for b in bans] == ["bob"]
    assert (await bob.get("/api/user-bans")).json()["items"] == []
    resp = await alice.post("/api/user-bans", json={"user_id": 2})
    assert resp.status_code == 409 and error_code(resp) == "already_banned"

    # unban: friendship not restored, DM stays frozen (now: not friends)
    assert (await bob.delete("/api/user-bans/1")).status_code == 404  # bob banned nobody
    assert (await alice.delete("/api/user-bans/2")).status_code == 204
    assert (await alice.get(f"/api/dms/{dm_id}")).json()["frozen_reason"] == "not_friends"
    assert (await alice.delete("/api/user-bans/2")).status_code == 404
    assert (await bob.post("/api/friend-requests", json={"username": "alice"})).status_code == 201


async def test_ban_removes_pending_request(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    await bob.post("/api/friend-requests", json={"username": "alice"})
    assert (await alice.post("/api/user-bans", json={"user_id": 2})).status_code == 201
    assert (await contacts(alice))["incoming"] == [] and (await contacts(bob))["outgoing"] == []


async def test_ban_errors(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    assert (await alice.post("/api/user-bans", json={"user_id": 1})).status_code == 400
    resp = await alice.post("/api/user-bans", json={"user_id": 42})
    assert resp.status_code == 404
    assert (await alice.post("/api/user-bans", json={})).status_code == 400


async def test_contacts_require_auth(client: httpx.AsyncClient) -> None:
    for method, url in (
        ("GET", "/api/contacts"),
        ("POST", "/api/friend-requests"),
        ("GET", "/api/user-bans"),
        ("POST", "/api/dms"),
        ("GET", "/api/dms/1"),
    ):
        assert (await client.request(method, url, json={})).status_code == 401


async def test_accepting_twice_is_a_conflict(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    req = (await alice.post("/api/friend-requests", json={"username": "bob"})).json()["request"]
    assert (await bob.post(f"/api/friend-requests/{req['id']}/accept")).status_code == 200
    resp = await bob.post(f"/api/friend-requests/{req['id']}/accept")
    assert resp.status_code == 409 and error_code(resp) == "request_not_pending"
