import re

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import mail
from tests.conftest import ClientFactory
from tests.helpers import PASSWORD, error_code, login, register

NEW_PASSWORD = "brand-new-secret-2"


def _token_from_mail() -> str:
    assert len(mail.outbox) == 1
    m = re.search(r"http://testserver/reset-password\?token=([\w-]+)", mail.outbox[0].body)
    assert m, mail.outbox[0].body
    return m.group(1)


# --- change password ------------------------------------------------------------------------------


async def test_change_password_keeps_current_and_ends_other_sessions(make_client: ClientFactory) -> None:
    a = await make_client()
    await register(a, "alice")
    b = await make_client()
    await login(b, "alice@example.com")

    resp = await a.post(
        "/api/auth/password/change", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert resp.status_code == 204
    assert (await a.get("/api/auth/me")).status_code == 200
    assert (await b.get("/api/auth/me")).status_code == 401

    c = await make_client()
    assert (await login(c, "alice@example.com", PASSWORD)).status_code == 401
    assert (await login(c, "alice@example.com", NEW_PASSWORD)).status_code == 200


async def test_change_password_wrong_current(client: httpx.AsyncClient) -> None:
    await register(client)
    resp = await client.post(
        "/api/auth/password/change", json={"current_password": "nope-nope", "new_password": NEW_PASSWORD}
    )
    assert resp.status_code == 401 and error_code(resp) == "invalid_current_password"
    # the session itself is still valid
    assert (await client.get("/api/auth/me")).status_code == 200


async def test_change_password_validates_new(client: httpx.AsyncClient) -> None:
    await register(client)
    resp = await client.post(
        "/api/auth/password/change", json={"current_password": PASSWORD, "new_password": "short"}
    )
    assert resp.status_code == 400


async def test_change_password_requires_auth(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/auth/password/change", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert resp.status_code == 401


# --- forgot / reset -------------------------------------------------------------------------------


async def test_forgot_password_unknown_email_is_indistinguishable(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/auth/password/forgot", json={"email": "nobody@example.com"})
    assert resp.status_code == 202 and resp.content == b""
    assert mail.outbox == []


async def test_full_reset_flow_ends_all_sessions(make_client: ClientFactory) -> None:
    a = await make_client()
    await register(a, "alice")
    b = await make_client()
    await login(b, "alice@example.com", keep=True)

    resp = await (await make_client()).post("/api/auth/password/forgot", json={"email": "ALICE@example.com"})
    assert resp.status_code == 202
    assert mail.outbox[0].to == "alice@example.com"
    token = _token_from_mail()

    c = await make_client()
    resp = await c.post("/api/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD})
    assert resp.status_code == 204
    assert (await a.get("/api/auth/me")).status_code == 401
    assert (await b.get("/api/auth/me")).status_code == 401
    assert (await login(c, "alice@example.com", NEW_PASSWORD)).status_code == 200

    # single use
    again = await c.post("/api/auth/password/reset", json={"token": token, "new_password": "another-one-3"})
    assert again.status_code == 410 and error_code(again) == "reset_token_invalid"


async def test_reset_token_expires(client: httpx.AsyncClient, db: AsyncSession) -> None:
    await register(client)
    await client.post("/api/auth/password/forgot", json={"email": "alice@example.com"})
    token = _token_from_mail()
    await db.execute(text("UPDATE password_reset_tokens SET expires_at = now() - interval '1 second'"))
    await db.commit()
    resp = await client.post("/api/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD})
    assert resp.status_code == 410


async def test_new_reset_request_invalidates_previous_token(client: httpx.AsyncClient) -> None:
    await register(client)
    await client.post("/api/auth/password/forgot", json={"email": "alice@example.com"})
    first = _token_from_mail()
    mail.outbox.clear()
    await client.post("/api/auth/password/forgot", json={"email": "alice@example.com"})
    second = _token_from_mail()
    bad = await client.post("/api/auth/password/reset", json={"token": first, "new_password": NEW_PASSWORD})
    assert bad.status_code == 410
    ok = await client.post("/api/auth/password/reset", json={"token": second, "new_password": NEW_PASSWORD})
    assert ok.status_code == 204


async def test_reset_with_unknown_token(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/auth/password/reset", json={"token": "nope", "new_password": NEW_PASSWORD})
    assert resp.status_code == 410


async def test_reset_validates_password_before_consuming_token(client: httpx.AsyncClient) -> None:
    await register(client)
    await client.post("/api/auth/password/forgot", json={"email": "alice@example.com"})
    token = _token_from_mail()
    bad = await client.post("/api/auth/password/reset", json={"token": token, "new_password": "short"})
    assert bad.status_code == 400
    ok = await client.post("/api/auth/password/reset", json={"token": token, "new_password": NEW_PASSWORD})
    assert ok.status_code == 204


async def test_forgot_password_rate_limited_per_email(client: httpx.AsyncClient) -> None:
    for _ in range(3):
        assert (
            await client.post("/api/auth/password/forgot", json={"email": "x@example.com"})
        ).status_code == 202
    resp = await client.post("/api/auth/password/forgot", json={"email": "X@example.com"})
    assert resp.status_code == 429
