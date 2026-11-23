import statistics
import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Message
from app.db.session import engine
from app.services.access import ChatRef
from app.services.messages import page_query
from tests.conftest import ClientFactory
from tests.helpers import create_room, error_code, make_admin, send, user


async def bulk_messages(
    db: AsyncSession, room_id: int, author_id: int, count: int, prefix: str = "msg"
) -> None:
    await db.execute(
        text(
            "INSERT INTO messages (room_id, author_id, content, created_at) "
            "SELECT :r, :a, :p || ' ' || g, now() FROM generate_series(1, :n) g"
        ),
        {"r": room_id, "a": author_id, "p": prefix, "n": count},
    )
    await db.commit()


# --- send --------------------------------------------------------------------------------------------


async def test_send_message(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    await bob.post(f"/api/rooms/{room['id']}/join")

    resp = await send(alice, room["id"], "Hello\r\nworld 👋  ")
    assert resp.status_code == 201
    msg = resp.json()
    assert msg["content"] == "Hello\nworld 👋  "  # CRLF normalised, whitespace kept
    assert msg["chat"] == {"type": "room", "id": room["id"]}
    assert msg["author"]["username"] == "alice"
    assert (msg["deleted"], msg["edited_at"], msg["reply_to"], msg["attachments"]) == (False, None, None, [])

    page = (await bob.get(f"/api/rooms/{room['id']}/messages")).json()
    assert [m["id"] for m in page["items"]] == [msg["id"]]


async def test_message_byte_limit(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    assert (await send(alice, room["id"], "😀" * 768)).status_code == 201  # 4 bytes each = 3072
    resp = await send(alice, room["id"], "😀" * 768 + "x")
    assert resp.status_code == 413 and error_code(resp) == "message_too_large"
    resp = await send(alice, room["id"], "a" * 3073)
    assert resp.status_code == 413


@pytest.mark.parametrize("content", ["", "   ", "\n\r\n\t"])
async def test_empty_message(make_client: ClientFactory, content: str) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    resp = await send(alice, room["id"], content)
    assert resp.status_code == 400 and error_code(resp) == "empty_message"


async def test_send_requires_membership(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    public = await create_room(alice, "general")
    private = await create_room(alice, "vault", "private")
    resp = await send(bob, public["id"], "hi")
    assert resp.status_code == 403 and error_code(resp) == "forbidden"
    resp = await send(bob, private["id"], "hi")
    assert resp.status_code == 404
    assert (await bob.get(f"/api/rooms/{public['id']}/messages")).status_code == 403
    assert (await bob.get(f"/api/rooms/{private['id']}/messages")).status_code == 404
    assert (await bob.get("/api/dms/1/messages")).status_code == 404  # DMs: stage 6
    assert (await bob.get("/api/teams/1/messages")).status_code in (400, 404)


async def test_left_member_loses_access(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    await bob.post(f"/api/rooms/{room['id']}/join")
    mine = (await send(bob, room["id"], "bye")).json()
    await bob.post(f"/api/rooms/{room['id']}/leave")
    assert (await send(bob, room["id"], "again")).status_code == 403
    assert (await bob.get(f"/api/rooms/{room['id']}/messages")).status_code == 403
    assert (await bob.patch(f"/api/messages/{mine['id']}", json={"content": "x"})).status_code == 403
    assert (await bob.delete(f"/api/messages/{mine['id']}")).status_code == 403
    # his message stays in the room
    assert [m["content"] for m in (await alice.get(f"/api/rooms/{room['id']}/messages")).json()["items"]] == [
        "bye"
    ]


async def test_reply(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    other = await create_room(alice, "other")
    original = (await send(alice, room["id"], "x" * 300)).json()
    foreign = (await send(alice, other["id"], "elsewhere")).json()

    resp = await send(alice, room["id"], "answer", reply_to_message_id=original["id"])
    assert resp.status_code == 201
    reply = resp.json()["reply_to"]
    assert reply["id"] == original["id"] and reply["author"]["username"] == "alice"
    assert reply["excerpt"] == "x" * 200 and reply["deleted"] is False

    for target in (foreign["id"], 999_999):
        resp = await send(alice, room["id"], "bad", reply_to_message_id=target)
        assert resp.status_code == 400 and error_code(resp) == "invalid_reply_target"

    await alice.delete(f"/api/messages/{original['id']}")
    items = (await alice.get(f"/api/rooms/{room['id']}/messages")).json()["items"]
    assert items[-1]["reply_to"] == {
        "id": original["id"],
        "author": reply["author"],
        "excerpt": "",
        "deleted": True,
    }


async def test_client_msg_id_is_idempotent(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    cid = str(uuid.uuid4())
    first = await send(alice, room["id"], "once", client_msg_id=cid)
    second = await send(alice, room["id"], "once", client_msg_id=cid)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] == second.json()["id"] and first.json()["client_msg_id"] == cid
    assert len((await alice.get(f"/api/rooms/{room['id']}/messages")).json()["items"]) == 1


async def test_send_rate_limit(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    codes = [(await send(alice, room["id"], f"m{i}")).status_code for i in range(6)]
    assert sorted(codes) == [201] * 5 + [429]


async def test_send_validation(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    for body in (
        {"content": "x", "client_msg_id": "nope"},
        {"content": "x", "attachment_ids": ["a"]},
        {"content": 5},
    ):
        resp = await alice.post(f"/api/rooms/{room['id']}/messages", json=body)
        assert resp.status_code == 400 and error_code(resp) == "validation_error", body


# --- history -----------------------------------------------------------------------------------------


async def test_cursor_pagination(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    await bulk_messages(db, room["id"], 1, 120)
    url = f"/api/rooms/{room['id']}/messages"

    page = (await alice.get(url)).json()
    ids = [m["id"] for m in page["items"]]
    assert ids == list(range(71, 121)) and page["has_more"] and page["next_cursor"] == 71

    page = (await alice.get(url, params={"before_id": 71})).json()
    assert [m["id"] for m in page["items"]] == list(range(21, 71)) and page["next_cursor"] == 21
    page = (await alice.get(url, params={"before_id": 21})).json()
    assert [m["id"] for m in page["items"]] == list(range(1, 21))
    assert page["has_more"] is False and page["next_cursor"] is None

    page = (await alice.get(url, params={"after_id": 100, "limit": 10})).json()
    assert [m["id"] for m in page["items"]] == list(range(101, 111)) and page["next_cursor"] == 110
    page = (await alice.get(url, params={"after_id": 110, "limit": 10})).json()
    assert [m["id"] for m in page["items"]] == list(range(111, 121)) and not page["has_more"]

    resp = await alice.get(url, params={"before_id": 50, "after_id": 10})
    assert resp.status_code == 400 and error_code(resp) == "validation_error"
    assert (await alice.get(url, params={"limit": 101})).status_code == 400


async def test_messages_around(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    other = await create_room(alice, "other")
    await bulk_messages(db, room["id"], 1, 100)
    await bulk_messages(db, other["id"], 1, 1)  # id 101
    url = f"/api/rooms/{room['id']}/messages/around"

    resp = (await alice.get(f"{url}/50", params={"limit": 10})).json()
    assert [m["id"] for m in resp["items"]] == list(range(45, 55))
    assert resp["has_more_before"] and resp["has_more_after"]
    edge = (await alice.get(f"{url}/2", params={"limit": 10})).json()
    assert [m["id"] for m in edge["items"]] == list(range(1, 7)) and not edge["has_more_before"]
    resp = await alice.get(f"{url}/101")
    assert resp.status_code == 404 and error_code(resp) == "message_not_found"


async def test_search(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    for text_ in ("Deploy on Friday", "lunch?", "deploy failed", "100% done"):
        await send(alice, room["id"], text_)
    gone = (await send(alice, room["id"], "deploy secret")).json()
    await alice.delete(f"/api/messages/{gone['id']}")
    url = f"/api/rooms/{room['id']}/messages/search"

    page = (await alice.get(url, params={"q": "DEPLOY"})).json()
    assert [m["content"] for m in page["items"]] == ["deploy failed", "Deploy on Friday"]
    page = (await alice.get(url, params={"q": "deploy", "limit": 1})).json()
    assert page["has_more"] and page["next_cursor"] == 3
    page = (await alice.get(url, params={"q": "deploy", "before_id": 3})).json()
    assert [m["content"] for m in page["items"]] == ["Deploy on Friday"]
    assert [m["content"] for m in (await alice.get(url, params={"q": "0%"})).json()["items"]] == ["100% done"]
    resp = await alice.get(url, params={"q": " d "})
    assert resp.status_code == 400 and error_code(resp) == "validation_error"


async def test_history_page_is_fast_and_uses_index(make_client: ClientFactory, db: AsyncSession) -> None:
    """DoD stage 4: a room with 10 000 messages, a page < 50 ms, the plan uses ix_messages_room_id.

    Data: 20 equally busy rooms (200 000 messages, interleaved like real traffic)."""
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    await db.execute(
        text(
            "INSERT INTO rooms (name, visibility, owner_id) "
            "SELECT 'noise-' || g, 'public', 1 FROM generate_series(1, 19) g"
        )
    )
    await db.execute(
        text(
            "INSERT INTO room_members (room_id, user_id, is_admin) "
            "SELECT id, 1, true FROM rooms WHERE id <> :r"
        ),
        {"r": room["id"]},
    )
    await db.execute(
        text(
            "INSERT INTO messages (room_id, author_id, content, created_at) "
            "SELECT CAST(:r AS bigint) + g % 20, 1, 'msg ' || g, now() FROM generate_series(1, 200000) g"
        ),
        {"r": room["id"]},
    )
    await db.commit()
    async with engine.connect() as conn:  # what autovacuum does in production: stats + visibility map
        await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM ANALYZE messages"))
    assert (
        await db.scalar(text("SELECT count(*) FROM messages WHERE room_id = :r"), {"r": room["id"]}) == 10_000
    )
    url = f"/api/rooms/{room['id']}/messages"
    await alice.get(url)  # warm up

    timings = []
    for before_id in (200_000, 150_000, 100_000, 50_000, 2_000):
        start = time.perf_counter()
        resp = await alice.get(url, params={"before_id": before_id})
        timings.append(time.perf_counter() - start)
        assert resp.status_code == 200 and len(resp.json()["items"]) == 50
        assert all(m["chat"]["id"] == room["id"] for m in resp.json()["items"])
    assert statistics.median(timings) < 0.050, timings

    query = page_query(ChatRef("room", room["id"]), Message.id < 100_000, newest_first=True, limit=51)
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    plan = "\n".join((await db.execute(text(f"EXPLAIN {sql}"))).scalars())
    assert "ix_messages_room_id" in plan, plan


# --- edit / delete -----------------------------------------------------------------------------------


async def test_edit_message(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    await bob.post(f"/api/rooms/{room['id']}/join")
    msg = (await send(alice, room["id"], "helo")).json()

    resp = await alice.patch(f"/api/messages/{msg['id']}", json={"content": "hello"})
    assert resp.status_code == 200
    assert resp.json()["content"] == "hello" and resp.json()["edited_at"] is not None

    resp = await bob.patch(f"/api/messages/{msg['id']}", json={"content": "hacked"})
    assert resp.status_code == 403 and error_code(resp) == "not_message_author"
    resp = await alice.patch(f"/api/messages/{msg['id']}", json={"content": "x" * 3073})
    assert resp.status_code == 413 and error_code(resp) == "message_too_large"
    resp = await alice.patch(f"/api/messages/{msg['id']}", json={"content": "  "})
    assert resp.status_code == 400 and error_code(resp) == "empty_message"
    assert (await alice.patch("/api/messages/999", json={"content": "x"})).status_code == 404

    await alice.delete(f"/api/messages/{msg['id']}")
    resp = await alice.patch(f"/api/messages/{msg['id']}", json={"content": "back"})
    assert resp.status_code == 400 and error_code(resp) == "message_deleted"


async def test_delete_message(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    carol = await user(make_client, "carol")
    room = await create_room(alice, "general")
    rid = room["id"]
    for c in (bob, carol):
        await c.post(f"/api/rooms/{rid}/join")
    by_carol = (await send(carol, rid, "carol's")).json()
    by_bob = (await send(bob, rid, "bob's")).json()

    resp = await bob.delete(f"/api/messages/{by_carol['id']}")
    assert resp.status_code == 403 and error_code(resp) == "forbidden"
    assert (await bob.delete(f"/api/messages/{by_bob['id']}")).status_code == 204
    assert (await bob.delete(f"/api/messages/{by_bob['id']}")).status_code == 204  # idempotent
    await make_admin(db, rid, 2)
    assert (await bob.delete(f"/api/messages/{by_carol['id']}")).status_code == 204  # admin deletes any

    items = (await alice.get(f"/api/rooms/{rid}/messages")).json()["items"]
    assert [(m["deleted"], m["content"]) for m in items] == [(True, ""), (True, "")]
    row = (
        await db.execute(
            text("SELECT content, deleted_by_id FROM messages WHERE id = :i"), {"i": by_carol["id"]}
        )
    ).one()
    assert tuple(row) == ("", 2)
    assert (await alice.delete("/api/messages/999")).status_code == 404


async def test_join_marks_existing_history_read(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    await bulk_messages(db, room["id"], 1, 7)
    await bob.post(f"/api/rooms/{room['id']}/join")
    last_read = await db.scalar(
        text("SELECT last_read_message_id FROM chat_read_states WHERE user_id = 2 AND room_id = :r"),
        {"r": room["id"]},
    )
    assert last_read == 7


async def test_message_endpoints_require_auth(make_client: ClientFactory) -> None:
    anon = await make_client()
    assert (await anon.get("/api/rooms/1/messages")).status_code == 401
    assert (await anon.post("/api/rooms/1/messages", json={"content": "x"})).status_code == 401
    assert (await anon.patch("/api/messages/1", json={"content": "x"})).status_code == 401
    assert (await anon.delete("/api/messages/1")).status_code == 401
