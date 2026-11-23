import io
import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.ratelimit import rate_limiter
from app.services import files
from tests.conftest import ClientFactory
from tests.helpers import Events, ban, befriend, create_room, error_code, send, user

MB = 1024 * 1024


def png(width: int = 40, height: int = 30) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 8)).save(buf, "JPEG")
    return buf.getvalue()


async def upload(
    c: httpx.AsyncClient,
    room_id: object,
    data: bytes,
    name: str = "notes.txt",
    ctype: str = "text/plain",
    comment: str | None = None,
    chat: str = "rooms",
) -> httpx.Response:
    form = {"comment": comment} if comment is not None else None
    return await c.post(f"/api/{chat}/{room_id}/attachments", files={"file": (name, data, ctype)}, data=form)


def on_disk(stored_rel: str) -> bool:
    return (get_settings().upload_dir / stored_rel).is_file()


async def stored_path(db: AsyncSession, attachment_id: int) -> str:
    return str(
        await db.scalar(text("SELECT stored_path FROM attachments WHERE id = :i"), {"i": attachment_id})
    )


# --- upload ---------------------------------------------------------------------------------------------


async def test_upload_image_and_file(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")

    resp = await upload(alice, room["id"], png(40, 30), "cat.png", "image/png", comment=" a cat ")
    assert resp.status_code == 201, resp.text
    img = resp.json()
    assert img["is_image"] is True and img["mime_type"] == "image/png"
    assert (img["width"], img["height"], img["comment"], img["original_filename"]) == (
        40,
        30,
        "a cat",
        "cat.png",
    )
    assert img["download_url"] == f"/api/attachments/{img['id']}/download"
    path = await stored_path(db, img["id"])
    assert path.startswith(f"rooms/{room['id']}/") and "cat" not in path and on_disk(path)

    # the type comes from the content, not from the name or the declared MIME
    resp = await upload(alice, room["id"], jpeg(), "photo.txt", "text/plain")
    assert resp.json()["is_image"] is True and resp.json()["mime_type"] == "image/jpeg"
    fake = await upload(alice, room["id"], b"not really a png", "evil.png", "image/png")
    assert fake.json()["is_image"] is False
    pdf = await upload(alice, room["id"], b"%PDF-1.4 ...", "отчёт 📄.pdf", "application/pdf")
    assert pdf.json()["mime_type"] == "application/pdf" and pdf.json()["original_filename"] == "отчёт 📄.pdf"
    odd = await upload(alice, room["id"], b"x", "../../etc/passwd", "text/html; <script>")
    assert (
        odd.json()["original_filename"] == "passwd" and odd.json()["mime_type"] == "application/octet-stream"
    )


async def test_image_limit_is_3mb_by_content(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    big_png = b"\x89PNG\r\n\x1a\n" + os.urandom(3 * MB)  # PNG magic, > 3 MB
    resp = await upload(alice, room["id"], big_png, "big.bin", "application/octet-stream")
    assert resp.status_code == 413 and error_code(resp) == "image_too_large"
    # the same size is fine for a non-image file
    resp = await upload(alice, room["id"], b"x" + os.urandom(3 * MB), "big.bin", "application/octet-stream")
    assert resp.status_code == 201 and resp.json()["is_image"] is False


async def test_file_limit_without_trusting_content_length(make_client: ClientFactory) -> None:
    """A chunked body (no Content-Length) of 25 MB: refused once 20 MB are read, the rest never read,
    nothing left in tmp/."""
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    boundary = "----chat-test-boundary"
    sent = 0
    chunk = b"\0" * (256 * 1024)

    async def body() -> AsyncIterator[bytes]:
        nonlocal sent
        yield (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="huge.bin"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        for _ in range(100):  # 25 MB
            sent += len(chunk)
            yield chunk
        yield f"\r\n--{boundary}--\r\n".encode()

    resp = await alice.post(
        f"/api/rooms/{room['id']}/attachments",
        content=body(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert resp.status_code == 413 and error_code(resp) == "file_too_large"
    assert sent < 22 * MB  # aborted early
    assert list((get_settings().upload_dir / "tmp").glob("*.part")) == []

    # a lying, small Content-Length does not help either: the stream is counted
    resp = await alice.post(
        f"/api/rooms/{room['id']}/attachments",
        files={"file": ("ok.bin", b"x" * 1000, "application/octet-stream")},
    )
    assert resp.status_code == 201
    # an honest, too large Content-Length is refused before reading
    resp = await alice.post(
        f"/api/rooms/{room['id']}/attachments",
        content=b"",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(30 * MB)},
    )
    assert resp.status_code == 413


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"content": b"plain", "headers": {"Content-Type": "text/plain"}}, "validation_error"),
        ({"files": {"other": ("a.txt", b"x", "text/plain")}}, "validation_error"),
        ({"files": {"file": ("empty.txt", b"", "text/plain")}}, "empty_file"),
        (
            {"files": {"file": ("a.txt", b"x", "text/plain")}, "data": {"comment": "c" * 1001}},
            "validation_error",
        ),
    ],
)
async def test_upload_bad_requests(make_client: ClientFactory, kwargs: dict[str, Any], code: str) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    resp = await alice.post(f"/api/rooms/{room['id']}/attachments", **kwargs)
    assert resp.status_code == 400 and error_code(resp) == code, resp.text


async def test_upload_requires_write_access(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    public = await create_room(alice, "general")
    private = await create_room(alice, "vault", "private")
    assert (await upload(bob, public["id"], b"x")).status_code == 403
    assert (await upload(bob, private["id"], b"x")).status_code == 404
    dm_id = await befriend(alice, bob, "bob")
    assert (await upload(bob, dm_id, b"x", chat="dms")).status_code == 201
    await alice.post("/api/user-bans", json={"user_id": 2})
    resp = await upload(bob, dm_id, b"x", chat="dms")
    assert resp.status_code == 403 and error_code(resp) == "dm_frozen"


# --- download -------------------------------------------------------------------------------------------


async def test_download_headers_names_and_range(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    data = b"0123456789" * 10
    att = (await upload(alice, room["id"], data, 'отчёт 📄 "q".txt', "text/plain")).json()
    await send(alice, room["id"], "", attachment_ids=[att["id"]])

    resp = await alice.get(att["download_url"])
    assert resp.status_code == 200 and resp.content == data
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["content-security-policy"] == "sandbox"
    assert resp.headers["cache-control"] == "private, max-age=0, must-revalidate"
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="')
    assert "filename*=UTF-8''%D0%BE%D1%82%D1%87%D1%91%D1%82%20%F0%9F%93%84%20%22q%22.txt" in disposition

    part = await alice.get(att["download_url"], headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == b"0123456789"

    img = (await upload(alice, room["id"], png(), "cat.png", "image/png")).json()
    await send(alice, room["id"], "", attachment_ids=[img["id"]])
    inline = await alice.get(img["download_url"], params={"inline": 1})
    assert inline.headers["content-type"] == "image/png"
    assert inline.headers["content-disposition"].startswith("inline;")
    # inline is only for images
    assert (
        (await alice.get(att["download_url"], params={"inline": 1}))
        .headers["content-disposition"]
        .startswith("attachment;")
    )


async def test_download_checks_current_access(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    carol = await user(make_client, "carol")
    room = await create_room(alice, "general")
    for c in (bob, carol):
        await c.post(f"/api/rooms/{room['id']}/join")
    att = (await upload(bob, room["id"], b"bob's file", "b.txt")).json()
    url = att["download_url"]

    # pending: only the uploader
    assert (await bob.get(url)).status_code == 200
    assert (await alice.get(url)).status_code == 404
    await send(bob, room["id"], "here", attachment_ids=[att["id"]])
    assert (await alice.get(url)).status_code == 200

    # leaving: no more access, but the file stays in the room for the others
    await bob.post(f"/api/rooms/{room['id']}/leave")
    resp = await bob.get(url)
    assert resp.status_code == 404 and error_code(resp) == "attachment_not_found"
    assert (await alice.get(url)).status_code == 200
    # banned member loses access at once
    await ban(db, room["id"], 3, by=1)
    assert (await carol.get(url)).status_code == 404
    assert (await alice.get("/api/attachments/999/download")).status_code == 404
    assert (await (await make_client()).get(url)).status_code == 401


async def test_dm_attachments(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    carol = await user(make_client, "carol")
    dm_id = await befriend(alice, bob, "bob")
    att = (await upload(alice, dm_id, png(), "pic.png", "image/png", chat="dms")).json()
    msg = await alice.post(f"/api/dms/{dm_id}/messages", json={"content": "", "attachment_ids": [att["id"]]})
    assert msg.status_code == 201 and msg.json()["attachments"][0]["id"] == att["id"]
    assert (await bob.get(att["download_url"])).status_code == 200
    assert (await carol.get(att["download_url"])).status_code == 404
    await bob.delete("/api/friends/1")  # frozen DM: still readable
    assert (await bob.get(att["download_url"])).status_code == 200


# --- pending: comment / cancel ----------------------------------------------------------------------------


async def test_comment_and_cancel_pending(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    await bob.post(f"/api/rooms/{room['id']}/join")
    att = (await upload(alice, room["id"], b"x")).json()

    resp = await alice.patch(f"/api/attachments/{att['id']}", json={"comment": "  see this "})
    assert resp.status_code == 200 and resp.json()["comment"] == "see this"
    assert (await bob.patch(f"/api/attachments/{att['id']}", json={"comment": "x"})).status_code == 404
    assert (
        await alice.patch(f"/api/attachments/{att['id']}", json={"comment": "c" * 1001})
    ).status_code == 400

    other = (await upload(alice, room["id"], b"y")).json()
    path = await stored_path(db, other["id"])
    assert (await alice.delete(f"/api/attachments/{other['id']}")).status_code == 204
    assert not on_disk(path)
    assert (await alice.delete(f"/api/attachments/{other['id']}")).status_code == 404

    await send(alice, room["id"], "sent", attachment_ids=[att["id"]])
    for resp in (
        await alice.patch(f"/api/attachments/{att['id']}", json={"comment": "late"}),
        await alice.delete(f"/api/attachments/{att['id']}"),
    ):
        assert resp.status_code == 409 and error_code(resp) == "attachment_already_sent"
    resp = await bob.patch(f"/api/attachments/{att['id']}", json={"comment": "x"})
    assert resp.status_code == 403  # visible to bob now, but not his


# --- sending with attachments -----------------------------------------------------------------------------


async def test_send_with_attachments(make_client: ClientFactory, events: Events) -> None:
    alice = await user(make_client, "alice")
    bob = await user(make_client, "bob")
    room = await create_room(alice, "general")
    other_room = await create_room(alice, "other")
    await bob.post(f"/api/rooms/{room['id']}/join")
    a1 = (await upload(alice, room["id"], png(), "1.png", "image/png", comment="first")).json()
    a2 = (await upload(alice, room["id"], b"doc", "2.txt")).json()

    resp = await send(alice, room["id"], "", attachment_ids=[a2["id"], a1["id"]])  # text optional with files
    assert resp.status_code == 201
    msg = resp.json()
    assert [a["id"] for a in msg["attachments"]] == [a1["id"], a2["id"]]
    assert msg["attachments"][0]["comment"] == "first"
    page = (await bob.get(f"/api/rooms/{room['id']}/messages")).json()["items"]
    assert [a["original_filename"] for a in page[-1]["attachments"]] == ["1.png", "2.txt"]
    # the edit may leave only the files
    assert (await alice.patch(f"/api/messages/{msg['id']}", json={"content": ""})).status_code == 200

    bobs = (await upload(bob, room["id"], b"b")).json()
    elsewhere = (await upload(alice, other_room["id"], b"o")).json()
    for ids in ([a1["id"]], [bobs["id"]], [elsewhere["id"]], [999]):
        resp = await send(alice, room["id"], "x", attachment_ids=ids)
        assert resp.status_code == 400 and error_code(resp) == "invalid_attachment", ids

    many = [(await upload(alice, room["id"], b"m", f"{i}.txt")).json()["id"] for i in range(11)]
    rate_limiter.reset()  # 5 messages/s per user
    resp = await send(alice, room["id"], "x", attachment_ids=many)
    assert resp.status_code == 413 and error_code(resp) == "too_many_attachments"
    assert (await send(alice, room["id"], "ten", attachment_ids=many[:10])).status_code == 201


async def test_deleting_message_removes_files(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    att = (await upload(alice, room["id"], b"secret", "s.txt")).json()
    path = await stored_path(db, att["id"])
    msg = (await send(alice, room["id"], "with file", attachment_ids=[att["id"]])).json()

    assert (await alice.delete(f"/api/messages/{msg['id']}")).status_code == 204
    assert not on_disk(path)
    assert await db.scalar(text("SELECT count(*) FROM attachments")) == 0
    assert (await alice.get(att["download_url"])).status_code == 404
    page = (await alice.get(f"/api/rooms/{room['id']}/messages")).json()["items"]
    assert page[0]["deleted"] is True and page[0]["attachments"] == []


async def test_deleting_room_removes_its_directory(make_client: ClientFactory) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    keep = await create_room(alice, "keep")
    att = (await upload(alice, room["id"], b"x")).json()
    await send(alice, room["id"], "f", attachment_ids=[att["id"]])
    kept = (await upload(alice, keep["id"], b"y")).json()
    assert files.room_dir(room["id"]).is_dir()

    assert (await alice.delete(f"/api/rooms/{room['id']}")).status_code == 204
    assert not files.room_dir(room["id"]).exists()
    assert files.room_dir(keep["id"]).is_dir()
    assert (await alice.get(kept["download_url"])).status_code == 200


# --- garbage collection -----------------------------------------------------------------------------------


async def test_garbage_collection(make_client: ClientFactory, db: AsyncSession) -> None:
    alice = await user(make_client, "alice")
    room = await create_room(alice, "general")
    old_pending = (await upload(alice, room["id"], b"old")).json()
    fresh_pending = (await upload(alice, room["id"], b"fresh")).json()
    sent = (await upload(alice, room["id"], b"sent")).json()
    await send(alice, room["id"], "s", attachment_ids=[sent["id"]])
    await db.execute(
        text("UPDATE attachments SET created_at = :t WHERE id = :i"),
        {"t": datetime.now(UTC) - timedelta(hours=25), "i": old_pending["id"]},
    )
    await db.execute(
        text("UPDATE attachments SET created_at = :t WHERE id = :i"),
        {"t": datetime.now(UTC) - timedelta(days=3), "i": sent["id"]},
    )
    await db.commit()
    old_path = await stored_path(db, old_pending["id"])

    base = get_settings().upload_dir
    hour_ago = time.time() - 2 * 3600
    orphan = files.room_dir(room["id"]) / "2020" / "01" / "orphan"
    fresh_orphan = files.room_dir(room["id"]) / "2020" / "01" / "fresh-orphan"
    old_part = base / "tmp" / "old.part"
    new_part = base / "tmp" / "new.part"
    gone_room = base / "rooms" / "424242" / "2020" / "01" / "x"
    for p in (orphan, fresh_orphan, old_part, new_part, gone_room):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"junk")
    for p in (orphan, old_part):
        os.utime(p, (hour_ago, hour_ago))

    counts = await files.collect_garbage(db)
    assert counts == {"pending": 1, "orphans": 1, "parts": 1, "chat_dirs": 1}
    assert not on_disk(old_path)
    assert (await alice.get(old_pending["download_url"])).status_code == 404
    for kept in (fresh_pending, sent):
        assert (await alice.get(kept["download_url"])).status_code == 200
    assert not orphan.exists() and fresh_orphan.exists()  # just written: maybe mid-commit
    assert not old_part.exists() and new_part.exists()
    assert not (base / "rooms" / "424242").exists()
    new_part.unlink()
