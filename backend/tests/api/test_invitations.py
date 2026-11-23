from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import ClientFactory
from tests.helpers import Events, ban, create_room, error_code, make_admin, user


async def test_invite_accept_private_room(
    make_client: ClientFactory, db: AsyncSession, events: Events
) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "vault", "private", description="secrets")
    rid = room["id"]
    events.clear()

    resp = await alice.post(f"/api/rooms/{rid}/invitations", json={"username": "BOB"})
    assert resp.status_code == 201
    inv = resp.json()["invitation"]
    assert inv["invitee"]["username"] == "bob" and inv["inviter"]["username"] == "alice"
    assert inv["room"] == {
        "id": rid,
        "name": "vault",
        "description": "secrets",
        "visibility": "private",
        "member_count": 1,
    }
    assert events == [
        (
            [2],
            {
                "type": "invitation_received",
                "invitation": {
                    "id": inv["id"],
                    "room": {"id": rid, "name": "vault"},
                    "inviter": inv["inviter"],
                },
            },
        )
    ]

    mine = (await bob.get("/api/invitations")).json()["items"]
    assert [(i["id"], i["room"]["name"], i["inviter"]["username"]) for i in mine] == [
        (inv["id"], "vault", "alice")
    ]
    # still invisible until accepted
    assert (await bob.get(f"/api/rooms/{rid}")).status_code == 404

    events.clear()
    resp = await bob.post(f"/api/invitations/{inv['id']}/accept")
    assert resp.status_code == 200
    assert resp.json()["my_role"] == "member" and resp.json()["member_count"] == 2
    assert [(u, e["type"]) for u, e in events] == [([2], "room_joined"), ([1], "member_joined")]
    assert (await bob.get(f"/api/rooms/{rid}")).status_code == 200
    assert (await bob.get("/api/invitations")).json()["items"] == []
    assert [r["name"] for r in (await bob.get("/api/rooms/mine")).json()["private"]] == ["vault"]
    status = await db.scalar(text("SELECT status FROM room_invitations WHERE id = :i"), {"i": inv["id"]})
    assert status == "accepted"

    again = await bob.post(f"/api/invitations/{inv['id']}/accept")
    assert again.status_code == 409


async def test_decline_invitation(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "vault", "private")
    inv = (await alice.post(f"/api/rooms/{room['id']}/invitations", json={"username": "bob"})).json()[
        "invitation"
    ]

    assert (await alice.post(f"/api/invitations/{inv['id']}/decline")).status_code == 404  # not the invitee
    assert (await bob.post(f"/api/invitations/{inv['id']}/decline")).status_code == 204
    assert (await bob.get("/api/invitations")).json()["items"] == []
    assert (await bob.post(f"/api/invitations/{inv['id']}/decline")).status_code == 404
    assert (await bob.post(f"/api/invitations/{inv['id']}/accept")).status_code == 409
    # can be invited again after declining
    assert (
        await alice.post(f"/api/rooms/{room['id']}/invitations", json={"username": "bob"})
    ).status_code == 201


async def test_accept_someone_elses_invitation_is_404(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    await user(make_client, "bob")
    carol = await user(make_client, "carol")
    room = await create_room(alice, "vault", "private")
    inv = (await alice.post(f"/api/rooms/{room['id']}/invitations", json={"username": "bob"})).json()[
        "invitation"
    ]
    resp = await carol.post(f"/api/invitations/{inv['id']}/accept")
    assert resp.status_code == 404
    assert (await carol.post("/api/invitations/999/accept")).status_code == 404


async def test_accept_after_ban_is_forbidden(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "vault", "private")
    inv = (await alice.post(f"/api/rooms/{room['id']}/invitations", json={"username": "bob"})).json()[
        "invitation"
    ]
    await ban(db, room["id"], 2, by=1)
    resp = await bob.post(f"/api/invitations/{inv['id']}/accept")
    assert resp.status_code == 403 and error_code(resp) == "room_banned"


async def test_invite_errors(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    await user(make_client, "carol")
    await user(make_client, "dave")
    outsider = await user(make_client, "eve")
    room = await create_room(alice, "general")
    rid = room["id"]
    await bob.post(f"/api/rooms/{rid}/join")

    def invite(username: str, client=alice):  # type: ignore[no-untyped-def]
        return client.post(f"/api/rooms/{rid}/invitations", json={"username": username})

    resp = await invite("nobody")
    assert resp.status_code == 404 and error_code(resp) == "user_not_found"
    resp = await invite("bob")
    assert resp.status_code == 409 and error_code(resp) == "already_member"
    resp = await invite("alice")
    assert resp.status_code == 409 and error_code(resp) == "already_member"
    assert (await invite("carol", bob)).status_code == 201  # any member may invite
    resp = await invite("carol")
    assert resp.status_code == 409 and error_code(resp) == "invitation_exists"
    await ban(db, rid, 4, by=1)
    resp = await invite("dave")
    assert resp.status_code == 409 and error_code(resp) == "user_is_banned_in_room"
    resp = await invite("carol", outsider)
    assert resp.status_code == 403 and error_code(resp) == "forbidden"
    resp = await alice.post(f"/api/rooms/{rid}/invitations", json={})
    assert resp.status_code == 400 and error_code(resp) == "validation_error"


async def test_joining_public_room_settles_pending_invitation(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    await alice.post(f"/api/rooms/{room['id']}/invitations", json={"username": "bob"})
    await bob.post(f"/api/rooms/{room['id']}/join")
    assert (await bob.get("/api/invitations")).json()["items"] == []


async def test_admin_lists_and_revokes_room_invitations(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    carol = await user(make_client, "carol")
    room = await create_room(alice, "vault", "private")
    rid = room["id"]
    inv_bob = (await alice.post(f"/api/rooms/{rid}/invitations", json={"username": "bob"})).json()[
        "invitation"
    ]
    await bob.post(f"/api/invitations/{inv_bob['id']}/accept")
    inv_carol = (await bob.post(f"/api/rooms/{rid}/invitations", json={"username": "carol"})).json()[
        "invitation"
    ]

    # plain member: forbidden
    resp = await bob.get(f"/api/rooms/{rid}/invitations")
    assert resp.status_code == 403 and error_code(resp) == "not_room_admin"
    resp = await bob.delete(f"/api/rooms/{rid}/invitations/{inv_carol['id']}")
    assert resp.status_code == 403 and error_code(resp) == "not_room_admin"

    await make_admin(db, rid, 2)
    items = (await bob.get(f"/api/rooms/{rid}/invitations")).json()["items"]
    assert [(i["invitee"]["username"], i["inviter"]["username"]) for i in items] == [("carol", "bob")]

    assert (await bob.delete(f"/api/rooms/{rid}/invitations/{inv_carol['id']}")).status_code == 204
    assert (await carol.get("/api/invitations")).json()["items"] == []
    assert (await alice.get(f"/api/rooms/{rid}/invitations")).json()["items"] == []
    assert (await alice.delete(f"/api/rooms/{rid}/invitations/{inv_carol['id']}")).status_code == 404
    assert (await carol.post(f"/api/invitations/{inv_carol['id']}/accept")).status_code == 409
