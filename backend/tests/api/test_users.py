import httpx

from tests.conftest import ClientFactory
from tests.helpers import error_code, register


async def test_update_display_name(client: httpx.AsyncClient) -> None:
    await register(client)
    resp = await client.patch("/api/me", json={"display_name": "  Alice A.  "})
    assert resp.status_code == 200 and resp.json()["user"]["display_name"] == "Alice A."
    resp = await client.patch("/api/me", json={"display_name": ""})
    assert resp.json()["user"]["display_name"] is None
    assert (await client.patch("/api/me", json={"display_name": "x" * 65})).status_code == 400


async def test_username_cannot_be_changed(client: httpx.AsyncClient) -> None:
    await register(client)
    resp = await client.patch("/api/me", json={"username": "mallory", "display_name": "M"})
    assert resp.status_code == 400 and error_code(resp) == "username_immutable"
    me = (await client.get("/api/auth/me")).json()["user"]
    assert me["username"] == "alice" and me["display_name"] is None


async def test_email_cannot_be_changed_via_profile(client: httpx.AsyncClient) -> None:
    await register(client)
    resp = await client.patch("/api/me", json={"email": "new@example.com"})
    assert resp.status_code == 400 and error_code(resp) == "validation_error"


async def test_get_user_by_username(make_client: ClientFactory) -> None:
    alice = await make_client()
    await register(alice, "alice")
    await register(await make_client(), "Bob.Smith")
    resp = await alice.get("/api/users/bob.smith")
    assert resp.status_code == 200
    assert resp.json() == {
        "id": 2,
        "username": "Bob.Smith",
        "display_name": None,
        "presence": "offline",
        "friendship": "none",
        "banned_by_me": False,
        "has_banned_me": False,
    }
    missing = await alice.get("/api/users/nobody")
    assert missing.status_code == 404 and error_code(missing) == "user_not_found"


async def test_search_users_by_prefix(make_client: ClientFactory) -> None:
    alice = await make_client()
    await register(alice, "alice")
    for name in ("bob", "Bobby", "bo_b", "carol"):
        await register(await make_client(), name)
    resp = await alice.get("/api/users", params={"search": "BO"})
    assert sorted(u["username"] for u in resp.json()["items"]) == ["Bobby", "bo_b", "bob"]
    # LIKE wildcards in the query are literal
    resp = await alice.get("/api/users", params={"search": "b_"})
    assert [u["username"] for u in resp.json()["items"]] == []
    assert (await alice.get("/api/users", params={"search": "b"})).status_code == 400


async def test_user_endpoints_require_auth(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/users/alice")).status_code == 401
    assert (await client.get("/api/users", params={"search": "al"})).status_code == 401
    assert (await client.patch("/api/me", json={"display_name": "x"})).status_code == 401
