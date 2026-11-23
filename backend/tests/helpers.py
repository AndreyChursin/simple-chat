import httpx

PASSWORD = "correct-horse-1"


async def register(
    client: httpx.AsyncClient, username: str = "alice", email: str | None = None, password: str = PASSWORD
) -> dict[str, object]:
    resp = await client.post(
        "/api/auth/register",
        json={"email": email or f"{username}@example.com", "username": username, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["user"]  # type: ignore[no-any-return]


async def login(
    client: httpx.AsyncClient, email: str, password: str = PASSWORD, keep: bool = False
) -> httpx.Response:
    return await client.post(
        "/api/auth/login", json={"email": email, "password": password, "keep_signed_in": keep}
    )


def error_code(resp: httpx.Response) -> str:
    return resp.json()["error"]["code"]  # type: ignore[no-any-return]
