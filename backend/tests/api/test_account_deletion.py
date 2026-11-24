"""Delete account cascade (docs/DATA_MODEL.md §3.2): the DB and the file system."""

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from tests.conftest import ClientFactory
from tests.helpers import PASSWORD, Events, befriend, create_room, error_code, login, send, user


async def upload(c: httpx.AsyncClient, chat: str, chat_id: object, name: str) -> dict[str, object]:
    resp = await c.post(
        f"/api/{chat}/{chat_id}/attachments", files={"file": (name, b"data " + name.encode())}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


def on_disk(stored: str) -> bool:
    return (get_settings().upload_dir / stored).is_file()


async def stored(db: AsyncSession, attachment_id: object) -> str:
    return str(
        await db.scalar(text("SELECT stored_path FROM attachments WHERE id = :i"), {"i": attachment_id})
    )


async def delete_me(c: httpx.AsyncClient, password: str = PASSWORD) -> httpx.Response:
    return await c.request("DELETE", "/api/me", json={"password": password})


async def test_wrong_password_changes_nothing(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    resp = await delete_me(alice, "wrong-password")
    assert resp.status_code == 401 and error_code(resp) == "invalid_current_password"
    assert (await alice.get("/api/auth/me")).status_code == 200
    resp = await alice.request("DELETE", "/api/me", json={})
    assert resp.status_code == 400 and error_code(resp) == "validation_error"
    assert (
        await (await make_client()).request("DELETE", "/api/me", json={"password": "x"})
    ).status_code == 401


async def test_delete_account_cascade(make_client: ClientFactory, db: AsyncSession, events: Events) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    carol = await user(make_client, "carol")
    alice_phone = await make_client()
    await login(alice_phone, "alice@example.com")

    # alice owns "mine" (bob is in it), is a member of bob's "theirs", has a DM with bob
    mine = (await create_room(alice, "mine"))["id"]
    theirs = (await create_room(bob, "theirs"))["id"]
    await bob.post(f"/api/rooms/{mine}/join")
    await alice.post(f"/api/rooms/{theirs}/join")
    await carol.post(f"/api/rooms/{theirs}/join")
    dm_id = await befriend(alice, bob, "bob")
    await alice.post("/api/friend-requests", json={"username": "carol"})  # pending

    in_mine = await upload(alice, "rooms", mine, "mine.txt")
    await send(alice, mine, "in my room", attachment_ids=[in_mine["id"]])
    in_theirs = await upload(alice, "rooms", theirs, "theirs.txt")
    kept_msg = (await send(alice, theirs, "in bob's room", attachment_ids=[in_theirs["id"]])).json()
    in_dm = await upload(alice, "dms", dm_id, "dm.txt")
    await alice.post(f"/api/dms/{dm_id}/messages", json={"content": "dm", "attachment_ids": [in_dm["id"]]})
    pending = await upload(alice, "rooms", theirs, "unsent.txt")
    paths = {
        k: await stored(db, a["id"])
        for k, a in [("mine", in_mine), ("theirs", in_theirs), ("dm", in_dm), ("pending", pending)]
    }
    assert all(on_disk(p) for p in paths.values())
    room_dir = get_settings().upload_dir / "rooms" / str(mine)
    assert room_dir.is_dir()

    events.clear()
    resp = await delete_me(alice)
    assert resp.status_code == 204
    assert "chat_session=" in resp.headers["set-cookie"] and "Max-Age=0" in resp.headers["set-cookie"]

    # sessions are gone everywhere
    assert (await alice.get("/api/auth/me")).status_code == 401
    assert (await alice_phone.get("/api/auth/me")).status_code == 401
    assert (await login(await make_client(), "alice@example.com")).status_code == 401

    # owned room: gone with messages, attachments and its directory
    assert await db.scalar(text("SELECT count(*) FROM rooms WHERE id = :r"), {"r": mine}) == 0
    assert await db.scalar(text("SELECT count(*) FROM messages WHERE room_id = :r"), {"r": mine}) == 0
    assert not on_disk(paths["mine"]) and not room_dir.exists()
    assert (await bob.get(f"/api/rooms/{mine}")).status_code == 404

    # other room: membership gone, count right, message and file kept as "Deleted user"
    room = (await bob.get(f"/api/rooms/{theirs}")).json()
    assert room["member_count"] == 2
    items = (await bob.get(f"/api/rooms/{theirs}/messages")).json()["items"]
    kept = next(m for m in items if m["id"] == kept_msg["id"])
    assert kept["author"]["id"] is None and kept["author"]["username"] == "Deleted user"
    assert kept["content"] == "in bob's room" and on_disk(paths["theirs"])
    assert (await carol.get(str(in_theirs["download_url"]))).status_code == 200

    # unsent upload: removed now, not by the 24 h GC
    assert not on_disk(paths["pending"])
    assert await db.scalar(text("SELECT count(*) FROM attachments WHERE id = :i"), {"i": pending["id"]}) == 0

    # DM stays with bob, frozen, history and file readable
    dm = (await bob.get(f"/api/dms/{dm_id}")).json()
    assert dm["frozen"] is True and dm["frozen_reason"] == "user_deleted"
    assert dm["peer"]["username"] == "Deleted user"
    assert (await bob.get(f"/api/dms/{dm_id}/messages")).json()["items"][0]["content"] == "dm"
    assert on_disk(paths["dm"]) and (await bob.get(str(in_dm["download_url"]))).status_code == 200
    contacts = (await bob.get("/api/contacts")).json()
    assert contacts["friends"] == [] and contacts["past_dms"][0]["dm_id"] == dm_id
    assert (await carol.get("/api/contacts")).json()["incoming"] == []

    # WS events after commit
    by_type: dict[str, list[tuple[list[int], dict[str, object]]]] = {}
    for users, event in events:
        by_type.setdefault(str(event["type"]), []).append((users, event))
    assert by_type["room_access_revoked"] == [
        ([2], {"type": "room_access_revoked", "room_id": mine, "reason": "room_deleted"})
    ]
    assert by_type["member_removed"] == [
        (
            [2, 3],
            {
                "type": "member_removed",
                "room_id": theirs,
                "user_id": 1,
                "reason": "account_deleted",
                "member_count": 2,
            },
        )
    ]
    assert by_type["dm_frozen"] == [([2], {"type": "dm_frozen", "dm_id": dm_id, "reason": "user_deleted"})]
    assert by_type["friend_removed"] == [([2], {"type": "friend_removed", "user_id": 1})]
    assert [e["result"] for _, e in by_type["friend_request_resolved"]] == ["cancelled"]

    # the name and e-mail are free again
    again = await make_client()
    reg = await again.post(
        "/api/auth/register", json={"email": "alice@example.com", "username": "alice", "password": PASSWORD}
    )
    assert reg.status_code == 201


async def test_dm_without_any_side_is_removed(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    dm_id = await befriend(alice, bob, "bob")
    att = await upload(alice, "dms", dm_id, "a.txt")
    await alice.post(f"/api/dms/{dm_id}/messages", json={"content": "", "attachment_ids": [att["id"]]})
    path = await stored(db, att["id"])
    assert (await delete_me(alice)).status_code == 204
    assert await db.scalar(text("SELECT count(*) FROM direct_conversations")) == 1
    assert (await delete_me(bob)).status_code == 204
    assert await db.scalar(text("SELECT count(*) FROM direct_conversations")) == 0
    assert await db.scalar(text("SELECT count(*) FROM messages")) == 0
    assert not on_disk(path) and not (get_settings().upload_dir / "dms" / str(dm_id)).exists()


async def test_ban_records_keep_deleted_moderator(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    carol = await user(make_client, "carol")
    rid = (await create_room(alice, "general"))["id"]
    await bob.post(f"/api/rooms/{rid}/join")
    await carol.post(f"/api/rooms/{rid}/join")
    await alice.post(f"/api/rooms/{rid}/admins/2")
    await bob.post(f"/api/rooms/{rid}/bans", json={"user_id": 3})
    assert (await delete_me(bob)).status_code == 204
    bans = (await alice.get(f"/api/rooms/{rid}/bans")).json()["items"]
    assert bans[0]["user"]["username"] == "carol" and bans[0]["banned_by"]["username"] == "Deleted user"
    assert (await alice.get(f"/api/rooms/{rid}")).json()["member_count"] == 1
