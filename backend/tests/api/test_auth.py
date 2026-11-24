import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import ClientFactory
from tests.helpers import PASSWORD, error_code, login, register


async def test_register_logs_in_with_browser_session_cookie(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/register",
        json={"email": "alice@example.com", "username": "alice", "password": PASSWORD},
    )
    assert resp.status_code == 201
    user = resp.json()["user"]
    assert user["username"] == "alice" and user["email"] == "alice@example.com"
    cookie = resp.headers["set-cookie"]
    assert "chat_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    assert "Max-Age" not in cookie  # registration does not "keep me signed in"

    me = await client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["user"]["id"] == user["id"]


async def test_password_is_stored_as_argon2id(client: httpx.AsyncClient, db: AsyncSession) -> None:
    await register(client)
    stored = (await db.execute(text("SELECT password_hash FROM users"))).scalar_one()
    assert stored.startswith("$argon2id$") and PASSWORD not in stored


@pytest.mark.parametrize(
    ("email", "username"),
    [("ALICE@example.com", "other"), ("other@example.com", "ALICE")],
)
async def test_email_and_username_unique_case_insensitive(
    make_client: ClientFactory, email: str, username: str
) -> None:
    await register(await make_client(), "alice")
    resp = await (await make_client()).post(
        "/api/auth/register", json={"email": email, "username": username, "password": PASSWORD}
    )
    assert resp.status_code == 409
    assert error_code(resp) == ("email_taken" if username == "other" else "username_taken")


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "username": "alice", "password": PASSWORD},
        {"email": "a@example.com", "username": "al", "password": PASSWORD},
        {"email": "a@example.com", "username": "bad name", "password": PASSWORD},
        {"email": "a@example.com", "username": "x" * 33, "password": PASSWORD},
        {"email": "a@example.com", "username": "alice", "password": "short"},
        {"email": "a@example.com", "username": "alice"},
    ],
)
async def test_register_validation(client: httpx.AsyncClient, payload: dict[str, str]) -> None:
    resp = await client.post("/api/auth/register", json=payload)
    assert resp.status_code == 400
    assert error_code(resp) == "validation_error"


async def test_login_errors_do_not_reveal_account_existence(make_client: ClientFactory) -> None:
    await register(await make_client(), "alice")
    c = await make_client()
    wrong_password = await login(c, "alice@example.com", "wrong-password")
    unknown_email = await login(c, "nobody@example.com", "wrong-password")
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert error_code(wrong_password) == "invalid_credentials"


async def test_login_email_case_insensitive(make_client: ClientFactory) -> None:
    await register(await make_client(), "alice")
    resp = await login(await make_client(), "Alice@Example.com")
    assert resp.status_code == 200


async def test_keep_me_signed_in_sets_persistent_cookie(make_client: ClientFactory, db: AsyncSession) -> None:
    await register(await make_client(), "alice")
    persistent = await login(await make_client(), "alice@example.com", keep=True)
    browser_only = await login(await make_client(), "alice@example.com", keep=False)
    assert "Max-Age=2592000" in persistent.headers["set-cookie"]
    assert "Max-Age" not in browser_only.headers["set-cookie"]
    flags = (await db.execute(text("SELECT persistent FROM user_sessions ORDER BY id"))).scalars().all()
    assert flags == [False, True, False]


async def test_me_requires_authentication(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401 and error_code(resp) == "not_authenticated"
    client.cookies.set("chat_session", "garbage")
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_expired_session_is_rejected(client: httpx.AsyncClient, db: AsyncSession) -> None:
    await register(client)
    await db.execute(text("UPDATE user_sessions SET expires_at = now() - interval '1 second'"))
    await db.commit()
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_logout_ends_only_current_session(make_client: ClientFactory) -> None:
    browser_a = await make_client()
    await register(browser_a, "alice")
    browser_b = await make_client()
    assert (await login(browser_b, "alice@example.com")).status_code == 200

    resp = await browser_a.post("/api/auth/logout")
    assert resp.status_code == 204
    assert 'chat_session=""' in resp.headers["set-cookie"] or "Max-Age=0" in resp.headers["set-cookie"]

    assert (await browser_a.get("/api/auth/me")).status_code == 401
    assert (await browser_b.get("/api/auth/me")).status_code == 200


async def test_logout_requires_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/auth/logout")).status_code == 401


async def test_login_rate_limited(make_client: ClientFactory) -> None:
    await register(await make_client(), "alice")
    c = await make_client()
    for _ in range(10):
        assert (await login(c, "alice@example.com", "wrong-password")).status_code == 401
    resp = await login(c, "alice@example.com", "wrong-password")
    assert resp.status_code == 429
    assert error_code(resp) == "rate_limited" and int(resp.headers["Retry-After"]) >= 1


async def test_username_immutable_in_database(client: httpx.AsyncClient, db: AsyncSession) -> None:
    await register(client)
    with pytest.raises(DBAPIError, match="username is immutable"):
        await db.execute(text("UPDATE users SET username = 'mallory'"))
    await db.rollback()
    with pytest.raises(DBAPIError, match="username is immutable"):  # citext: a case change too
        await db.execute(text("UPDATE users SET username = 'ALICE'"))
    await db.rollback()
