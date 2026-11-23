from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import ClientFactory
from tests.helpers import error_code, login, register

CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)


async def test_list_sessions_shows_browser_ip_and_current(make_client: ClientFactory) -> None:
    desktop = await make_client(CHROME)
    await register(desktop, "alice")
    phone = await make_client(IPHONE)
    await login(phone, "alice@example.com", keep=True)

    resp = await desktop.get("/api/sessions")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    current, other = items
    assert current["is_current"] is True and other["is_current"] is False
    assert current["browser"] == "Chrome 131" and current["os"] == "Windows 10"
    assert current["device"] == "Desktop" and current["user_agent"] == CHROME
    assert current["ip"] == "127.0.0.1"
    assert other["browser"] == "Mobile Safari 17" and other["device"] == "Mobile"
    assert other["persistent"] is True


async def test_sessions_are_private_per_user(make_client: ClientFactory) -> None:
    alice = await make_client()
    await register(alice, "alice")
    bob = await make_client()
    await register(bob, "bob")
    alice_ids = {s["id"] for s in (await alice.get("/api/sessions")).json()["items"]}
    bob_items = (await bob.get("/api/sessions")).json()["items"]
    assert len(bob_items) == 1 and bob_items[0]["id"] not in alice_ids

    resp = await bob.delete(f"/api/sessions/{next(iter(alice_ids))}")
    assert resp.status_code == 404 and error_code(resp) == "not_found"
    assert (await alice.get("/api/auth/me")).status_code == 200


async def test_revoke_other_session(make_client: ClientFactory) -> None:
    a = await make_client()
    await register(a, "alice")
    b = await make_client()
    await login(b, "alice@example.com")
    other_id = next(s["id"] for s in (await a.get("/api/sessions")).json()["items"] if not s["is_current"])

    resp = await a.delete(f"/api/sessions/{other_id}")
    assert resp.status_code == 204 and "set-cookie" not in resp.headers
    assert (await b.get("/api/auth/me")).status_code == 401
    assert (await a.get("/api/auth/me")).status_code == 200


async def test_revoke_current_session_signs_out(make_client: ClientFactory) -> None:
    c = await make_client()
    await register(c, "alice")
    current_id = next(s["id"] for s in (await c.get("/api/sessions")).json()["items"] if s["is_current"])
    resp = await c.delete(f"/api/sessions/{current_id}")
    assert resp.status_code == 204 and "chat_session" in resp.headers["set-cookie"]
    assert (await c.get("/api/auth/me")).status_code == 401


async def test_revoke_all_other_sessions(make_client: ClientFactory) -> None:
    a = await make_client()
    await register(a, "alice")
    others = [await make_client() for _ in range(3)]
    for c in others:
        await login(c, "alice@example.com")

    assert (await a.delete("/api/sessions", params={"scope": "others"})).status_code == 204
    assert (await a.get("/api/auth/me")).status_code == 200
    for c in others:
        assert (await c.get("/api/auth/me")).status_code == 401
    assert len((await a.get("/api/sessions")).json()["items"]) == 1


async def test_revoke_all_requires_scope(make_client: ClientFactory) -> None:
    c = await make_client()
    await register(c, "alice")
    resp = await c.delete("/api/sessions")
    assert resp.status_code == 400 and error_code(resp) == "validation_error"


async def test_activity_updates_last_seen(make_client: ClientFactory, db: AsyncSession) -> None:
    c = await make_client()
    await register(c, "alice")
    await db.execute(
        text(
            "UPDATE user_sessions SET last_seen_at = now() - interval '2 hours', "
            "expires_at = now() + interval '1 hour', ip = '10.0.0.1'"
        )
    )
    await db.commit()
    assert (await c.get("/api/auth/me")).status_code == 200
    row = (
        await db.execute(
            text(
                "SELECT now() - last_seen_at < interval '1 minute', "
                "expires_at > now() + interval '23 hours', host(ip) FROM user_sessions"
            )
        )
    ).one()
    assert row[0] is True and row[1] is True and row[2] == "127.0.0.1"
