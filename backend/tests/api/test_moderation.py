"""Roles and room bans (docs/API_AND_WS_SPEC.md §7)."""

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import ClientFactory
from tests.helpers import Events, create_room, error_code, send, user


async def room_with(
    make_client: ClientFactory, *names: str, visibility: str = "public"
) -> tuple[int, list[httpx.AsyncClient]]:
    """alice owns the room (user 1); the other names (users 2, 3, ...) join it."""
    owner = await user(make_client, "alice")
    room = await create_room(owner, "general", visibility)
    rid = int(room["id"])  # type: ignore[call-overload]
    clients = [owner]
    for name in names:
        c = await user(make_client, name)
        if visibility == "public":
            assert (await c.post(f"/api/rooms/{rid}/join")).status_code == 200
        clients.append(c)
    return rid, clients


async def roles(c: httpx.AsyncClient, rid: int) -> dict[str, str]:
    items = (await c.get(f"/api/rooms/{rid}/members")).json()["items"]
    return {m["username"]: m["role"] for m in items}


# --- admins -------------------------------------------------------------------------------------------


async def test_owner_grants_and_revokes_admin(make_client: ClientFactory, events: Events) -> None:
    rid, (alice, bob, carol) = await room_with(make_client, "bob", "carol")
    events.clear()
    resp = await alice.post(f"/api/rooms/{rid}/admins/2")
    assert resp.status_code == 200
    assert resp.json()["username"] == "bob" and resp.json()["role"] == "admin"
    assert events == [
        ([1, 2, 3], {"type": "member_role_changed", "room_id": rid, "user_id": 2, "role": "admin"})
    ]
    assert await roles(carol, rid) == {"alice": "owner", "bob": "admin", "carol": "member"}
    detail = (await bob.get(f"/api/rooms/{rid}")).json()
    assert detail["my_role"] == "admin" and [a["username"] for a in detail["admins"]] == ["alice", "bob"]

    resp = await alice.post(f"/api/rooms/{rid}/admins/2")
    assert resp.status_code == 409 and error_code(resp) == "already_admin"
    resp = await alice.post(f"/api/rooms/{rid}/admins/1")  # the owner already is an admin
    assert resp.status_code == 409 and error_code(resp) == "already_admin"

    events.clear()
    resp = await alice.delete(f"/api/rooms/{rid}/admins/2")
    assert resp.status_code == 200 and resp.json()["role"] == "member"
    assert events == [
        ([1, 2, 3], {"type": "member_role_changed", "room_id": rid, "user_id": 2, "role": "member"})
    ]
    resp = await alice.delete(f"/api/rooms/{rid}/admins/2")
    assert resp.status_code == 409 and error_code(resp) == "not_admin"


async def test_only_owner_grants_admin(make_client: ClientFactory) -> None:
    rid, (alice, bob, carol) = await room_with(make_client, "bob", "carol")
    await alice.post(f"/api/rooms/{rid}/admins/2")
    for c in (bob, carol):  # an admin and a member
        resp = await c.post(f"/api/rooms/{rid}/admins/3")
        assert resp.status_code == 403 and error_code(resp) == "not_room_owner"
    dave = await user(make_client, "dave")
    resp = await alice.post(f"/api/rooms/{rid}/admins/4")  # not a member
    assert resp.status_code == 404 and error_code(resp) == "not_found"
    resp = await dave.post(f"/api/rooms/{rid}/admins/4")  # not a member of the public room
    assert resp.status_code == 403 and error_code(resp) == "not_room_owner"


async def test_admin_demotes_other_admins_but_never_the_owner(make_client: ClientFactory) -> None:
    rid, (alice, bob, carol, dave) = await room_with(make_client, "bob", "carol", "dave")
    await alice.post(f"/api/rooms/{rid}/admins/2")
    await alice.post(f"/api/rooms/{rid}/admins/3")

    resp = await bob.delete(f"/api/rooms/{rid}/admins/1")
    assert resp.status_code == 403 and error_code(resp) == "cannot_modify_owner"
    resp = await alice.delete(f"/api/rooms/{rid}/admins/1")
    assert resp.status_code == 403 and error_code(resp) == "cannot_modify_owner"
    resp = await dave.delete(f"/api/rooms/{rid}/admins/3")
    assert resp.status_code == 403 and error_code(resp) == "not_room_admin"
    resp = await bob.delete(f"/api/rooms/{rid}/admins/99")
    assert resp.status_code == 404

    assert (await bob.delete(f"/api/rooms/{rid}/admins/3")).status_code == 200
    assert (await bob.delete(f"/api/rooms/{rid}/admins/2")).status_code == 200  # stepping down
    assert await roles(alice, rid) == {"alice": "owner", "bob": "member", "carol": "member", "dave": "member"}
    # a former admin has no admin rights left
    resp = await bob.get(f"/api/rooms/{rid}/bans")
    assert resp.status_code == 403 and error_code(resp) == "not_room_admin"


async def test_admin_rights_follow_the_role(make_client: ClientFactory) -> None:
    rid, (alice, bob, carol) = await room_with(make_client, "bob", "carol")
    msg = (await send(carol, rid, "spam")).json()
    resp = await bob.delete(f"/api/messages/{msg['id']}")
    assert resp.status_code == 403
    await alice.post(f"/api/rooms/{rid}/admins/2")
    assert (await bob.delete(f"/api/messages/{msg['id']}")).status_code == 204
    # admins cannot change settings, appoint admins or delete the room
    resp = await bob.patch(f"/api/rooms/{rid}", json={"description": "x"})
    assert resp.status_code == 403 and error_code(resp) == "not_room_owner"
    resp = await bob.delete(f"/api/rooms/{rid}")
    assert resp.status_code == 403 and error_code(resp) == "not_room_owner"


# --- bans ---------------------------------------------------------------------------------------------


async def test_ban_removes_member_and_revokes_access(
    make_client: ClientFactory, events: Events, db: AsyncSession
) -> None:
    rid, (alice, bob, carol) = await room_with(make_client, "bob", "carol")
    await alice.post(f"/api/rooms/{rid}/admins/2")
    files_resp = await carol.post(
        f"/api/rooms/{rid}/attachments", files={"file": ("a.txt", b"hello", "text/plain")}
    )
    att = files_resp.json()
    await send(carol, rid, "with file", attachment_ids=[att["id"]])

    events.clear()
    resp = await bob.post(f"/api/rooms/{rid}/bans", json={"user_id": 3, "reason": "  spam  "})
    assert resp.status_code == 201
    body = resp.json()
    assert body["user"]["username"] == "carol" and body["banned_by"]["username"] == "bob"
    assert body["reason"] == "spam" and body["banned_at"]
    assert events == [
        ([3], {"type": "room_access_revoked", "room_id": rid, "reason": "banned"}),
        (
            [1, 2],
            {"type": "member_removed", "room_id": rid, "user_id": 3, "reason": "banned", "member_count": 2},
        ),
    ]

    # the banned user is out at once: messages, files, members, join, catalog
    assert (await carol.get(f"/api/rooms/{rid}/messages")).status_code == 403
    assert (await send(carol, rid, "still here?")).status_code == 403
    assert (await carol.get(att["download_url"])).status_code == 404
    assert (await alice.get(att["download_url"])).status_code == 200  # the file stays in the room
    resp = await carol.post(f"/api/rooms/{rid}/join")
    assert resp.status_code == 403 and error_code(resp) == "room_banned"
    catalog = (await carol.get("/api/rooms/catalog")).json()["items"]
    assert catalog[0]["is_banned"] is True
    assert (await carol.get("/api/rooms/mine")).json()["public"] == []
    assert await roles(alice, rid) == {"alice": "owner", "bob": "admin"}
    assert (await alice.get(f"/api/rooms/{rid}")).json()["member_count"] == 2
    left = await db.scalar(text("SELECT count(*) FROM chat_read_states WHERE user_id = 3"))
    assert left == 0
    # ... and cannot be invited back until unbanned
    resp = await alice.post(f"/api/rooms/{rid}/invitations", json={"username": "carol"})
    assert resp.status_code == 409 and error_code(resp) == "user_is_banned_in_room"


async def test_ban_errors(make_client: ClientFactory) -> None:
    rid, (alice, bob, carol, dave) = await room_with(make_client, "bob", "carol", "dave")
    await alice.post(f"/api/rooms/{rid}/admins/2")

    resp = await bob.post(f"/api/rooms/{rid}/bans", json={"user_id": 2})
    assert resp.status_code == 400 and error_code(resp) == "cannot_ban_self"
    resp = await bob.post(f"/api/rooms/{rid}/bans", json={"user_id": 1})
    assert resp.status_code == 403 and error_code(resp) == "cannot_modify_owner"
    resp = await bob.delete(f"/api/rooms/{rid}/members/1")
    assert resp.status_code == 403 and error_code(resp) == "cannot_modify_owner"
    resp = await carol.post(f"/api/rooms/{rid}/bans", json={"user_id": 4})
    assert resp.status_code == 403 and error_code(resp) == "not_room_admin"
    resp = await carol.delete(f"/api/rooms/{rid}/members/4")
    assert resp.status_code == 403 and error_code(resp) == "not_room_admin"
    resp = await bob.post(f"/api/rooms/{rid}/bans", json={"user_id": 99})
    assert resp.status_code == 404 and error_code(resp) == "user_not_found"
    for body in ({"user_id": 4, "reason": "r" * 501}, {"user_id": "x"}, {}, {"user_id": 4, "extra": 1}):
        resp = await bob.post(f"/api/rooms/{rid}/bans", json=body)
        assert resp.status_code == 400 and error_code(resp) == "validation_error"

    assert (await bob.post(f"/api/rooms/{rid}/bans", json={"user_id": 4})).status_code == 201
    resp = await bob.post(f"/api/rooms/{rid}/bans", json={"user_id": 4})
    assert resp.status_code == 409 and error_code(resp) == "already_banned"
    # an admin may ban another admin (not the owner)
    await alice.post(f"/api/rooms/{rid}/admins/3")
    assert (await carol.post(f"/api/rooms/{rid}/bans", json={"user_id": 2})).status_code == 201
    assert await roles(alice, rid) == {"alice": "owner", "carol": "admin"}


async def test_owner_ban_self_is_refused(make_client: ClientFactory) -> None:
    rid, (alice,) = await room_with(make_client)
    resp = await alice.post(f"/api/rooms/{rid}/bans", json={"user_id": 1})
    assert resp.status_code == 400 and error_code(resp) == "cannot_ban_self"


async def test_remove_member_is_a_ban(make_client: ClientFactory, events: Events) -> None:
    rid, (alice, bob) = await room_with(make_client, "bob")
    await user(make_client, "carol")
    events.clear()
    assert (await alice.delete(f"/api/rooms/{rid}/members/2")).status_code == 204
    assert [e["type"] for _, e in events] == ["room_access_revoked", "member_removed"]
    bans = (await alice.get(f"/api/rooms/{rid}/bans")).json()["items"]
    assert [(b["user"]["username"], b["banned_by"]["username"], b["reason"]) for b in bans] == [
        ("bob", "alice", None)
    ]
    resp = await bob.post(f"/api/rooms/{rid}/join")
    assert resp.status_code == 403 and error_code(resp) == "room_banned"
    resp = await alice.delete(f"/api/rooms/{rid}/members/3")  # not a member: nothing to remove
    assert resp.status_code == 404 and error_code(resp) == "not_found"
    assert len((await alice.get(f"/api/rooms/{rid}/bans")).json()["items"]) == 1


async def test_preventive_ban_and_invitations(
    make_client: ClientFactory, events: Events, db: AsyncSession
) -> None:
    rid, (alice,) = await room_with(make_client, visibility="private")
    bob = await user(make_client, "bob")
    carol = await user(make_client, "carol")
    inv = (await alice.post(f"/api/rooms/{rid}/invitations", json={"username": "bob"})).json()["invitation"]

    events.clear()
    assert (await alice.post(f"/api/rooms/{rid}/bans", json={"user_id": 2})).status_code == 201
    assert events == []  # bob was not a member: nobody to tell
    status = await db.scalar(text("SELECT status FROM room_invitations WHERE id = :i"), {"i": inv["id"]})
    assert status == "revoked"
    resp = await bob.post(f"/api/invitations/{inv['id']}/accept")
    assert resp.status_code == 409 and error_code(resp) == "invitation_not_pending"
    assert (await bob.get("/api/invitations")).json()["items"] == []

    # a non-member never learns about a private room
    resp = await carol.post(f"/api/rooms/{rid}/bans", json={"user_id": 2})
    assert resp.status_code == 404 and error_code(resp) == "room_not_found"
    resp = await carol.get(f"/api/rooms/{rid}/bans")
    assert resp.status_code == 404
    resp = await carol.post(f"/api/rooms/{rid}/admins/1")
    assert resp.status_code == 404


async def test_ban_list_and_unban(make_client: ClientFactory, events: Events) -> None:
    rid, (alice, bob, carol, dave) = await room_with(make_client, "bob", "carol", "dave")
    await alice.post(f"/api/rooms/{rid}/admins/2")
    await bob.post(f"/api/rooms/{rid}/bans", json={"user_id": 3, "reason": "flood"})
    await alice.post(f"/api/rooms/{rid}/bans", json={"user_id": 4})

    resp = await bob.get(f"/api/rooms/{rid}/bans")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert [(b["user"]["username"], b["banned_by"]["username"], b["reason"]) for b in items] == [
        ("dave", "alice", None),
        ("carol", "bob", "flood"),
    ]
    assert all(b["banned_at"] for b in items)

    events.clear()
    assert (await bob.delete(f"/api/rooms/{rid}/bans/3")).status_code == 204
    assert events == []
    resp = await bob.delete(f"/api/rooms/{rid}/bans/3")
    assert resp.status_code == 404 and error_code(resp) == "not_found"
    # unbanning does not re-add: carol has to join (public) again
    assert await roles(alice, rid) == {"alice": "owner", "bob": "admin"}
    assert (await carol.post(f"/api/rooms/{rid}/join")).status_code == 200

    resp = await carol.delete(f"/api/rooms/{rid}/bans/4")
    assert resp.status_code == 403 and error_code(resp) == "not_room_admin"
    resp = await carol.get(f"/api/rooms/{rid}/bans")
    assert resp.status_code == 403 and error_code(resp) == "not_room_admin"


async def test_ban_by_deleted_user_shows_deleted(make_client: ClientFactory, db: AsyncSession) -> None:
    rid, (alice, bob, carol) = await room_with(make_client, "bob", "carol")
    await alice.post(f"/api/rooms/{rid}/admins/2")
    await bob.post(f"/api/rooms/{rid}/bans", json={"user_id": 3})
    await db.execute(text("UPDATE room_bans SET banned_by_id = NULL"))  # what ON DELETE SET NULL leaves
    await db.commit()
    items = (await alice.get(f"/api/rooms/{rid}/bans")).json()["items"]
    assert items[0]["banned_by"] == {
        "id": None,
        "username": "Deleted user",
        "display_name": None,
        "presence": "offline",
    }


async def test_moderation_requires_auth(make_client: ClientFactory) -> None:
    rid, _ = await room_with(make_client)
    anon = await make_client()
    assert (await anon.post(f"/api/rooms/{rid}/bans", json={"user_id": 1})).status_code == 401
    assert (await anon.get(f"/api/rooms/{rid}/bans")).status_code == 401
    assert (await anon.post(f"/api/rooms/{rid}/admins/1")).status_code == 401
